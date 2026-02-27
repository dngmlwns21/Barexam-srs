"""mock_cards.py — Serve OX flashcard data from the database.

Endpoints:
    GET /api/v1/mock/decks          — per-subject deck stats
    GET /api/v1/mock/cards          — flat list of OX statements (filterable by subject)
    GET /api/v1/mock/mock-test      — random OX cards for a mock test
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..dependencies import get_db
from ..models import Choice, Question
from ..schemas import ChoiceOut, QuestionCardOut

router = APIRouter()

# choice_number ≥ 101 means OX card (가=101 나=102 다=103 라=104 마=105 …)
_OX_MIN_NUM = 101
_IDX_TO_LETTER = {101 + i: l for i, l in enumerate("가나다라마바사아자차카타파하")}


def _letter(choice_number: int) -> str:
    return _IDX_TO_LETTER.get(choice_number, str(choice_number))


# ── Schemas ───────────────────────────────────────────────────────────────────

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
    legal_basis: Optional[str]
    case_citation: Optional[str]
    explanation_core: Optional[str]
    keywords: Optional[List[str]]
    theory: Optional[str]
    is_revised: bool
    revision_note: Optional[str]
    importance: str
    explanation: str
    is_outdated: bool


class MockTestCardOut(BaseModel):
    question: QuestionCardOut
    choice: ChoiceOut


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/decks", response_model=List[DeckOut])
async def get_mock_decks(db: AsyncSession = Depends(get_db)):
    """Return per-subject OX card counts from the database."""
    sql = text("""
        SELECT s.name AS subject, COUNT(c.id) AS total
        FROM choices c
        JOIN questions q ON q.id = c.question_id
        JOIN subjects s ON s.id = q.subject_id
        WHERE c.choice_number >= :min_num
        GROUP BY s.name
        ORDER BY s.name
    """)
    rows = (await db.execute(sql, {"min_num": _OX_MIN_NUM})).fetchall()
    return [
        DeckOut(
            subject=r.subject,
            new_count=r.total,
            learning_count=0,
            review_count=0,
            total=r.total,
        )
        for r in rows
    ]


@router.get("/cards", response_model=List[OXCardOut])
async def get_mock_cards(
    subject: Optional[str] = Query(None, description="Filter by subject name"),
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """Return flat list of OX statements from the database."""
    sql = text("""
        SELECT
            q.id          AS question_id,
            s.name        AS subject,
            q.source_year AS year,
            q.exam_type   AS source,
            q.question_number,
            q.stem,
            q.overall_explanation,
            q.is_outdated,
            c.choice_number,
            c.content     AS statement,
            c.is_correct,
            c.legal_basis,
            c.case_citation,
            c.explanation_core,
            c.keywords,
            c.explanation
        FROM choices c
        JOIN questions q ON q.id = c.question_id
        JOIN subjects  s ON s.id = q.subject_id
        WHERE c.choice_number >= :min_num
          AND (:subject IS NULL OR s.name = :subject)
        ORDER BY q.source_year, q.question_number, c.choice_number
        LIMIT :limit
    """)
    rows = (await db.execute(sql, {
        "min_num": _OX_MIN_NUM,
        "subject": subject,
        "limit": limit,
    })).fetchall()

    result = []
    for r in rows:
        kw = r.keywords
        if isinstance(kw, str):
            try:
                kw = json.loads(kw)
            except Exception:
                kw = []
        elif kw is None:
            kw = []

        result.append(OXCardOut(
            raw_id=str(r.question_id),
            subject=r.subject,
            year=r.year,
            source=r.source or "",
            question_number=r.question_number or 0,
            stem=r.stem or "",
            overall_explanation=r.overall_explanation,
            letter=_letter(r.choice_number),
            choice_number=r.choice_number,
            statement=r.statement,
            is_correct=r.is_correct,
            legal_basis=r.legal_basis,
            case_citation=r.case_citation,
            explanation_core=r.explanation_core,
            keywords=kw,
            theory=None,
            is_revised=False,
            revision_note=None,
            importance="B",
            explanation=r.explanation or "",
            is_outdated=bool(r.is_outdated),
        ))
    return result


@router.get("/mock-test", response_model=List[MockTestCardOut])
async def get_mock_test(
    subject_id: Optional[str] = None,
    num_cards: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Return random OX cards for a mock test."""
    q = (
        select(Choice)
        .options(joinedload(Choice.question).joinedload(Question.subject))
        .where(Choice.choice_number >= _OX_MIN_NUM)
        .order_by(func.random())
        .limit(num_cards)
    )
    if subject_id:
        q = q.join(Question).filter(Question.subject_id == subject_id)

    result = await db.execute(q)
    choices = result.scalars().unique().all()
    return [
        MockTestCardOut(question=c.question, choice=c)
        for c in choices
    ]
