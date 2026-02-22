"""
pdf_extractor.py — Extract MCQ questions from Korean bar exam PDF/text files.

Supports:
  • pdfplumber for PDF extraction
  • Plain text (.txt) files
  • questions_parsed.json (existing format from batch_process_exams.py)

File naming conventions expected:
  bar_01_civil.pdf          → 1st bar exam
  변시_제10회_민사법.pdf     → 10th bar exam, civil law
  2024년_9월_모의고사.pdf   → mock exam

Usage:
    python -m data_pipeline.pdf_extractor --input-dir data/exams/ --out data/bar_raw.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .models import OX_LETTERS, RawQuestion, Source, SUBJECT_ALIASES

log = logging.getLogger(__name__)

try:
    import pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False
    log.warning("pdfplumber not installed — PDF extraction disabled. pip install pdfplumber")

# ── Korean circled number → int ───────────────────────────────────────────────
CIRCLED     = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
CIRCLED_STR = "①②③④⑤"
CIRCLED_RE  = re.compile(f"[{CIRCLED_STR}]")

# ── Question patterns ─────────────────────────────────────────────────────────
# "문 1.", "문1.", "1.", "[1]", "제1문" at line start
Q_NUM_RE = re.compile(
    r"(?m)^[ \t]*(?:문\s*)?(?:제\s*)?(\d{1,2})\s*[.\]]\s*"
)

# Subject section header patterns
SECTION_RE = re.compile(
    r"(?m)^[ \t]*[<\[【]?\s*("
    + "|".join(SUBJECT_ALIASES.keys())
    + r")\s*[>\]】]?\s*$",
    re.MULTILINE,
)

# Answer key patterns: "1. ①" or "1-①" or "01 ①"
ANSWER_RE = re.compile(r"(\d{1,2})\s*[-.\s]\s*([①②③④⑤])")

# Session number from filename: "제10회", "10회", "bar_10"
SESSION_RE = re.compile(r"(?:제\s*)?(\d{1,2})\s*회|bar_(\d{1,2})")

# Year / month from filename or text
YEAR_RE  = re.compile(r"(20\d{2}|19\d{2})")
MONTH_RE = re.compile(r"(\d{1,2})\s*월")

# 변호사시험 session → year mapping
BAR_SESSION_YEAR = {
    1: 2012, 2: 2013, 3: 2014, 4: 2015, 5: 2016,
    6: 2017, 7: 2018, 8: 2019, 9: 2020, 10: 2021,
    11: 2022, 12: 2023, 13: 2024, 14: 2025,
}


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_text_from_pdf(path: Path) -> str:
    """Extract all text from a PDF file using pdfplumber."""
    if not HAS_PDF:
        raise RuntimeError("pdfplumber not installed")
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3)
            if t:
                pages.append(t)
    return "\n".join(pages)


def extract_text_from_file(path: Path) -> str:
    """Load text from .txt file."""
    for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


# ── Metadata from filename ────────────────────────────────────────────────────

def _meta_from_filename(path: Path) -> Dict:
    name = path.stem
    meta: Dict = {}

    # Source
    if any(k in name for k in ("변시", "bar", "변호사시험", "사법시험")):
        meta["source"] = Source.BAR_EXAM
    elif any(k in name for k in ("모의", "mock", "법전협")):
        meta["source"] = Source.MOCK_EXAM
    else:
        meta["source"] = Source.BAR_EXAM  # default

    # Session number
    sm = SESSION_RE.search(name)
    if sm:
        session = int(sm.group(1) or sm.group(2))
        meta["exam_session"] = session
        meta["year"] = BAR_SESSION_YEAR.get(session)

    # Year / month
    ym = YEAR_RE.search(name)
    if ym and "year" not in meta:
        meta["year"] = int(ym.group(1))
    mm = MONTH_RE.search(name)
    if mm:
        meta["month"] = int(mm.group(1))

    # Subject from filename
    for alias, canonical in SUBJECT_ALIASES.items():
        if alias in name:
            meta["subject"] = canonical
            break

    return meta


# ── Answer key extraction ─────────────────────────────────────────────────────

def extract_answer_key(text: str) -> Dict[int, int]:
    """Return {question_number: correct_choice} from answer key section."""
    # Find answer key block (정답, 답, answer, etc.)
    key_section = text
    key_match = re.search(
        r"(?:정\s*답|답\s*안|answer\s*key|정\s*답\s*표)[^\n]*\n(.*)",
        text, re.IGNORECASE | re.DOTALL
    )
    if key_match:
        key_section = key_match.group(1)[:3000]

    pairs = ANSWER_RE.findall(key_section)
    return {int(q): CIRCLED[a] for q, a in pairs}


# ── Question parsing ──────────────────────────────────────────────────────────

def _split_into_question_blocks(text: str) -> List[Tuple[int, str]]:
    """
    Split text into (question_number, body_text) pairs.
    Returns list ordered by question number.
    """
    # Find all question number positions
    matches = list(Q_NUM_RE.finditer(text))
    if not matches:
        return []

    blocks: List[Tuple[int, str]] = []
    for i, m in enumerate(matches):
        q_num = int(m.group(1))
        if q_num < 1 or q_num > 99:
            continue
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body  = text[start:end].strip()
        blocks.append((q_num, body))

    return blocks


def _parse_choices(body: str) -> Tuple[str, Dict[int, str]]:
    """
    Split body into (stem, {1: choice1, ..., 5: choice5}).
    """
    circ_pos = CIRCLED_RE.search(body)
    if not circ_pos:
        return body.strip(), {}

    stem        = body[: circ_pos.start()].strip()
    choice_text = body[circ_pos.start():]

    parts   = re.split(r"([①②③④⑤])", choice_text)
    choices: Dict[int, str] = {}
    cur_num: Optional[int]  = None

    for part in parts:
        if part in CIRCLED:
            cur_num = CIRCLED[part]
        elif cur_num is not None and part.strip():
            choices[cur_num] = part.strip()

    return stem, choices


def parse_questions(
    text: str,
    meta: Dict,
    source_file: str,
) -> List[RawQuestion]:
    """
    Parse raw Korean exam text into a list of RawQuestion objects.
    `meta` must contain at minimum: source, subject.
    """
    source        = meta.get("source", Source.BAR_EXAM)
    subject       = meta.get("subject", "기타")
    year          = meta.get("year")
    month         = meta.get("month")
    exam_session  = meta.get("exam_session")

    answer_key = extract_answer_key(text)

    # If the file has a section header mid-text, parse sections separately
    section_matches = list(SECTION_RE.finditer(text))

    if section_matches:
        # Multi-subject file: split at section headers
        segments: List[Tuple[str, str]] = []
        for i, sm in enumerate(section_matches):
            sec_subj = SUBJECT_ALIASES.get(sm.group(1), sm.group(1))
            sec_start = sm.end()
            sec_end   = section_matches[i + 1].start() if i + 1 < len(section_matches) else len(text)
            segments.append((sec_subj, text[sec_start:sec_end]))
    else:
        segments = [(subject, text)]

    questions: List[RawQuestion] = []

    for seg_subj, seg_text in segments:
        blocks = _split_into_question_blocks(seg_text)
        for q_num, body in blocks:
            stem, choices = _parse_choices(body)
            if not stem or len(choices) < 2:
                continue

            correct = answer_key.get(q_num, 1)

            raw_id = (
                f"bar_{exam_session or year or 'X'}"
                f"_{seg_subj}_{q_num:03d}"
            )
            questions.append(
                RawQuestion(
                    source=source,
                    raw_id=raw_id,
                    exam_session=exam_session,
                    year=year,
                    month=month,
                    subject=seg_subj,
                    question_number=q_num,
                    stem=stem,
                    choices=choices,
                    correct_choice=correct,
                    tags=[seg_subj],
                    source_file=source_file,
                )
            )

    return questions


# ── File loader ───────────────────────────────────────────────────────────────

def load_existing_json(path: Path) -> List[RawQuestion]:
    """Load from questions_parsed.json or questions_enhanced.json format."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("questions", data) if isinstance(data, dict) else data

    questions: List[RawQuestion] = []
    for q in items:
        source = (
            Source.MOCK_EXAM if q.get("source") in ("법전협", "mock")
            else Source.BAR_EXAM
        )
        choices_raw = q.get("choices") or {}
        if isinstance(choices_raw, list):
            choices = {i + 1: v for i, v in enumerate(choices_raw)}
        else:
            choices = {int(k): v for k, v in choices_raw.items()}

        questions.append(
            RawQuestion(
                source=source,
                raw_id=q.get("id", ""),
                exam_session=q.get("exam_session"),
                year=q.get("year"),
                month=q.get("month"),
                subject=q.get("subject", ""),
                question_number=int(q.get("question_number", 0)),
                stem=q.get("question_text") or q.get("stem", ""),
                choices=choices,
                correct_choice=int(q.get("answer") or q.get("correct_choice") or 1),
                tags=q.get("tags", []),
                is_outdated=q.get("is_outdated", False),
                needs_revision=q.get("needs_revision", False),
                source_file=q.get("source_file"),
            )
        )
    return questions


