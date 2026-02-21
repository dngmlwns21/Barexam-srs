"""
batch_process_exams.py — Phase 2: Bulk Data Transformation via Claude API

Reads  : data/questions_parsed.json
Writes : data/questions_enhanced.json  (enriched, resumable)
Errors : data/batch_errors.json
Check  : data/.batch_checkpoint.json   (auto-resume on crash)

What it adds per question
  • explanation      – Korean 3-5 sentence expert explanation
  • tags             – 2-4 Korean legal topic tags
  • is_outdated      – True if the question cites superseded law
  • needs_revision   – True if the question warrants legal review
  • is_box_type      – True if this is a combination (조합형) question
  • ox_statements    – [{letter, text, is_correct}] for box-type only

Box-type logic (deterministic, no LLM guessing)
  • Detects questions whose choices are letter combos  e.g. "가, 나", "ㄱ, ㄴ, ㄷ"
  • Derives each statement's is_correct from answer + choice text locally
  • Only asks LLM to extract clean statement texts + write the explanation

Usage
  py -3 batch_process_exams.py                    # process all
  py -3 batch_process_exams.py --limit 50         # first 50 only
  py -3 batch_process_exams.py --concurrency 3    # slower, safer rate
  py -3 batch_process_exams.py --dry-run          # preview prompts, no API call

Setup
  pip install anthropic python-dotenv tqdm
  Add ANTHROPIC_API_KEY=sk-ant-... to phase4_api/.env
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# ── Force UTF-8 output on Windows (cp949 terminal can't handle full Unicode) ─
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import anthropic
except ImportError:
    sys.exit("ERROR: run:  pip install anthropic")

try:
    from tqdm import tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT            = Path(__file__).resolve().parent
DATA            = ROOT / "data"
INPUT_FILE      = DATA / "questions_parsed.json"
OUTPUT_FILE     = DATA / "questions_enhanced.json"
CHECKPOINT_FILE = DATA / ".batch_checkpoint.json"
ERRORS_FILE     = DATA / "batch_errors.json"

load_dotenv(ROOT / "phase4_api" / ".env")
load_dotenv(ROOT / ".env")          # fallback

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ── Constants ─────────────────────────────────────────────────────────────────
MODEL       = "claude-haiku-4-5-20251001"  # fast + cheap for bulk work
MAX_TOKENS  = 1200
CONCURRENCY = 5     # simultaneous API calls
RETRY_MAX   = 3
RETRY_DELAY = 2.0   # seconds

# Korean sub-statement letter sets
LETTERS_GA  = list("가나다라마바사아자차카타파하")  # 가-형
LETTERS_BOX = list("ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ")  # ㄱ-형
CIRCLE = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}


# ── Choice parsing ────────────────────────────────────────────────────────────
def parse_choices(raw: Any) -> Dict[int, str]:
    """Return {1: text, 2: text, ...} regardless of dict or list input."""
    if isinstance(raw, dict):
        return {int(k): v for k, v in raw.items()}
    if isinstance(raw, list):
        return {i + 1: v for i, v in enumerate(raw)}
    return {}


# ── Box-type detection ────────────────────────────────────────────────────────
_COMBO_RE_GA  = re.compile(r'^[\s가나다라마바사아자차,，、·]+$')
_COMBO_RE_BOX = re.compile(r'^[\sㄱㄴㄷㄹㅁㅂㅅㅇㅈ,，、·]+$')

def _is_combo_text(text: str) -> Tuple[bool, str]:
    """
    Returns (is_combo, kind) where kind is 'ga' or 'box'.
    A combo text is short and consists only of Korean letters + separators.
    """
    t = text.strip()
    if len(t) > 30 or len(t) < 2:
        return False, ""
    if _COMBO_RE_GA.match(t) and any(l in t for l in LETTERS_GA[:8]):
        return True, "ga"
    if _COMBO_RE_BOX.match(t) and any(l in t for l in LETTERS_BOX[:6]):
        return True, "box"
    return False, ""

def detect_box_type(q: dict) -> Tuple[bool, str]:
    """
    Returns (is_box_type, letter_kind).
    Requires at least 2 out of 4 choices to look like letter combos.
    """
    choices = parse_choices(q.get("choices") or {})
    kinds: List[str] = []
    for text in choices.values():
        ok, kind = _is_combo_text(text)
        if ok:
            kinds.append(kind)
    if len(kinds) >= 2:
        # Determine dominant kind
        return True, "ga" if kinds.count("ga") >= kinds.count("box") else "box"
    return False, ""

def get_correct_letters(
    q: dict, letter_kind: str
) -> Tuple[List[str], List[str]]:
    """
    Returns (correct_letters, all_letters_used_in_choices).
    Correct letters are those that appear in the correct answer combination.
    """
    choices  = parse_choices(q.get("choices") or {})
    answer   = int(q.get("answer") or 1)
    letters  = LETTERS_GA if letter_kind == "ga" else LETTERS_BOX

    # Collect every letter that appears in any choice text (= all sub-statements)
    seen: set = set()
    all_letters: List[str] = []
    for text in choices.values():
        for letter in letters:
            if letter in text and letter not in seen:
                seen.add(letter)
                all_letters.append(letter)

    # Correct letters = those in the winning combination text
    correct_combo = choices.get(answer, "")
    correct = [l for l in letters if l in correct_combo]

    return correct, all_letters


# ── Prompt builders ───────────────────────────────────────────────────────────
def build_mcq_prompt(q: dict) -> str:
    choices      = parse_choices(q.get("choices") or {})
    answer       = int(q.get("answer") or 1)
    choice_lines = "\n".join(
        f"{CIRCLE.get(n, str(n))} {text}"
        for n, text in sorted(choices.items())
    )
    correct_text = choices.get(answer, "")

    return f"""당신은 한국 변호사시험 전문가입니다. 아래 문제를 분석하고 JSON으로만 응답하세요.

