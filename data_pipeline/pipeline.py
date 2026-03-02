"""
pipeline.py — Main orchestrator for the Korean Bar Exam data pipeline.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Set

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")

from .crawler        import AklsCrawler
from .db_writer      import SRSWriter
from .llm_processor  import MCQTransformer
from .models         import RawQuestion, Source, TransformedQuestion
from .pdf_extractor  import scan_directory

log = logging.getLogger(__name__)

# Same deterministic namespace as db_writer.py
_NS = uuid.UUID("12345678-1234-5678-1234-567812345678")

def _q_uuid(raw_id: str) -> str:
    return str(uuid.uuid5(_NS, raw_id))

def _save_json(data, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, list) and data and hasattr(data[0], "model_dump"):
        out = [d.model_dump(mode="json") for d in data]
    else:
        out = data
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


# ── seed: write existing transformed JSON → DB ────────────────────────────────

async def run_seed(args: argparse.Namespace) -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL not set"); return

    input_file = Path(args.input)
    if not input_file.exists():
        log.error("File not found: %s", input_file); return

    raw_list = json.loads(input_file.read_text(encoding="utf-8"))
    if isinstance(raw_list, dict):
        raw_list = raw_list.get("questions", [])

    # Deduplicate by raw_id (keep last occurrence)
    seen: dict = {}
    for q in raw_list:
        seen[q["raw_id"]] = q
    deduped = list(seen.values())
    log.info("Loaded %d entries → %d unique after dedup", len(raw_list), len(deduped))

    questions = [TransformedQuestion(**q) for q in deduped]

    async with SRSWriter(db_url) as writer:
        stats = await writer.write_all(questions, wipe=args.wipe)
    log.info("Seed complete: %s", stats)


# ── from_json: read questions_parsed.json → LLM → DB (real-time) ─────────────

def _load_raw_questions(data_file: Path) -> List[RawQuestion]:
    data = json.loads(data_file.read_text(encoding="utf-8"))
    raw_qs = data.get("questions", data) if isinstance(data, dict) else data

    result: List[RawQuestion] = []
    for q in raw_qs:
        raw_id = q.get("id", "")
        source = Source.BAR_EXAM if raw_id.startswith("bar") else Source.MOCK_EXAM
        choices_raw = q.get("choices", {})
        if isinstance(choices_raw, dict):
            choices = {int(k): v for k, v in choices_raw.items()}
        else:
            choices = {i + 1: v for i, v in enumerate(choices_raw)}
        result.append(RawQuestion(
            source=source,
            raw_id=raw_id,
            exam_session=q.get("exam_session"),
            year=q.get("year"),
            subject=q.get("subject", "기타"),
            question_number=int(q.get("question_number") or 0),
            stem=q.get("question_text", ""),
            choices=choices,
            correct_choice=int(q.get("answer") or 1),
            tags=[q.get("subject", "기타")],
            is_outdated=False,
            needs_revision=False,
        ))
    return result


async def _fetch_processed_ids(db_url: str) -> Set[str]:
    """Return set of raw_ids already present in the questions table."""
    conn_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(conn_url)
    try:
        rows = await conn.fetch("SELECT id FROM questions")
        return {str(r["id"]) for r in rows}
    finally:
        await conn.close()


async def run_from_json(args: argparse.Namespace) -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL not set"); return

    data_file = ROOT / "data" / args.input
    if not data_file.exists():
        log.error("File not found: %s", data_file); return

    log.info("=== STEP 1: Load questions from %s ===", data_file.name)
    raw_questions = _load_raw_questions(data_file)
    log.info("Loaded %d raw questions", len(raw_questions))

    log.info("=== STEP 2: Check DB for already-processed questions ===")
    if getattr(args, "wipe", False):
        conn_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(conn_url)
        try:
            await conn.execute("TRUNCATE TABLE user_progress, review_logs, study_sessions, flashcards, choices, question_tags, questions, subjects CASCADE")
            log.info("DB wiped (--wipe flag set)")
        finally:
            await conn.close()
        to_process = raw_questions
        log.info("All %d questions will be (re)processed", len(to_process))
    else:
        db_q_ids = await _fetch_processed_ids(db_url)
        skip_ids = {rq.raw_id for rq in raw_questions if _q_uuid(rq.raw_id) in db_q_ids}
        to_process = [rq for rq in raw_questions if rq.raw_id not in skip_ids]
        log.info("Already in DB: %d  |  Remaining: %d", len(skip_ids), len(to_process))

    if args.limit:
        to_process = to_process[:args.limit]
        log.info("Limited to %d questions", len(to_process))

    if not to_process:
        log.info("Nothing to process."); return

    # Checkpoint: load existing results to resume
    checkpoint_path = ROOT / "data" / f"checkpoint_{datetime.now():%Y%m%d_%H%M%S}.json"
    all_transformed: List[TransformedQuestion] = []

    transformer = MCQTransformer(concurrency=args.concurrency)
    batch_size = args.batch_size
    total_batches = (len(to_process) + batch_size - 1) // batch_size

    for i in range(0, len(to_process), batch_size):
        batch = to_process[i:i + batch_size]
        batch_num = i // batch_size + 1
        log.info("=== Batch %d/%d (%d questions) ===", batch_num, total_batches, len(batch))

        transformed = await transformer.transform_batch(batch)
        all_transformed.extend(transformed)

        # Save checkpoint after every batch
        _save_json(all_transformed, checkpoint_path)
        log.info("  Checkpoint: %s  (%d total)", checkpoint_path.name, len(all_transformed))

        # Write this batch to DB immediately (동시 진행)
        if not args.dry_run and transformed:
            async with SRSWriter(db_url) as writer:
                stats = await writer.write_all(transformed)
            log.info("  DB +%d questions, +%d flashcards", stats["questions"], stats["flashcards"])

    if args.output:
        _save_json(all_transformed, Path(args.output))

    log.info("Pipeline complete. Total transformed: %d", len(all_transformed))


# ── mock: crawl akls.kr → LLM → DB ──────────────────────────────────────────

async def run_mock(args: argparse.Namespace) -> None:
    log.info("=== STEP 1: Crawl akls.kr (idx %d–%d) ===", args.idx_min, args.idx_max)
    async with AklsCrawler(concurrency=args.concurrency, idx_range=(args.idx_min, args.idx_max)) as crawler:
        raw_questions = await crawler.crawl_all(idx_override=list(range(args.idx_min, args.idx_max + 1)))

    if args.save_raw:
        _save_json(raw_questions, Path(args.save_raw))

    log.info("=== STEP 2: LLM Transform with RAG ===")
    transformer = MCQTransformer(concurrency=args.concurrency)
    transformed = await transformer.transform_batch(raw_questions)

    if args.output:
        _save_json(transformed, Path(args.output))

    if not args.dry_run:
        log.info("=== STEP 3: Write to DB ===")
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            log.error("DATABASE_URL not set"); return
        async with SRSWriter(db_url) as writer:
            await writer.write_all(transformed, wipe=args.wipe)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser(prog="python -m data_pipeline.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    # seed: write existing transformed JSON to DB
    p_seed = sub.add_parser("seed", help="Write existing transformed JSON to DB")
    p_seed.add_argument("--input", default="data/transformed_20260222_221343.json",
                        help="Path to transformed JSON file")
    p_seed.add_argument("--wipe", action="store_true", help="Truncate tables before insert")

    # from_json: process questions_parsed.json → LLM → DB
    p_json = sub.add_parser("from_json", help="Process questions_parsed.json via LLM → DB")
    p_json.add_argument("--input", default="questions_parsed.json",
                        help="Filename inside data/ directory")
    p_json.add_argument("--limit", type=int, default=0,
                        help="Max questions to process (0 = all)")
    p_json.add_argument("--batch-size", type=int, default=10,
                        help="Questions per batch (default: 10)")
    p_json.add_argument("--concurrency", type=int, default=1)
    p_json.add_argument("--output", default=None, help="Save final results to JSON")
    p_json.add_argument("--dry-run", action="store_true", help="Skip DB writes")
    p_json.add_argument("--wipe", action="store_true", help="Truncate all tables before processing")

    # mock: crawl akls.kr → LLM → DB
    p_mock = sub.add_parser("mock", help="Crawl akls.kr → LLM → DB")
    p_mock.add_argument("--idx-min", type=int, default=1)
    p_mock.add_argument("--idx-max", type=int, default=200)
    p_mock.add_argument("--wipe", action="store_true")
    p_mock.add_argument("--concurrency", type=int, default=8)
    p_mock.add_argument("--save-raw", default=None)
    p_mock.add_argument("--output", default=None)
    p_mock.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "seed":
        asyncio.run(run_seed(args))
    elif args.command == "from_json":
        asyncio.run(run_from_json(args))
    elif args.command == "mock":
        asyncio.run(run_mock(args))


if __name__ == "__main__":
    main()
