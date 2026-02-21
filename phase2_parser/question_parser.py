"""
Parse Korean bar exam questions and answer keys from extracted HWP text.

Handles:
  - 선택형 (multiple-choice) question files
  - 정답 / 정답가안 (answer key) files

Question format:
  문  1.                   ← question number
  question text?
  ① choice A              ← choices (Unicode ①-⑤, U+2460-U+2464)
  ② choice B
  ...

Answer key format (two styles):
  Style A: <1><4><2><3>...   ← pairs: <question#><answer#>
  Style B: 1  3  / 2  4  /  ← space/tab delimited grid
"""

import re
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
CIRCLE_NUMS = {
    "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5,
    "\u2460": 1, "\u2461": 2, "\u2462": 3, "\u2463": 4, "\u2464": 5,
}
# Regex patterns
_RE_Q_NUM = re.compile(r"문\s{0,4}(\d{1,3})\s*[.]", re.MULTILINE)
_RE_ANGLE_ANS = re.compile(r"<(\d{1,3})><([1-5])>")   # <Q#><Ans>
_RE_CIRCLE = re.compile(r"[①②③④⑤\u2460-\u2464]")

# Subject keyword → canonical name
_SUBJECT_MAP = [
    (["공법", "헌법", "행정법"],        "공법"),
    (["민사법", "민법", "상법"],         "민사법"),
    (["형사법", "형법", "형사소송"],     "형사법"),
    (["법조윤리", "윤리"],               "법조윤리"),
]

# Exam session patterns from filename / text
_RE_SESSION = re.compile(r"제?\s*(\d{1,2})\s*회")
_RE_YEAR    = re.compile(r"(20\d{2})")


# ── Helpers ────────────────────────────────────────────────────────────────
def infer_subject(text: str) -> str:
    for keywords, canonical in _SUBJECT_MAP:
        if any(kw in text for kw in keywords):
            return canonical
    return "기타"


def infer_session(text: str) -> Optional[int]:
    m = _RE_SESSION.search(text)
    return int(m.group(1)) if m else None


def infer_year(text: str) -> Optional[int]:
    m = _RE_YEAR.search(text)
    return int(m.group(1)) if m else None


def _clean(text: str) -> str:
    """Collapse excessive whitespace."""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Answer key parsing ─────────────────────────────────────────────────────
def parse_answer_key(text: str) -> Dict[int, int]:
    """
    Return {question_number: answer_digit} from answer key text.
    Supports:
      <1><4><2><3>...  or  1\\n4\\n2\\n3  (two-column table layout)
    """
    answers: Dict[int, int] = {}

    # Style A: <Q><A> angle bracket pairs
    pairs = _RE_ANGLE_ANS.findall(text)
    if pairs:
        for q_str, a_str in pairs:
            answers[int(q_str)] = int(a_str)
        return answers

    # Style B: Numbers appear as separate lines (table cells)
    # The answer key table has rows: q_num, answer, q_num, answer, ...
    nums = re.findall(r"\b(\d{1,3})\b", text)
    candidates = [int(n) for n in nums if 1 <= int(n) <= 200]
    # Heuristic: pairs of (1-40ish, 1-5) alternating
    i = 0
    while i + 1 < len(candidates):
        q, a = candidates[i], candidates[i + 1]
        if 1 <= q <= 100 and 1 <= a <= 5:
            answers[q] = a
            i += 2
        else:
            i += 1

    return answers


def is_answer_key(filepath: str, text: str) -> bool:
    name = Path(filepath).name.lower()
    if any(kw in name for kw in ["정답", "가안", "답안"]):
        return True
    if any(kw in text[:200] for kw in ["정답가안", "최종정답", "정 답"]):
        return True
    return False