[문제]
{q["question_text"]}

[선택지]
{choice_lines}

[정답] {CIRCLE.get(answer, str(answer))} {correct_text}

※ explanation의 핵심 법률 용어(법령명, 법률 개념)는 <mark>용어</mark>로 감싸세요 (2~5개).

아래 JSON만 출력하세요 (마크다운 코드블록 없이):
{{
  "explanation": "정답이 왜 옳고 나머지 선택지가 왜 틀렸는지 3~5문장으로 한국어 설명. 핵심 법령·법률 개념어는 <mark>개념어</mark>로 감싸세요.",
  "tags": ["관련법령또는법률개념1", "관련법령또는법률개념2"],
  "is_outdated": false,
  "needs_revision": false
}}"""


def build_box_prompt(
    q: dict,
    correct_letters: List[str],
    all_letters: List[str],
    letter_kind: str,
) -> str:
    choices       = parse_choices(q.get("choices") or {})
    answer        = int(q.get("answer") or 1)
    correct_combo = choices.get(answer, "")
    wrong_letters = [l for l in all_letters if l not in correct_letters]

    choice_lines = "\n".join(
        f"{CIRCLE.get(n, str(n))} {text}"
        for n, text in sorted(choices.items())
    )

    # Build statement schema for the LLM output
    stmt_entries = ",\n    ".join(
        f'{{"letter": "{l}", "text": "소문항 {l}의 원문 전체", '
        f'"is_correct": {str(l in correct_letters).lower()}}}'
        for l in all_letters
    )

    return f"""당신은 한국 변호사시험 전문가입니다. 이 문제는 조합형(박스형) 문제입니다. JSON으로만 응답하세요.

[문제]
{q["question_text"]}

[조합 선택지]
{choice_lines}

[정답] {CIRCLE.get(answer, str(answer))}번 → 정답 조합: "{correct_combo}"

※ 아래 판별은 정답 조합에서 확정된 것입니다 (LLM이 변경하지 말 것):
- 옳은 소문항: {', '.join(correct_letters) if correct_letters else '없음'}
- 틀린 소문항: {', '.join(wrong_letters) if wrong_letters else '없음'}

지시사항:
1. 문제 원문에서 각 소문항({', '.join(all_letters)})의 텍스트를 그대로 추출하세요.
2. 왜 이 조합이 정답인지 3~5문장으로 한국어 설명을 작성하세요.
3. 관련 법령/개념 태그 2~4개를 제시하세요.
4. explanation에서 핵심 법률 용어(법령명, 법률 개념)는 <mark>용어</mark>로 감싸세요 (2~5개).

