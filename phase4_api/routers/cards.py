from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Float, case, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, User, UserProgress
from ..schemas import DueCardOut
from ..utils import build_due_card_out, up_load_opts

router = APIRouter()


@router.get("/quick-scan", response_model=List[DueCardOut])
async def quick_scan(
    mode: str = Query(
        "failure",
        description="Sort/filter strategy: 'failure' (highest failure rate), "
                    "'newest' (most recently added to DB), 'favorites' (starred only)",
        pattern="^(failure|newest|favorites)$",
    ),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch cards for rapid pre-exam review WITHOUT triggering SRS grading.

    The frontend should display these as a fast slideshow:
    show question → tap/click → reveal answer + explanation.
    Do NOT call POST /reviews/{flashcard_id} — this endpoint is read-only.

    Modes:
    - failure:   cards with the highest peer failure rate come first
    - newest:    most recently seeded/added questions come first
    - favorites: only starred cards, ordered by most-recently starred
    """
    opts = up_load_opts()

    stmt = (
        select(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question, Question.id == Flashcard.question_id)
        .options(*opts)
        .where(UserProgress.user_id == current_user.id)
    )

    if mode == "failure":
        # Failure rate = (total - correct) / total; 0 attempts → 0 rate
        failure_rate = case(
            (
                Question.total_attempts > 0,
                cast(Question.total_attempts - Question.correct_attempts, Float)
                / cast(Question.total_attempts, Float),
            ),
            else_=0.0,
        )
        stmt = stmt.order_by(failure_rate.desc())

    elif mode == "newest":
        stmt = stmt.order_by(Question.created_at.desc())

    elif mode == "favorites":
        stmt = stmt.where(UserProgress.is_starred == True).order_by(
            UserProgress.updated_at.desc()
        )

    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [build_due_card_out(up) for up in result.scalars().all()]
