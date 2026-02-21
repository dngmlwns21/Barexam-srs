from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db
from ..models import (
    Flashcard,
    Question,
    ReviewLog,
    StudySession,
    User,
    UserProgress,
)
from ..schemas import QuestionStatsOut, ReviewIn, ReviewLogOut, ReviewOut, SM2StateOut
from ..sm2 import SM2State, calc_next_review_at, compute_next_sm2

router = APIRouter()


async def _upsert_study_session(
    db: AsyncSession, user: User, was_correct: bool
) -> None:
    """Upsert today's study session and update streak."""
    today = date.today()
    yesterday = today - timedelta(days=1)

    result = await db.execute(
        select(StudySession).where(
            StudySession.user_id == user.id,
            StudySession.session_date == today,
        )
    )
    session = result.scalar_one_or_none()
    if session:
        session.cards_reviewed += 1
        if was_correct:
            session.correct_count += 1
    else:
        session = StudySession(
            user_id=user.id,
            session_date=today,
            cards_reviewed=1,
            correct_count=1 if was_correct else 0,
        )
        db.add(session)

        # Update streak
        if user.last_studied_date == yesterday:
            user.study_streak += 1
        elif user.last_studied_date != today:
            user.study_streak = 1
        user.longest_streak = max(user.longest_streak, user.study_streak)
        user.last_studied_date = today


@router.post("/{flashcard_id}", response_model=ReviewOut)
async def submit_review(
    flashcard_id: uuid.UUID,
    body: ReviewIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Fetch flashcard + question
    fc_result = await db.execute(
        select(Flashcard).where(Flashcard.id == flashcard_id)
    )
    fc = fc_result.scalar_one_or_none()
    if not fc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flashcard not found")

    q_result = await db.execute(select(Question).where(Question.id == fc.question_id))
    q = q_result.scalar_one_or_none()

    # Fetch or create UserProgress
    up_result = await db.execute(
        select(UserProgress).where(
            UserProgress.user_id == current_user.id,
            UserProgress.flashcard_id == flashcard_id,
        )
    )
    up = up_result.scalar_one_or_none()
    if not up:
        up = UserProgress(user_id=current_user.id, flashcard_id=flashcard_id)
        db.add(up)
        await db.flush()

    # Determine correctness
    if fc.type == "question":
        was_correct = (body.answer_given == q.correct_choice) if body.answer_given else (body.rating >= 3)
    else:
        # choice_ox: rating >= 3 = user knew it correctly
        was_correct = body.rating >= 3

    # Snapshot old state for audit log
    prev_ef   = float(up.ease_factor)
    prev_iv   = float(up.interval_days)
    prev_reps = up.repetitions

    # Compute new SM-2 state
    new_state = compute_next_sm2(
        rating=body.rating,
        state=SM2State(
            ease_factor=float(up.ease_factor),
            interval_days=float(up.interval_days),
            repetitions=up.repetitions,
        ),
        hard_minutes=current_user.sm2_hard_interval_minutes,
        good_days=current_user.sm2_good_interval_days,
        easy_days=current_user.sm2_easy_interval_days,
    )

    new_due = calc_next_review_at(
        new_state.interval_days,
        vacation_mode=current_user.vacation_mode_enabled,
        vacation_started_at=current_user.vacation_started_at,
    )

    # "Again" cards (rating <= 1) must re-appear immediately in the same session.
    # We keep interval_days=10min in the SM-2 state for future scheduling,
    # but set next_review_at=now so the card is immediately due again.
    if body.rating <= 1:
        new_due = now

    # Apply to UserProgress
    now = datetime.now(timezone.utc)
    up.ease_factor      = new_state.ease_factor
    up.interval_days    = new_state.interval_days
    up.repetitions      = new_state.repetitions
    up.next_review_at   = new_due
    up.last_reviewed_at = now
    up.last_rating      = body.rating

    # Append to review_logs (immutable)
    log = ReviewLog(
        user_id=current_user.id,
        flashcard_id=flashcard_id,
        rating=body.rating,
        answer_given=body.answer_given,
        was_correct=was_correct,
        time_spent_ms=body.time_spent_ms,
        prev_ease_factor=prev_ef,
        prev_interval_days=prev_iv,
        prev_repetitions=prev_reps,
        new_ease_factor=new_state.ease_factor,
        new_interval_days=new_state.interval_days,
        new_next_review_at=new_due,
    )
    db.add(log)

    # Update global peer stats atomically
    if q:
        q.total_attempts += 1
        if was_correct:
            q.correct_attempts += 1

    # Update streak / study session
    await _upsert_study_session(db, current_user, was_correct)

    await db.commit()
    await db.refresh(up)

    sm2_out = SM2StateOut(
        ease_factor=float(up.ease_factor),
        interval_days=float(up.interval_days),
        repetitions=up.repetitions,
        next_review_at=up.next_review_at,
        last_reviewed_at=up.last_reviewed_at,
        last_rating=up.last_rating,
    )

    peer = QuestionStatsOut(
        question_id=q.id,
        total_attempts=q.total_attempts,
        correct_attempts=q.correct_attempts,
        difficulty_pct=round(q.correct_attempts / q.total_attempts * 100, 1) if q.total_attempts else 0.0,
    ) if q else None

    return ReviewOut(
        flashcard_id=flashcard_id,
        was_correct=was_correct,
        new_sm2=sm2_out,
        peer_stats=peer,
    )


@router.get("/history", response_model=List[ReviewLogOut])
async def get_history(
    flashcard_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    stmt = (
        select(ReviewLog)
        .options(selectinload(ReviewLog.flashcard).selectinload(Flashcard.question))
        .where(ReviewLog.user_id == current_user.id)
        .order_by(ReviewLog.reviewed_at.desc())
        .limit(limit)
    )
    if flashcard_id:
        stmt = stmt.where(ReviewLog.flashcard_id == flashcard_id)

    result = await db.execute(stmt)
    logs = result.scalars().all()
    out = []
    for log in logs:
        fc = log.flashcard
        out.append(ReviewLogOut(
            id=log.id,
            flashcard_id=log.flashcard_id,
            rating=log.rating,
            was_correct=log.was_correct,
            reviewed_at=log.reviewed_at,
            question_stem=fc.question.stem if fc and fc.question else None,
            card_type=fc.type if fc else None,
        ))
    return out
