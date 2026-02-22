"""Shared Pydantic models for the Korean Bar Exam data pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────────────

class Source(str, Enum):
    BAR_EXAM  = "변시"    # 변호사시험 (1st–14th)
    MOCK_EXAM = "법전협"  # 법학전문대학원협의회 모의시험


class ImportanceGrade(str, Enum):
    A = "A"  # 핵심: 반복 출제, 필수 암기
    B = "B"  # 표준: 정기 출제
    C = "C"  # 주변: 가끔 출제


# Subject canonical names (matches subjects table)
SUBJECT_ALIASES: Dict[str, str] = {
    "민법":       "민법",
    "민사법":     "민법",
    "민사소송법": "민사소송법",
    "민사집행법": "민사소송법",
    "가족법":     "가족법",
    "친족상속법": "가족법",
    "상법":       "상법",
    "형법":       "형법",
    "형사소송법": "형사소송법",
    "형사특별법": "형사특별법",
    "행정법":     "행정법",
    "헌법":       "헌법",
    "법조윤리":   "법조윤리",
    # Electives
    "국제법":     "국제법",
    "국제사법":   "국제사법",
    "노동법":     "노동법",
    "조세법":     "조세법",
    "지식재산권법": "지식재산권법",
    "경제법":     "경제법",
    "환경법":     "환경법",
}

# Korean bar exam subject range: (first_q, last_q) per subject block
# 변호사시험 공통 배점표 (1교시/2교시)
BAR_EXAM_SUBJECT_MAP: List[tuple] = [
    # (start, end, subject)
    (1,  40,  "헌법"),
    (41, 80,  "민법"),
    (81, 120, "형법"),
]

# Letters for OX statements
OX_LETTERS = ["가", "나", "다", "라", "마"]


# ── Raw question (pre-LLM) ────────────────────────────────────────────────────

class RawQuestion(BaseModel):
    """Parsed MCQ question before LLM transformation."""
    source:         Source
    raw_id:         str                       # e.g. "bar_10_민법_001"
    exam_session:   Optional[int]  = None     # 변호사시험 회차
    year:           Optional[int]  = None
    month:          Optional[int]  = None     # 법전협 모의시험 월
    subject:        str
    question_number: int
    stem:           str
    choices:        Dict[int, str]            # {1: "...", ..., 5: "..."}
    correct_choice: int                       # 1–5
    tags:           List[str]      = Field(default_factory=list)
    is_outdated:    bool           = False
    needs_revision: bool           = False
    source_file:    Optional[str]  = None


# ── LLM output models ─────────────────────────────────────────────────────────

class OXStatement(BaseModel):
    """One O/X flashcard generated from a single MCQ choice."""
    letter:           str                    # 가/나/다/라/마
    choice_number:    int                    # 1–5 (original MCQ choice)
    statement:        str                    # Standalone O/X proposition
    is_correct:       bool                   # True=O (correct), False=X (wrong)
    legal_provision:  Optional[str] = None   # 관련 조문 e.g. "민법 제390조"
    precedent:        Optional[str] = None   # 판례 e.g. "대법원 2022.12.29. 선고 2022다1234 판결"
    theory:           Optional[str] = None   # 학설
    is_revised:       bool          = False  # 최근 개정/판례 변경 여부
    revision_note:    Optional[str] = None   # 개정 내용
    importance:       ImportanceGrade        # A/B/C
    explanation:      str                    # Instructor-level explanation


class TransformedQuestion(BaseModel):
    """Fully processed question ready for DB insertion."""
    source:           Source
    raw_id:           str
    exam_session:     Optional[int]  = None
    year:             Optional[int]  = None
    month:            Optional[int]  = None
    subject:          str
    question_number:  int
    stem:             str
    choices:          Dict[int, str]
    correct_choice:   int
    tags:             List[str]      = Field(default_factory=list)
    is_outdated:      bool           = False
    needs_revision:   bool           = False
    overall_explanation: Optional[str] = None
    ox_statements:    List[OXStatement]