아래 JSON만 출력하세요 (마크다운 코드블록 없이):
{{
  "explanation": "왜 이 조합이 정답인지 각 소문항 핵심을 포함하여 설명. 핵심 법률 용어는 <mark>용어</mark>로.",
  "ox_statements": [
    {stmt_entries}
  ],
  "tags": ["관련법령1", "관련법령2"],
  "is_outdated": false,
  "needs_revision": false
}}"""


# ── Claude API call with retry ─────────────────────────────────────────────────
async def call_claude(
    client: "anthropic.AsyncAnthropic",
    prompt: str,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """Call Claude under semaphore with up to RETRY_MAX attempts. Returns parsed JSON."""
    async with semaphore:
        last_err: Optional[Exception] = None
        raw = ""
        for attempt in range(RETRY_MAX):
            try:
                message = await client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = message.content[0].text.strip()

                # Strip markdown code fence if model wraps in ```json ... ```
                if raw.startswith("```"):
                    raw = re.sub(r"^```(?:json)?\s*", "", raw)
                    raw = re.sub(r"\s*```\s*$", "", raw)

                return json.loads(raw)

            except json.JSONDecodeError as e:
                last_err = e
                if attempt < RETRY_MAX - 1:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    raise ValueError(
                        f"JSON parse failed after {RETRY_MAX} attempts: {e}\n"
                        f"Raw response (first 300 chars): {raw[:300]}"
                    )

            except Exception as e:
                last_err = e
                cls = type(e).__name__
                # Rate limit: wait longer
                if "RateLimit" in cls or "rate_limit" in str(e).lower():
                    wait = RETRY_DELAY * (2 ** attempt)
                    await asyncio.sleep(wait)
                elif attempt < RETRY_MAX - 1:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    raise

        if last_err:
            raise last_err
        return None


# ── Enrich one question ───────────────────────────────────────────────────────
async def process_question(
    q: dict,
    client: "anthropic.AsyncAnthropic",
    semaphore: asyncio.Semaphore,
) -> dict:
    """Return the question dict enriched with LLM-generated fields."""
    result = dict(q)  # shallow copy preserves all original fields

    is_box, letter_kind = detect_box_type(q)
    result["is_box_type"] = is_box

    if is_box:
        correct_letters, all_letters = get_correct_letters(q, letter_kind)
        prompt = build_box_prompt(q, correct_letters, all_letters, letter_kind)
    else:
        correct_letters, all_letters = [], []
        prompt = build_mcq_prompt(q)

    llm: Optional[dict] = await call_claude(client, prompt, semaphore)

    if llm:
        result["explanation"]    = llm.get("explanation")
        result["tags"]           = llm.get("tags") or []
        result["is_outdated"]    = bool(llm.get("is_outdated", False))
        result["needs_revision"] = bool(llm.get("needs_revision", False))

        if is_box:
            raw_stmts = llm.get("ox_statements") or []
            # Always use our deterministic is_correct — never trust LLM for this
            validated: List[dict] = []
            for stmt in raw_stmts:
                letter = stmt.get("letter", "")
                validated.append({
                    "letter":     letter,
                    "text":       (stmt.get("text") or "").strip(),
                    "is_correct": letter in correct_letters,
                })
            result["ox_statements"] = validated
        else:
            result["ox_statements"] = []
    else:
        result["explanation"]    = None
        result["tags"]           = []
        result["is_outdated"]    = False
        result["needs_revision"] = False
        result["ox_statements"]  = []

    return result


# ── Checkpoint helpers ────────────────────────────────────────────────────────
def load_checkpoint() -> Dict[str, dict]:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text("utf-8"))
        except Exception:
            return {}
    return {}

def save_checkpoint(done: Dict[str, dict]) -> None:
    CHECKPOINT_FILE.write_text(
        json.dumps(done, ensure_ascii=False), "utf-8"
    )


# ── Progress bar (tqdm or plain) ──────────────────────────────────────────────
class _PlainProgress:
    def __init__(self, total: int, desc: str = ""):
        self.total = total
        self.n     = 0
        self._desc = desc
        self._t0   = time.time()

    def update(self, n: int = 1) -> None:
        self.n += n
        elapsed = time.time() - self._t0
        rate    = self.n / elapsed if elapsed else 0
        eta     = (self.total - self.n) / rate if rate else 0
        pct     = 100 * self.n / self.total if self.total else 0
        print(
            f"\r{self._desc}: {self.n}/{self.total} "
            f"({pct:.0f}%) | {rate:.1f} q/s | ETA {eta:.0f}s   ",
            end="", flush=True,
        )

    def set_postfix_str(self, s: str) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        print()


def make_pbar(total: int, desc: str = ""):
    if _HAS_TQDM:
        return tqdm(total=total, desc=desc, unit="q", dynamic_ncols=True)
    return _PlainProgress(total=total, desc=desc)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main(concurrency: int, limit: Optional[int], dry_run: bool) -> None:
    if not dry_run and not ANTHROPIC_API_KEY:
        sys.exit(
            "ERROR: ANTHROPIC_API_KEY not found.\n"
            "Add it to phase4_api/.env:\n"
            "  ANTHROPIC_API_KEY=sk-ant-api03-..."
        )

    # ── Load input ────────────────────────────────────────────────────────────
    if not INPUT_FILE.exists():
        sys.exit(f"ERROR: {INPUT_FILE} not found")

    print(f"Loading {INPUT_FILE} …")
    raw_data   = json.loads(INPUT_FILE.read_text("utf-8"))
    questions  = raw_data["questions"]
    if limit:
        questions = questions[:limit]
        print(f"  --limit {limit}: processing first {limit} questions only")
    print(f"  Total in input: {len(questions)}")

    # ── Load already-processed results ───────────────────────────────────────
    done_map: Dict[str, dict] = {}

    if OUTPUT_FILE.exists():
        existing = json.loads(OUTPUT_FILE.read_text("utf-8"))
        for q in existing.get("questions", []):
            done_map[q["id"]] = q
        print(f"  Loaded {len(done_map)} from existing output file")

    checkpoint = load_checkpoint()
    for qid, qdata in checkpoint.items():
        if qid not in done_map:
            done_map[qid] = qdata
    print(f"  After checkpoint merge: {len(done_map)} done")

    to_process = [q for q in questions if q["id"] not in done_map]
    print(f"  Remaining: {len(to_process)}")

    if not to_process:
        print("All questions already processed.")
        _write_output(done_map)
        return

    # ── Dry run preview ───────────────────────────────────────────────────────
    if dry_run:
        _dry_run_preview(to_process)
        return

    # ── Process ───────────────────────────────────────────────────────────────
    client    = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    semaphore = asyncio.Semaphore(concurrency)
    errors: List[dict] = []
    BATCH_SAVE = 50  # checkpoint every N questions

    async def _run(q: dict, pbar) -> Optional[dict]:
        try:
            result = await process_question(q, client, semaphore)
            pbar.update(1)
            pbar.set_postfix_str(f"ok:{q['id'][-14:]}")
            return result
        except Exception as e:
            pbar.update(1)
            errors.append({"id": q["id"], "error": str(e)})
            pbar.set_postfix_str(f"ERR:{q['id'][-10:]}")
            return None

    with make_pbar(len(to_process), "Enriching") as pbar:
        for batch_start in range(0, len(to_process), BATCH_SAVE):
            batch   = to_process[batch_start : batch_start + BATCH_SAVE]
            tasks   = [_run(q, pbar) for q in batch]
            results = await asyncio.gather(*tasks)

            for r in results:
                if r is not None:
                    done_map[r["id"]] = r

            save_checkpoint(done_map)

    # ── Write output ──────────────────────────────────────────────────────────
    _write_output(done_map)

    if errors:
        ERRORS_FILE.write_text(
            json.dumps(errors, ensure_ascii=False, indent=2), "utf-8"
        )
        print(f"\nErrors ({len(errors)}) saved → {ERRORS_FILE}")
        print("Re-run the script to retry failed questions automatically.")

    print(f"\nDone. {len(done_map)} questions total.")
    print(f"Next step: run  py -3 seed_from_enhanced.py")


def _write_output(done_map: Dict[str, dict]) -> None:
    output = {
        "total":     len(done_map),
        "questions": list(done_map.values()),
    }
    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), "utf-8"
    )
    print(f"\nOutput saved → {OUTPUT_FILE}")


def _dry_run_preview(to_process: List[dict]) -> None:
    """Show detection stats and sample prompts without calling the API."""
    box_qs  = [(q, detect_box_type(q)) for q in to_process]
    n_box   = sum(1 for _, (b, _) in box_qs if b)
    n_mcq   = len(to_process) - n_box

    print(f"\n[DRY RUN] Would process {len(to_process)} questions")
    print(f"  MCQ (regular 5-choice): {n_mcq}")
    print(f"  Box-type (combination): {n_box}")

    # Show one MCQ sample
    mcq_sample = next((q for q, (b, _) in box_qs if not b), None)
    if mcq_sample:
        print("\n--- Sample MCQ prompt (truncated) ---")
        prompt_text = build_mcq_prompt(mcq_sample)[:500]
        print(prompt_text.encode("utf-8", errors="replace").decode("utf-8"))
        print("...")

    # Show one box-type sample
    box_sample = next(
        ((q, lk) for q, (b, lk) in box_qs if b), None
    )
    if box_sample:
        q, lk = box_sample
        cl, al = get_correct_letters(q, lk)
        print("\n--- Sample Box-type prompt (truncated) ---")
        prompt_text = build_box_prompt(q, cl, al, lk)[:700]
        print(prompt_text.encode("utf-8", errors="replace").decode("utf-8"))
        print("...")
        print(f"\n  all_letters:     {al}")
        print(f"  correct_letters: {cl}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Batch-enrich Korean Bar Exam questions via Claude API"
    )
    parser.add_argument(
        "--concurrency", type=int, default=CONCURRENCY,
        help=f"Parallel API calls (default {CONCURRENCY})"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only first N questions (for testing)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview box-type detection + sample prompts without calling API"
    )
    args = parser.parse_args()

    asyncio.run(main(args.concurrency, args.limit, args.dry_run))
