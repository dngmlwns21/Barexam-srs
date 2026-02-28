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


OX_LETTERS = ["가", "나", "다", "라", "마"]

# Alias → canonical subject name (used by crawler and pdf_extractor)
SUBJECT_ALIASES: Dict[str, str] = {
    "헌법":      "헌법",
    "민법":      "민법",
    "민사소송법": "민사소송법",
    "민소":      "민사소송법",
    "형법":      "형법",
    "형사소송법": "형사소송법",
    "형소":      "형사소송법",
    "상법":      "상법",
    "행정법":    "행정법",
    "국제법":    "국제법",
    "공법":      "헌법",
}


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

    class Config:
        populate_by_name = True


# ── LLM output models ─────────────────────────────────────────────────────────

class OXStatement(BaseModel):
    """One O/X flashcard generated from a single MCQ choice."""
    letter:           str                    # 가/나/다/라/마
    choice_number:    int                    # 1–5 (original MCQ choice)
    statement:        str                    # Standalone O/X proposition
    is_correct:       bool                   # True=O (correct), False=X (wrong)

    # ── Textbook-style structured explanation (UNION OX format) ──────────────
    # conclusion: "O" | "X" | "O, X" — rendered as square badge
    conclusion:          str             = ""
    # core_reasoning: one direct sentence stating the bottom-line legal principle
    core_reasoning:      Optional[str]   = None
    # detailed_explanation: step-by-step; complex precedents use ①②③; **bold** key terms
    detailed_explanation: Optional[str] = None
    # citation: formatted legal basis at the end, e.g. "(민법 제104조)" or "(대법원 ...)"
    citation:            Optional[str]   = None

    # ── Legacy / enrichment fields ────────────────────────────────────────────
    legal_basis:      Optional[str] = None   # 관련 조문
    case_citation:    Optional[str] = None   # 판례
    explanation_core: Optional[str] = None   # 핵심 해설 (maps to core_reasoning in DB)
    keywords:         List[str]     = Field(default_factory=list)

    theory:           Optional[str] = None   # 학설
    is_revised:       bool          = False  # 최근 개정/판례 변경 여부
    revision_note:    Optional[str] = None   # 개정 내용
    importance:       ImportanceGrade        # A/B/C
    explanation:      str                    # Detailed explanation (maps to detailed_explanation)


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
