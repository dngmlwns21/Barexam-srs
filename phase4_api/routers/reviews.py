from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
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
from ..sm2 import (
    SM2State,
    apply_retention_modifier,
    calc_next_review_at,
    compute_next_sm2,
    parse_steps,
)

router = APIRouter()


# ── State machine ─────────────────────────────────────────────────────────────

def _compute_transition(
    state: str,
    step: int,
    rating: int,
    learning_steps: List[float],
    relearning_steps: List[float],
    new_sm2_interval: float,
    target_retention: float,
    vacation_mode: bool,
    vacation_started_at: Optional[datetime],
) -> Tuple[str, int, Optional[datetime], datetime]:
    """
    Returns (new_card_state, new_learning_step, new_learning_due_at, new_next_review_at).
    new_learning_due_at is None when the card is in 'review' state.
    """
    now = datetime.now(timezone.utc)

    def graduate() -> Tuple[str, int, Optional[datetime], datetime]:
        adj = apply_retention_modifier(new_sm2_interval, target_retention)
        due = calc_next_review_at(adj, vacation_mode, vacation_started_at)
        return ("review", 0, None, due)

    def lapse_due(rs: List[float], s: int) -> Tuple[str, int, Optional[datetime], datetime]:
        """Enter lapsed state at relearning step s."""
        if not rs:
            return graduate()
        adj = apply_retention_modifier(new_sm2_interval, target_retention)
        next_rev = calc_next_review_at(adj, vacation_mode, vacation_started_at)
        d = now + timedelta(days=rs[s])
        return ("lapsed", s, d, next_rev)

    if state == "new":
        if rating >= 4:                        # Easy → graduate immediately
            return graduate()
        elif rating == 3:                      # Good → step 1 or graduate
            if len(learning_steps) > 1:
                d = now + timedelta(days=learning_steps[1])
                return ("learning", 1, d, d)
            return graduate()
        else:                                  # Again / Hard → step 0
            if not learning_steps:
                return graduate()
            d = now + timedelta(days=learning_steps[0])
            return ("learning", 0, d, d)

    elif state == "learning":
        if rating >= 4:                        # Easy → graduate
            return graduate()
        elif rating == 3:                      # Good → advance step
            ns = step + 1
            if ns >= len(learning_steps):
                return graduate()
            d = now + timedelta(days=learning_steps[ns])
            return ("learning", ns, d, d)
        elif rating == 2:                      # Hard → same step
            s = min(step, len(learning_steps) - 1) if learning_steps else 0
            if not learning_steps:
                return graduate()
            d = now + timedelta(days=learning_steps[s])
            return ("learning", s, d, d)
        else:                                  # Again → step 0
            if not learning_steps:
                return graduate()
            d = now + timedelta(days=learning_steps[0])
            return ("learning", 0, d, d)

    elif state == "review":
        if rating <= 1:                        # Again → lapse
            return lapse_due(relearning_steps, 0)
        else:                                  # Hard / Good / Easy → stay review
            return graduate()

    elif state == "lapsed":
        if rating >= 4:                        # Easy → graduate back
            return graduate()
        elif rating == 3:                      # Good → advance relearning step
            ns = step + 1
            if not relearning_steps or ns >= len(relearning_steps):
                return graduate()
            adj = apply_retention_modifier(new_sm2_interval, target_retention)
            next_rev = calc_next_review_at(adj, vacation_mode, vacation_started_at)
            d = now + timedelta(days=relearning_steps[ns])
            return ("lapsed", ns, d, next_rev)
        elif rating == 2:                      # Hard → same step
            if not relearning_steps:
                return graduate()
            s = min(step, len(relearning_steps) - 1)
            adj = apply_retention_modifier(new_sm2_interval, target_retention)
            next_rev = calc_next_review_at(adj, vacation_mode, vacation_started_at)
            d = now + timedelta(days=relearning_steps[s])
            return ("lapsed", s, d, next_rev)
        else:                                  # Again → step 0
            return lapse_due(relearning_steps, 0)

    return graduate()


# ── Study session / streak upsert ────────────────────────────────────────────

async def _upsert_study_session(
    db: AsyncSession, user: User, was_correct: bool
) -> None:
    today     = date.today()
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

        if user.last_studied_date == yesterday:
            user.study_streak += 1
        elif user.last_studied_date != today:
            user.study_streak = 1
        user.longest_streak = max(user.longest_streak, user.study_streak)
        user.last_studied_date = today


# ── Submit review ─────────────────────────────────────────────────────────────