# ── Directory scanner ─────────────────────────────────────────────────────────

def scan_directory(input_dir: Path) -> List[RawQuestion]:
    """Scan a directory and extract questions from all supported files."""
    all_questions: List[RawQuestion] = []
    exts = {".pdf", ".txt", ".json"}

    for path in sorted(input_dir.rglob("*")):
        if path.suffix.lower() not in exts:
            continue

        log.info("Processing: %s", path.name)

        try:
            if path.suffix.lower() == ".json":
                qs = load_existing_json(path)
                log.info("  JSON → %d questions", len(qs))
                all_questions.extend(qs)
                continue

            if path.suffix.lower() == ".pdf":
                if not HAS_PDF:
                    log.warning("  Skipping PDF (pdfplumber not installed)")
                    continue
                text = extract_text_from_pdf(path)
            else:
                text = extract_text_from_file(path)

            meta = _meta_from_filename(path)
            qs   = parse_questions(text, meta, str(path.name))
            log.info("  → %d questions (subject: %s)", len(qs), meta.get("subject", "?"))
            all_questions.extend(qs)

        except Exception as exc:
            log.error("  FAILED: %s — %s", path.name, exc)

    log.info("Total extracted: %d questions from %s", len(all_questions), input_dir)
    return all_questions


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Extract MCQ questions from exam files")
    parser.add_argument("--input-dir", default="data/exams", type=Path)
    parser.add_argument("--out",       default="data/bar_raw.json")
    args = parser.parse_args()

    questions = scan_directory(args.input_dir)

    out = [q.model_dump(mode="json") for q in questions]
    Path(args.out).write_text(
        json.dumps({"questions": out}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved {len(questions)} questions → {args.out}")


if __name__ == "__main__":
    main()