# ── Question parsing ────────────────────────────────────────────────────────
def parse_questions(text: str, meta: dict) -> List[dict]:
    """
    Split text into individual questions.
    Each question dict contains: question_number, question_text, choices.
    """
    text = _clean(text)
    lines = text.splitlines()

    questions: List[dict] = []
    current_q: Optional[dict] = None
    current_section = []   # lines belonging to current question

    def flush():
        if current_q is None:
            return
        body = "\n".join(current_section).strip()
        choices, q_text = _split_choices(body)
        current_q["question_text"] = _clean(q_text)
        current_q["choices"] = choices
        questions.append(current_q)

    for line in lines:
        line = line.strip()
        if not line:
            if current_section:
                current_section.append("")
            continue

        # New question marker
        m = _RE_Q_NUM.match(line)
        if m:
            flush()
            q_num = int(m.group(1))
            current_q = {**meta, "question_number": q_num}
            remainder = line[m.end():].strip()
            current_section = [remainder] if remainder else []
            continue

        if current_q is not None:
            current_section.append(line)

    flush()

    # Filter out empty / malformed
    valid = []
    for q in questions:
        if q.get("question_text") and q.get("choices"):
            valid.append(q)

    return valid


_RE_CIRCLE_CHAR = re.compile(r"[①②③④⑤\u2460-\u2464]")


def _split_choices(body: str) -> Tuple[Dict[str, str], str]:
    """
    Separate choices from question text using circle-number positions.
    Works even when multiple choices appear on the same line (HWP table cells).
    Returns (choices_dict, question_text).
    """
    positions = [
        (m.start(), CIRCLE_NUMS[m.group()])
        for m in _RE_CIRCLE_CHAR.finditer(body)
    ]
    if not positions:
        return {}, body.strip()

    q_text = body[: positions[0][0]].strip()
    choices: Dict[str, str] = {}

    for idx, (pos, num) in enumerate(positions):
        start = pos + 1
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(body)
        choice_text = re.sub(r"\s+", " ", body[start:end]).strip()
        choices[str(num)] = choice_text

    return choices, q_text


# ── Main parser entry point ─────────────────────────────────────────────────
def parse_file(filepath: str, text: str) -> dict:
    """
    Given extracted HWP text, return structured result dict:
      {
        "is_answer_key": bool,
        "answers": {...} or None,
        "questions": [...] or None,
        "meta": {...}
      }
    """
    name = Path(filepath).name
    # Infer metadata from filename + first 300 chars of text
    header = name + " " + text[:300]
    meta = {
        "source_file": name,
        "subject": infer_subject(header),
        "exam_session": infer_session(header),
        "year": infer_year(header),
        "exam_type": "선택형",
    }

    if is_answer_key(filepath, text):
        return {
            "is_answer_key": True,
            "answers": parse_answer_key(text),
            "questions": None,
            "meta": meta,
        }
    else:
        questions = parse_questions(text, meta)
        return {
            "is_answer_key": False,
            "answers": None,
            "questions": questions,
            "meta": meta,
        }


# ── Merge questions + answers ───────────────────────────────────────────────
def merge_answers(questions: List[dict], answer_map: Dict[int, int]) -> List[dict]:
    """Attach the correct answer to each question dict."""
    for q in questions:
        qn = q.get("question_number")
        q["answer"] = answer_map.get(qn) if qn else None
    return questions


def build_database(all_results: List[dict]) -> dict:
    """
    Combine all parsed file results into a single database.
    Groups questions with their answer keys by (subject, exam_session).
    """
    # Separate answer keys and question sets
    answer_keys: Dict[Tuple, Dict[int, int]] = {}
    question_sets: List[List[dict]] = []

    for res in all_results:
        meta = res["meta"]
        key = (meta.get("subject"), meta.get("exam_session"), meta.get("year"))

        if res["is_answer_key"] and res["answers"]:
            existing = answer_keys.get(key, {})
            existing.update(res["answers"])
            answer_keys[key] = existing
        elif res["questions"]:
            question_sets.append((key, res["questions"]))

    # Merge
    all_questions: List[dict] = []
    for key, qs in question_sets:
        ans_map = answer_keys.get(key, {})
        merged = merge_answers(qs, ans_map)
        all_questions.extend(merged)

    # Assign unique IDs
    for i, q in enumerate(all_questions):
        subj = q.get("subject", "기타").replace(" ", "_")
        session = q.get("exam_session") or "?"
        qnum = q.get("question_number") or i
        q["id"] = f"bar_{session}_{subj}_{qnum:03d}"

    return {
        "total": len(all_questions),
        "questions": all_questions,
    }
