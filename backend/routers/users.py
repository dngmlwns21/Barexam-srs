from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, StudySession, User, UserProgress
from ..schemas import (
    DueCardOut,
    HeatmapEntry,
    StreakOut,
    StudySettingsIn,
    UserOut,
    UserUpdateIn,
    VacationIn,
)
from ..utils import build_due_card_out, up_load_opts

router = APIRouter()


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me", response_model=UserOut)
async def update_me(
    body: UserUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.display_name is not None:
        current_user.display_name = body.display_name
    if body.sm2_hard_interval_minutes is not None:
        current_user.sm2_hard_interval_minutes = body.sm2_hard_interval_minutes
    if body.sm2_good_interval_days is not None:
        current_user.sm2_good_interval_days = body.sm2_good_interval_days
    if body.sm2_easy_interval_days is not None:
        current_user.sm2_easy_interval_days = body.sm2_easy_interval_days

    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.put("/me/vacation", response_model=UserOut)
async def set_vacation(
    body: VacationIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)

    if body.enabled and not current_user.vacation_mode_enabled:
        current_user.vacation_mode_enabled = True
        current_user.vacation_started_at = now

    elif not body.enabled and current_user.vacation_mode_enabled:
        # Shift all pending SM-2 due dates forward by vacation duration
        if current_user.vacation_started_at:
            vs = current_user.vacation_started_at
            if vs.tzinfo is None:
                vs = vs.replace(tzinfo=timezone.utc)
            elapsed = now - vs

            from sqlalchemy import update
            from ..models import UserProgress, Flashcard
            # Shift next_review_at for all non-overdue cards
            result = await db.execute(
                select(UserProgress).where(UserProgress.user_id == current_user.id)
            )
            for up in result.scalars().all():
                if up.next_review_at and up.next_review_at.tzinfo is None:
                    up.next_review_at = up.next_review_at.replace(tzinfo=timezone.utc)
                if up.next_review_at and up.next_review_at > now:
                    up.next_review_at = up.next_review_at + elapsed

        current_user.vacation_mode_enabled = False
        current_user.vacation_started_at = None

    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.put("/me/study-settings", response_model=UserOut)
async def update_study_settings(
    body: StudySettingsIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.daily_new_limit    is not None: current_user.daily_new_limit    = body.daily_new_limit
    if body.daily_review_limit is not None: current_user.daily_review_limit = body.daily_review_limit
    if body.target_retention   is not None: current_user.target_retention   = body.target_retention
    if body.learning_steps     is not None: current_user.learning_steps     = body.learning_steps.strip()
    if body.relearning_steps   is not None: current_user.relearning_steps   = body.relearning_steps.strip()
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/me/sync", response_model=UserOut)
async def sync(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.last_synced_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.get("/me/streak", response_model=StreakOut)
async def get_streak(current_user: User = Depends(get_current_user)):
    return StreakOut(
        study_streak=current_user.study_streak,
        longest_streak=current_user.longest_streak,
        last_studied_date=current_user.last_studied_date,
    )


@router.post("/me/init-progress", status_code=200)
async def init_progress(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create missing user_progress rows for all flashcards. Safe to call multiple times."""
    fc_result = await db.execute(select(Flashcard.id))
    all_fc_ids = fc_result.scalars().all()

    existing = await db.execute(
        select(UserProgress.flashcard_id).where(UserProgress.user_id == current_user.id)
    )
    existing_ids = set(existing.scalars().all())

    created = 0
    for fc_id in all_fc_ids:
        if fc_id not in existing_ids:
            db.add(UserProgress(user_id=current_user.id, flashcard_id=fc_id))
            created += 1

    await db.commit()
    return {"created": created, "total_flashcards": len(all_fc_ids)}


@router.get("/me/heatmap", response_model=List[HeatmapEntry])
async def get_heatmap(
    from_date: Optional[date] = Query(None),
    to_date:   Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not from_date:
        from_date = date.today() - timedelta(days=365)
    if not to_date:
        to_date = date.today()

    result = await db.execute(
        select(StudySession)
        .where(
            StudySession.user_id == current_user.id,
            StudySession.session_date >= from_date,
            StudySession.session_date <= to_date,
        )
        .order_by(StudySession.session_date)
    )
    sessions = result.scalars().all()
    return [
        HeatmapEntry(
            date=s.session_date,
            cards_reviewed=s.cards_reviewed,
            correct_count=s.correct_count,
        )
        for s in sessions
    ]


@router.get("/me/favorites", response_model=List[DueCardOut])
async def get_favorites(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all flashcards the user has starred."""
    stmt = (
        select(UserProgress)
        .options(*up_load_opts())
        .where(
            UserProgress.user_id == current_user.id,
            UserProgress.is_starred == True,
        )
        .order_by(UserProgress.updated_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [build_due_card_out(up) for up in result.scalars().all()]


@router.get("/me/notes", response_model=List[DueCardOut])
async def get_notes(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all flashcards where the user has written a personal note."""
    stmt = (
        select(UserProgress)
        .options(*up_load_opts())
        .where(
            UserProgress.user_id == current_user.id,
            UserProgress.personal_note.isnot(None),
            UserProgress.personal_note != "",
        )
        .order_by(UserProgress.updated_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [build_due_card_out(up) for up in result.scalars().all()]