@router.post("/{flashcard_id}", response_model=ReviewOut)
async def submit_review(
    flashcard_id: uuid.UUID,
    body: ReviewIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    fc_result = await db.execute(
        select(Flashcard).where(Flashcard.id == flashcard_id)
    )
    fc = fc_result.scalar_one_or_none()
    if not fc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flashcard not found")

    q_result = await db.execute(select(Question).where(Question.id == fc.question_id))
    q = q_result.scalar_one_or_none()

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

    # ── Correctness ──────────────────────────────────────────────────────────
    if fc.type == "question":
        if body.answer_given is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "answer_given is required for MCQ (question-type) cards",
            )
        was_correct = body.answer_given == (q.correct_choice if q else -1)
    else:
        was_correct = body.rating >= 3

    # ── Snapshot pre-review state ────────────────────────────────────────────
    prev_ef          = float(up.ease_factor)
    prev_iv          = float(up.interval_days)
    prev_reps        = up.repetitions
    prev_card_state  = up.card_state or ("review" if up.repetitions > 0 else "new")

    # ── SM-2 computation ─────────────────────────────────────────────────────
    new_sm2 = compute_next_sm2(
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

    # ── Card state transition ────────────────────────────────────────────────
    learning_steps    = parse_steps(current_user.learning_steps    or "1 10")
    relearning_steps  = parse_steps(current_user.relearning_steps  or "10")
    target_retention  = float(current_user.target_retention or 0.90)

    (new_card_state, new_learning_step, new_learning_due_at, new_next_review_at) = \
        _compute_transition(
            state=prev_card_state,
            step=up.learning_step or 0,
            rating=body.rating,
            learning_steps=learning_steps,
            relearning_steps=relearning_steps,
            new_sm2_interval=new_sm2.interval_days,
            target_retention=target_retention,
            vacation_mode=current_user.vacation_mode_enabled,
            vacation_started_at=current_user.vacation_started_at,
        )

    # ── Apply to UserProgress ────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    up.ease_factor       = new_sm2.ease_factor
    up.interval_days     = new_sm2.interval_days
    up.repetitions       = new_sm2.repetitions
    up.card_state        = new_card_state
    up.learning_step     = new_learning_step
    up.learning_due_at   = new_learning_due_at
    up.next_review_at    = new_next_review_at
    up.last_reviewed_at  = now
    up.last_rating       = body.rating

    # Mark first study date when card exits 'new'
    if prev_card_state == "new" and new_card_state != "new":
        if not up.date_first_studied:
            up.date_first_studied = date.today()

    # Track lapses
    if prev_card_state == "review" and new_card_state == "lapsed":
        up.lapses = (up.lapses or 0) + 1

    # ── Audit log ────────────────────────────────────────────────────────────
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
        prev_card_state=prev_card_state,
        new_ease_factor=new_sm2.ease_factor,
        new_interval_days=new_sm2.interval_days,
        new_next_review_at=new_next_review_at,
    )
    db.add(log)

    # ── Atomic peer-stats increment ──────────────────────────────────────────
    new_total   = (q.total_attempts   or 0) + 1
    new_correct = (q.correct_attempts or 0) + (1 if was_correct else 0)
    if q:
        await db.execute(
            update(Question)
            .where(Question.id == q.id)
            .values(
                total_attempts=Question.total_attempts + 1,
                correct_attempts=Question.correct_attempts + (1 if was_correct else 0),
            )
        )

    await _upsert_study_session(db, current_user, was_correct)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Review save failed — please retry"
        )

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
        total_attempts=new_total,
        correct_attempts=new_correct,
        difficulty_pct=round(new_correct / new_total * 100, 1) if new_total else 0.0,
    ) if q else None

    return ReviewOut(
        flashcard_id=flashcard_id,
        was_correct=was_correct,
        new_sm2=sm2_out,
        peer_stats=peer,
    )


# ── Undo ──────────────────────────────────────────────────────────────────────

@router.delete("/{flashcard_id}/undo-last", status_code=200)
async def undo_last_review(
    flashcard_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    log_result = await db.execute(
        select(ReviewLog)
        .where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.flashcard_id == flashcard_id,
        )
        .order_by(ReviewLog.reviewed_at.desc())
        .limit(1)
    )
    log = log_result.scalar_one_or_none()
    if not log:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No review to undo")

    up_result = await db.execute(
        select(UserProgress).where(
            UserProgress.user_id == current_user.id,
            UserProgress.flashcard_id == flashcard_id,
        )
    )
    up = up_result.scalar_one_or_none()
    if up:
        up.ease_factor       = log.prev_ease_factor    or 2.5
        up.interval_days     = log.prev_interval_days  or 0
        up.repetitions       = log.prev_repetitions    or 0
        up.card_state        = log.prev_card_state     or "new"
        up.learning_step     = 0
        up.learning_due_at   = None
        up.next_review_at    = datetime.now(timezone.utc)
        up.last_reviewed_at  = None
        up.last_rating       = None

    await db.delete(log)
    await db.commit()
    return {"undone": True, "flashcard_id": str(flashcard_id)}


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/history", response_model=List[ReviewLogOut])
async def get_history(
    flashcard_id: Optional[uuid.UUID] = Query(None),
    limit:        int = Query(50, ge=1, le=200),
    offset:       int = Query(0,  ge=0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    stmt = (
        select(ReviewLog)
        .options(selectinload(ReviewLog.flashcard).selectinload(Flashcard.question))
        .where(ReviewLog.user_id == current_user.id)
        .order_by(ReviewLog.reviewed_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if flashcard_id:
        stmt = stmt.where(ReviewLog.flashcard_id == flashcard_id)

    result = await db.execute(stmt)
    logs   = result.scalars().all()
    out    = []
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
