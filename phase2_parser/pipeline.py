"""
Phase 2 Pipeline: Parse HWP exam files → generate AI explanations → save JSON.

Usage:
  # Step 1: Extract & parse (no API key needed)
  py -3 pipeline.py --parse

  # Step 2: Submit explanation batch (needs ANTHROPIC_API_KEY)
  py -3 pipeline.py --explain

  # Step 3: Collect batch results (run after batch completes)
  py -3 pipeline.py --collect --batch-id <BATCH_ID>

  # Or run all steps sequentially:
  py -3 pipeline.py --all
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = ROOT / "downloads"
OUTPUT_DIR    = ROOT / "data"
PARSED_JSON   = OUTPUT_DIR / "questions_parsed.json"
FINAL_JSON    = OUTPUT_DIR / "questions_final.json"
BATCH_ID_FILE = OUTPUT_DIR / "batch_id.txt"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extractor import collect_hwp_files, extract_hwp_text
from question_parser import parse_file, build_database
import explainer as exp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Step 1: Parse ──────────────────────────────────────────────────────────
def run_parse():
    OUTPUT_DIR.mkdir(exist_ok=True)
    log.info("Collecting HWP files from %s …", DOWNLOADS_DIR)
    hwp_files = collect_hwp_files(DOWNLOADS_DIR)
    log.info("Found %d HWP files.", len(hwp_files))

    all_results = []
    for i, fp in enumerate(hwp_files):
        log.info("[%d/%d] %s", i + 1, len(hwp_files), fp.name[:60])
        text = extract_hwp_text(str(fp))
        if not text.strip():
            log.warning("  Empty text, skipping.")
            continue
        result = parse_file(str(fp), text)
        if result["is_answer_key"]:
            log.info("  → Answer key: %d answers", len(result["answers"]))
        else:
            n = len(result["questions"] or [])
            log.info("  → Questions: %d", n)
        all_results.append(result)

    db = build_database(all_results)
    log.info("Total questions parsed: %d", db["total"])

    # Stats
    with_answers = sum(1 for q in db["questions"] if q.get("answer"))
    log.info("Questions with answers: %d / %d", with_answers, db["total"])

    PARSED_JSON.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Saved → %s", PARSED_JSON)
    return db


# ── Step 2: Submit explanation batch ──────────────────────────────────────
def run_explain(limit: int = 0):
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: Set ANTHROPIC_API_KEY environment variable.")

    if not PARSED_JSON.exists():
        sys.exit("ERROR: Run --parse first.")

    db = json.loads(PARSED_JSON.read_text(encoding="utf-8"))
    questions = db["questions"]

    # Only process questions that have answers and no explanation yet
    to_explain = [
        q for q in questions
        if q.get("answer") and not q.get("explanation") and q.get("question_text") and q.get("choices")
    ]
    if limit:
        to_explain = to_explain[:limit]

    log.info("Submitting %d questions for AI explanation…", len(to_explain))
    client = anthropic.Anthropic(api_key=api_key)
    batch_id = exp.submit_batch(to_explain, client)

    BATCH_ID_FILE.write_text(batch_id, encoding="utf-8")
    log.info("Batch ID saved to %s", BATCH_ID_FILE)
    log.info("Run: py -3 pipeline.py --collect --batch-id %s", batch_id)
    return batch_id


# ── Step 3: Collect results ────────────────────────────────────────────────
def run_collect(batch_id: str):
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: Set ANTHROPIC_API_KEY environment variable.")

    if not PARSED_JSON.exists():
        sys.exit("ERROR: Run --parse first.")

    db = json.loads(PARSED_JSON.read_text(encoding="utf-8"))
    questions = db["questions"]

    client = anthropic.Anthropic(api_key=api_key)
    exp.poll_batch(batch_id, client, poll_interval=30)
    count = exp.collect_results(batch_id, questions, client)

    db["total"] = len(questions)
    db["explanations_generated"] = count

    FINAL_JSON.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Final database saved → %s  (%d explanations)", FINAL_JSON, count)


# ── Quick single-question test ─────────────────────────────────────────────
def run_test_single():
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: Set ANTHROPIC_API_KEY environment variable.")

    if not PARSED_JSON.exists():
        sys.exit("ERROR: Run --parse first.")

    db = json.loads(PARSED_JSON.read_text(encoding="utf-8"))
    candidates = [q for q in db["questions"] if q.get("answer") and q.get("choices")]
    if not candidates:
        sys.exit("No questions with answers found.")

    q = candidates[0]
    log.info("Testing on: %s  Q%s", q.get("subject"), q.get("question_number"))

    client = anthropic.Anthropic(api_key=api_key)
    explanation = exp.explain_single(q, client)
    print("\n" + "=" * 60)
    print(explanation)
    print("=" * 60)


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Phase 2 Pipeline")
    parser.add_argument("--parse",    action="store_true", help="Extract & parse HWP files")
    parser.add_argument("--explain",  action="store_true", help="Submit Batches API job")
    parser.add_argument("--collect",  action="store_true", help="Collect batch results")
    parser.add_argument("--test",     action="store_true", help="Test single-question explanation")
    parser.add_argument("--all",      action="store_true", help="Run parse + explain + collect")
    parser.add_argument("--batch-id", type=str,            help="Batch ID for --collect")
    parser.add_argument("--limit",    type=int, default=0, help="Max questions to explain (0=all)")
    args = parser.parse_args()

    if args.parse or args.all:
        run_parse()

    if args.explain or args.all:
        batch_id = run_explain(limit=args.limit)
        if args.all:
            run_collect(batch_id)

    if args.collect:
        bid = args.batch_id or (BATCH_ID_FILE.read_text().strip() if BATCH_ID_FILE.exists() else None)
        if not bid:
            sys.exit("ERROR: Provide --batch-id or run --explain first.")
        run_collect(bid)

    if args.test:
        run_test_single()

    if not any([args.parse, args.explain, args.collect, args.test, args.all]):
        parser.print_help()


if __name__ == "__main__":
    main()
