"""
pipeline.py — Main orchestrator for the Korean Bar Exam data pipeline.

Full pipeline:
  [PDF/TXT/JSON] ──► pdf_extractor  ──► [raw JSON]
  [akls.kr]      ──► crawler        ──► [raw JSON]
                                         │
                                         ▼
                                    llm_transformer  (Claude API)
                                         │
                                         ▼
                                    db_writer  ──► PostgreSQL

Usage examples:

  # Full pipeline: bar exam PDFs → DB
  py -3 -m data_pipeline.pipeline bar \
      --input-dir data/exams/ --wipe

  # Full pipeline: mock exam crawl → DB
  py -3 -m data_pipeline.pipeline mock \
      --idx-min 50 --idx-max 120

  # Transform already-crawled raw JSON → DB (skip extraction)
  py -3 -m data_pipeline.pipeline transform \
      --input data/mock_raw.json

  # Write already-transformed JSON → DB (skip LLM)
  py -3 -m data_pipeline.pipeline write \
      --input data/transformed.json [--wipe]

  # Dry-run: transform only, save JSON, do NOT write DB
  py -3 -m data_pipeline.pipeline transform \
      --input data/bar_raw.json --dry-run --output data/out.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "phase4_api" / ".env")

from .crawler        import AklsCrawler
from .db_writer      import SRSWriter
from .llm_transformer import MCQTransformer
from .models         import RawQuestion, TransformedQuestion
from .pdf_extractor  import scan_directory, load_existing_json

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _save_json(data, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, list) and data and hasattr(data[0], "model_dump"):
        out = [d.model_dump(mode="json") for d in data]
    else:
        out = data
    path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Saved → %s (%d items)", path, len(out))


def _load_raw(path: Path) -> List[RawQuestion]:
    """Load raw questions — handles both RawQuestion JSON and legacy questions_enhanced.json."""
    from .pdf_extractor import load_existing_json
    try:
        # Try loading via the legacy-aware adapter first
        return load_existing_json(path)
    except Exception:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("questions", [])
        return [RawQuestion.model_validate(q) for q in items]


def _load_transformed(path: Path) -> List[TransformedQuestion]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("questions", [])
    return [TransformedQuestion.model_validate(q) for q in items]


# ── Sub-commands ──────────────────────────────────────────────────────────────

async def run_bar(args: argparse.Namespace) -> None:
    """Extract from local PDF/text files → LLM → DB."""
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        sys.exit(f"Input directory not found: {input_dir}")

    # 1. Extract
    log.info("=== STEP 1: Extract from %s ===", input_dir)
    raw_questions = scan_directory(input_dir)

    if args.limit:
        raw_questions = raw_questions[: args.limit]

    if args.save_raw:
        raw_path = Path(args.save_raw)
        _save_json(raw_questions, raw_path)

    await _transform_and_write(raw_questions, args)


async def run_mock(args: argparse.Namespace) -> None:
    """Crawl akls.kr → LLM → DB."""
    log.info("=== STEP 1: Crawl akls.kr (idx %d–%d) ===", args.idx_min, args.idx_max)
    idx_list = list(range(args.idx_min, args.idx_max + 1))
    async with AklsCrawler(
        concurrency=args.concurrency,
        idx_range=(args.idx_min, args.idx_max),
    ) as crawler:
        # Pass idx_override to skip board list scraping when range is explicit
        raw_questions = await crawler.crawl_all(idx_override=idx_list)

    if args.limit:
        raw_questions = raw_questions[: args.limit]

    if args.save_raw:
        _save_json(raw_questions, Path(args.save_raw))

    await _transform_and_write(raw_questions, args)


async def run_transform(args: argparse.Namespace) -> None:
    """Load raw JSON → LLM transform → optionally write DB."""
    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"File not found: {input_path}")

    log.info("=== Loading raw questions from %s ===", input_path)
    raw_questions = _load_raw(input_path)

    if args.limit:
        raw_questions = raw_questions[: args.limit]

    await _transform_and_write(raw_questions, args)


async def run_write(args: argparse.Namespace) -> None:
    """Load already-transformed JSON → write DB."""
    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"File not found: {input_path}")

    log.info("=== Loading transformed questions from %s ===", input_path)
    transformed = _load_transformed(input_path)

    if args.limit:
        transformed = transformed[: args.limit]

    await _write_to_db(transformed, args)


# ── Shared transform + write ──────────────────────────────────────────────────

async def _transform_and_write(
    raw_questions: List[RawQuestion],
    args: argparse.Namespace,
) -> None:
    log.info("=== STEP 2: LLM Transform (%d questions) ===", len(raw_questions))

    checkpoint_dir = Path("data")
    checkpoint_dir.mkdir(exist_ok=True)
    checkpoint = checkpoint_dir / f"checkpoint_{_stamp()}.json"

    transformer  = MCQTransformer(concurrency=args.concurrency)
    transformed  = await transformer.transform_batch(
        raw_questions,
        checkpoint_path=checkpoint,
    )

    output_path = Path(getattr(args, "output", None) or f"data/transformed_{_stamp()}.json")
    _save_json(transformed, output_path)

    if args.dry_run:
        log.info("--dry-run: skipping DB write")
        return

    await _write_to_db(transformed, args)


async def _write_to_db(
    transformed: List[TransformedQuestion],
    args: argparse.Namespace,
) -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL not set in environment")

    log.info("=== STEP 3: Write to DB (%d questions) ===", len(transformed))

    async with SRSWriter(db_url) as writer:
        stats = await writer.write_all(transformed, wipe=getattr(args, "wipe", False))

    print("\n── DB Write Result ──────────────────────────")
    for k, v in stats.items():
        print(f"  {k}: {v}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--limit",       type=int, default=None,
                   help="Process at most N questions")
    p.add_argument("--concurrency", type=int, default=8,
                   help="LLM parallel calls (default: 8)")
    p.add_argument("--dry-run",     action="store_true",
                   help="Run LLM but skip DB write")
    p.add_argument("--save-raw",    default=None,
                   help="Save extracted raw questions to this JSON path")
    p.add_argument("--output",      default=None,
                   help="Save transformed questions to this JSON path")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        prog="python -m data_pipeline.pipeline",
        description="Korean Bar Exam SRS data pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── bar: extract from local files ────────────────────────────────────────
    p_bar = sub.add_parser("bar", help="Extract from local PDF/text files")
    p_bar.add_argument("--input-dir", default="data/exams", type=Path,
                       help="Directory containing exam PDFs/TXTs")
    p_bar.add_argument("--wipe", action="store_true",
                       help="Truncate all DB tables before writing")
    _add_common(p_bar)

    # ── mock: crawl akls.kr ───────────────────────────────────────────────────
    p_mock = sub.add_parser("mock", help="Crawl akls.kr mock exams")
    p_mock.add_argument("--idx-min", type=int, default=1)
    p_mock.add_argument("--idx-max", type=int, default=200)
    p_mock.add_argument("--wipe", action="store_true")
    _add_common(p_mock)

    # ── transform: raw JSON → LLM → DB ───────────────────────────────────────
    p_tr = sub.add_parser("transform", help="Load raw JSON → LLM → DB")
    p_tr.add_argument("--input", required=True, help="Input raw JSON file")
    p_tr.add_argument("--wipe", action="store_true")
    _add_common(p_tr)

    # ── write: transformed JSON → DB ─────────────────────────────────────────
    p_wr = sub.add_parser("write", help="Load transformed JSON → DB only (skip LLM)")
    p_wr.add_argument("--input", required=True, help="Input transformed JSON file")
    p_wr.add_argument("--wipe", action="store_true")
    p_wr.add_argument("--limit", type=int, default=None)

    args = parser.parse_args()

    dispatch = {
        "bar":       run_bar,
        "mock":      run_mock,
        "transform": run_transform,
        "write":     run_write,
    }
    asyncio.run(dispatch[args.command](args))


if __name__ == "__main__":
    main()
