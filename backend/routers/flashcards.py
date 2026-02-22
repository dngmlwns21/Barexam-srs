from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import queries
from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, UserProgress
from ..schemas import DueCardOut
from ..sm2 import parse_steps
from ..utils import build_due_card_out, up_load_opts

router = APIRouter()


@router.get("/due", response_model=List[DueCardOut])
async def get_due_cards(
    limit:        int                  = Query(20, ge=1, le=100),
    subject_id:   Optional[uuid.UUID] = Query(None),
    starred_only: bool                = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.vacation_mode_enabled:
        return []

    now         = datetime.now(timezone.utc)
    today       = date.today()
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

    new_today    = await queries.count_new_today(db, current_user.id, today_start)
    review_today = await queries.count_review_today(db, current_user.id, today_start)

    new_remaining    = max(0, (current_user.daily_new_limit    or 20)  - new_today)
    review_remaining = max(0, (current_user.daily_review_limit or 200) - review_today)

    base   = [UserProgress.user_id == current_user.id]
    s_filt = []
    if starred_only:
        base.append(UserProgress.is_starred == True)  # noqa: E712
    if subject_id:
        s_filt.append(Question.subject_id == subject_id)

    opts = up_load_opts()
    result_rows: list = []

    # ── Priority 1: learning / lapsed cards due now ───────────────────────────
    stmt1 = (
        select(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question,  Question.id  == Flashcard.question_id)
        .options(*opts)
        .where(
            *base, *s_filt,
            UserProgress.card_state.in_(["learning", "lapsed"]),
            UserProgress.learning_due_at <= now,
        )
        .order_by(UserProgress.learning_due_at.asc())
        .limit(limit)
    )
    r1 = await db.execute(stmt1)
    result_rows.extend(r1.scalars().all())

    # ── Priority 2: review cards due (daily limit) ────────────────────────────
    if len(result_rows) < limit and review_remaining > 0:
        rev_lim = min(limit - len(result_rows), review_remaining)
        stmt2 = (
            select(UserProgress)
            .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
            .join(Question,  Question.id  == Flashcard.question_id)
            .options(*opts)
            .where(
                *base, *s_filt,
                UserProgress.card_state == "review",
                UserProgress.next_review_at <= now,
            )
            .order_by(UserProgress.next_review_at.asc())
            .limit(rev_lim)
        )
        r2 = await db.execute(stmt2)
        result_rows.extend(r2.scalars().all())

    # ── Priority 3: new cards (daily limit) ───────────────────────────────────
    if len(result_rows) < limit and new_remaining > 0:
        new_lim = min(limit - len(result_rows), new_remaining)
        stmt3 = (
            select(UserProgress)
            .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
            .join(Question,  Question.id  == Flashcard.question_id)
            .options(*opts)
            .where(
                *base, *s_filt,
                UserProgress.card_state == "new",
            )
            .order_by(UserProgress.created_at.asc())
            .limit(new_lim)
        )
        r3 = await db.execute(stmt3)
        result_rows.extend(r3.scalars().all())

    return [build_due_card_out(up) for up in result_rows[:limit]]


@router.get("/{flashcard_id}", response_model=DueCardOut)
async def get_flashcard(
    flashcard_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Return a single flashcard by ID regardless of due status (for on-demand re-study)."""
    stmt = (
        select(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .options(*up_load_opts())
        .where(
            UserProgress.user_id      == current_user.id,
            UserProgress.flashcard_id == flashcard_id,
        )
    )
    result = await db.execute(stmt)
    up = result.scalar_one_or_none()
    if not up:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flashcard not found")
    return build_due_card_out(up)
