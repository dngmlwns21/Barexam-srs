"""mock_cards.py — Serve transformed JSON flashcards for frontend testing.

Endpoints:
    GET /api/v1/mock/decks          — deck list with new/learning/review counts
    GET /api/v1/mock/cards          — flat list of OX statements (filterable by subject)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Choice, Question
from ..schemas import ChoiceOut, QuestionCardOut

router = APIRouter()

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _load_transformed() -> List[Dict[str, Any]]:
    """Load the most recently created transformed_*.json file."""
    files = sorted(DATA_DIR.glob("transformed_*.json"), reverse=True)
    if not files:
        return []
    return json.loads(files[0].read_text(encoding="utf-8"))


# ── Schemas ──────────────────────────────────────────────────────────────────

class DeckOut(BaseModel):
    subject: str
    new_count: int
    learning_count: int
    review_count: int
    total: int


class OXCardOut(BaseModel):
    raw_id: str
    subject: str
    year: Optional[int]
    source: str
    question_number: int
    stem: str
    overall_explanation: Optional[str]
    letter: str
    choice_number: int
    statement: str
    is_correct: bool
    legal_basis: Optional[str] # Changed from legal_provision
    case_citation: Optional[str] # Changed from precedent
    explanation_core: Optional[str] # New field
    keywords: Optional[List[str]] # New field
    theory: Optional[str]
    is_revised: bool
    revision_note: Optional[str]
    importance: str
    explanation: str
    is_outdated: bool


class MockTestCardOut(BaseModel):
    question: QuestionCardOut
    choice: ChoiceOut


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/decks", response_model=List[DeckOut])
async def get_mock_decks():
    """Return per-subject deck stats.  All cards are 'new' (no SRS history in mock)."""
    questions = _load_transformed()
    counts: Dict[str, int] = {}
    for q in questions:
        subj = q.get("subject") or "기타"
        counts[subj] = counts.get(subj, 0) + len(q.get("ox_statements", []))
    return [
        DeckOut(
            subject=subj,
            new_count=n,
            learning_count=0,
            review_count=0,
            total=n,
        )
        for subj, n in sorted(counts.items())
    ]


@router.get("/cards", response_model=List[OXCardOut])
async def get_mock_cards(
    subject: Optional[str] = Query(None, description="Filter by subject name"),
    limit: int = Query(200, ge=1, le=1000),
):
    """Return flat list of OX statements, optionally filtered by subject."""
    questions = _load_transformed()
    flat: List[OXCardOut] = []
    for q in questions:
        if subject and q.get("subject") != subject:
            continue
        for stmt in q.get("ox_statements", []):
            flat.append(
                OXCardOut(
                    raw_id=q["raw_id"],
                    subject=q.get("subject") or "기타",
                    year=q.get("year"),
                    source=q.get("source", ""),
                    question_number=q["question_number"],
                    stem=q.get("stem", ""),
                    overall_explanation=q.get("overall_explanation"),
                    letter=stmt["letter"],
                    choice_number=stmt["choice_number"],
                    statement=stmt["statement"],
                    is_correct=stmt["is_correct"],
                    legal_basis=stmt.get("legal_basis"), # Changed from legal_provision
                    case_citation=stmt.get("case_citation"), # Changed from precedent
                    explanation_core=stmt.get("explanation_core"), # New field
                    keywords=stmt.get("keywords", []), # New field
                    theory=stmt.get("theory"),
                    is_revised=bool(stmt.get("is_revised", False)),
                    revision_note=stmt.get("revision_note"),
                    importance=stmt.get("importance", "B"),
                    explanation=stmt["explanation"],
                    is_outdated=bool(q.get("is_outdated", False)),
                )
            )
    return flat[:limit]


@router.get("/mock-test", response_model=list[MockTestCardOut])
async def get_mock_test(
    subject_id: int | None = None,
    num_cards: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    지정된 수의 무작위 OX 카드로 모의고사를 생성합니다.
    OX 카드는 문제와 그에 속한 선택지 하나가 조합된 형태입니다.
    """
    query = db.query(Choice).options(joinedload(Choice.question).joinedload(Question.subject))

    if subject_id:
        query = query.join(Question).filter(Question.subject_id == subject_id)

    # 데이터베이스에서 효율적으로 무작위 선택지를 가져옵니다.
    random_choices = query.order_by(func.random()).limit(num_cards).all()

    if not random_choices:
        return []

    # MockTestCardOut 객체들을 구성합니다.
    mock_test_cards = [
        MockTestCardOut(question=choice.question, choice=choice) for choice in random_choices
    ]

    return mock_test_cards
