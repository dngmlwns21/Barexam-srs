"""
pipeline.py — Main orchestrator for the Korean Bar Exam data pipeline.
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
from typing import List

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")

# Updated imports to reflect new structure
from .crawler        import AklsCrawler
from .db_writer      import SRSWriter
from .llm_processor  import MCQTransformer  # Changed from llm_transformer
from .models         import RawQuestion, TransformedQuestion
from .pdf_extractor  import scan_directory

log = logging.getLogger(__name__)

def _save_json(data, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, list) and data and hasattr(data[0], "model_dump"):
        out = [d.model_dump(mode="json") for d in data]
    else:
        out = data
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

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
            log.error("DATABASE_URL not set")
            return
        async with SRSWriter(db_url) as writer:
            await writer.write_all(transformed, wipe=args.wipe)

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser(prog="python -m data_pipeline.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_mock = sub.add_parser("mock")
    p_mock.add_argument("--idx-min", type=int, default=1)
    p_mock.add_argument("--idx-max", type=int, default=200)
    p_mock.add_argument("--wipe", action="store_true")
    p_mock.add_argument("--concurrency", type=int, default=8)
    p_mock.add_argument("--save-raw", default=None)
    p_mock.add_argument("--output", default=None)
    p_mock.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "mock":
        asyncio.run(run_mock(args))

if __name__ == "__main__":
    main()
