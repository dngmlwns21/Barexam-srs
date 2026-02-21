from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, UserProgress
from ..schemas import ChoiceOut, DueCardOut, QuestionOut, SM2StateOut

router = APIRouter()


@router.get("/due", response_model=List[DueCardOut])
async def get_due_cards(
    limit:        int             = Query(20, ge=1, le=100),
    subject_id:   Optional[uuid.UUID] = Query(None),
    starred_only: bool            = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    now = datetime.now(timezone.utc)

    # If vacation mode is on, return nothing (SM-2 frozen)
    if current_user.vacation_mode_enabled:
        return []

    stmt = (
        select(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question, Question.id == Flashcard.question_id)
        .options(
            selectinload(UserProgress.flashcard)
            .selectinload(Flashcard.question)
            .selectinload(Question.choices),
            selectinload(UserProgress.flashcard)
            .selectinload(Flashcard.choice),
        )
        .where(
            UserProgress.user_id == current_user.id,
            UserProgress.next_review_at <= now,
        )
    )

    if starred_only:
        stmt = stmt.where(UserProgress.is_starred == True)
    if subject_id:
        stmt = stmt.where(Question.subject_id == subject_id)

    stmt = stmt.order_by(UserProgress.next_review_at.asc()).limit(limit)
    result = await db.execute(stmt)
    progress_rows = result.scalars().all()

    out = []
    for up in progress_rows:
        fc = up.flashcard
        q  = fc.question

        q_out = QuestionOut.model_validate(q)
        choice_out = ChoiceOut.model_validate(fc.choice) if fc.choice else None

        sm2_out = SM2StateOut(
            ease_factor=float(up.ease_factor),
            interval_days=float(up.interval_days),
            repetitions=up.repetitions,
            next_review_at=up.next_review_at,
            last_reviewed_at=up.last_reviewed_at,
            last_rating=up.last_rating,
        )

        out.append(
            DueCardOut(
                flashcard_id=fc.id,
                type=fc.type,
                question=q_out,
                choice=choice_out,
                sm2=sm2_out,
                personal_note=up.personal_note,
                is_starred=up.is_starred,
            )
        )

    return out
