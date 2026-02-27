from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, ReviewLog, Subject, UserProgress
from ..schemas import OverallStatsOut, QuestionStatsOut, SubjectStatsOut

router = APIRouter()


@router.get("/", response_model=OverallStatsOut)
async def overall_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    now   = datetime.now(timezone.utc)
    today = date.today()
    week_ago = now - timedelta(days=7)

    # Total cards registered for user
    total_res = await db.execute(
        select(func.count())
        .select_from(UserProgress)
        .where(UserProgress.user_id == current_user.id)
    )
    total_cards = total_res.scalar_one()

    # Due today
    due_res = await db.execute(
        select(func.count())
        .select_from(UserProgress)
        .where(
            UserProgress.user_id == current_user.id,
            UserProgress.next_review_at <= now,
        )
    )
    due_today = due_res.scalar_one()

    # Reviewed today + correct today (single query, two aggregates)
    today_res = await db.execute(
        select(
            func.count().label("reviewed"),
            func.count().filter(ReviewLog.was_correct == True).label("correct"),
        )
        .select_from(ReviewLog)
        .where(
            ReviewLog.user_id == current_user.id,
            func.date(ReviewLog.reviewed_at) == today,
        )
    )
    today_row = today_res.one()
    reviewed_today = today_row.reviewed
    correct_today  = today_row.correct

    # 7-day accuracy (single query)
    week_res = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(ReviewLog.was_correct == True).label("correct"),
        )
        .select_from(ReviewLog)
        .where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.reviewed_at >= week_ago,
        )
    )
    week_row  = week_res.one()
    acc_total   = week_row.total   or 0
    acc_correct = week_row.correct or 0
    accuracy_7d = round(acc_correct / acc_total * 100, 1) if acc_total else 0.0

    return OverallStatsOut(
        total_cards=total_cards,
        due_today=due_today,
        reviewed_today=reviewed_today,
        correct_today=correct_today,
        accuracy_7d=accuracy_7d,
        study_streak=current_user.study_streak,
    )


@router.get("/subjects")
async def subject_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    FIX H-4: Replaced N+1 per-subject loop with 3 aggregated GROUP BY queries.
    Was: 4 SQL queries × N subjects. Now: 3 queries total, regardless of subject count.
    """
    now   = datetime.now(timezone.utc)
    today = date.today()

    # 1. All subjects
    subjects_res = await db.execute(select(Subject).order_by(Subject.sort_order))
    subjects = subjects_res.scalars().all()

    # 2. Questions per subject
    q_per_sub_res = await db.execute(
        select(Question.subject_id, func.count().label("total"))
        .group_by(Question.subject_id)
    )
    q_per_sub: dict[uuid.UUID, int] = {r.subject_id: r.total for r in q_per_sub_res.all()}

    # 3. Due count per subject (user-specific)
    due_per_sub_res = await db.execute(
        select(Question.subject_id, func.count().label("due"))
        .select_from(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question, Question.id == Flashcard.question_id)
        .where(
            UserProgress.user_id == current_user.id,
            UserProgress.next_review_at <= now,
        )
        .group_by(Question.subject_id)
    )
    due_per_sub: dict[uuid.UUID, int] = {r.subject_id: r.due for r in due_per_sub_res.all()}

    # 4. Review stats per subject: reviewed_today, total reviews, correct reviews
    review_per_sub_res = await db.execute(
        select(
            Question.subject_id,
            func.count().label("total_reviews"),
            func.count().filter(ReviewLog.was_correct == True).label("correct_reviews"),
            func.count()
            .filter(func.date(ReviewLog.reviewed_at) == today)
            .label("reviewed_today"),
        )
        .select_from(ReviewLog)
        .join(Flashcard, Flashcard.id == ReviewLog.flashcard_id)
        .join(Question, Question.id == Flashcard.question_id)
        .where(ReviewLog.user_id == current_user.id)
        .group_by(Question.subject_id)
    )
    review_per_sub = {r.subject_id: r for r in review_per_sub_res.all()}

    out = []
    for s in subjects:
        rev = review_per_sub.get(s.id)
        acc_t = rev.total_reviews   if rev else 0
        acc_c = rev.correct_reviews if rev else 0
        out.append(
            SubjectStatsOut(
                subject_id=s.id,
                subject_name=s.name,
                total=q_per_sub.get(s.id, 0),
                due=due_per_sub.get(s.id, 0),
                reviewed_today=rev.reviewed_today if rev else 0,
                accuracy_all=round(acc_c / acc_t * 100, 1) if acc_t else 0.0,
            )
        )

    return out


@router.get("/weekly")
async def weekly_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Return per-day reviewed/correct/accuracy for the last 7 days."""
    today    = date.today()
    week_ago = today - timedelta(days=6)

    res = await db.execute(
        select(
            func.date(ReviewLog.reviewed_at).label("day"),
            func.count().label("total"),
            func.count().filter(ReviewLog.was_correct == True).label("correct"),
        )
        .where(
            ReviewLog.user_id == current_user.id,
            func.date(ReviewLog.reviewed_at) >= week_ago,
        )
        .group_by(func.date(ReviewLog.reviewed_at))
        .order_by(func.date(ReviewLog.reviewed_at))
    )
    row_map = {str(r.day): r for r in res.all()}

    days = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        r   = row_map.get(day.isoformat())
        total   = r.total   if r else 0
        correct = r.correct if r else 0
        days.append({
            "date":     day.isoformat(),
            "reviewed": total,
            "correct":  correct,
            "accuracy": round(correct / total * 100, 1) if total else 0.0,
        })
    return {"days": days}


@router.get("/peer/{question_id}", response_model=QuestionStatsOut)
async def peer_stats(
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")

    return QuestionStatsOut(
        question_id=q.id,
        total_attempts=q.total_attempts,
        correct_attempts=q.correct_attempts,
        difficulty_pct=round(q.correct_attempts / q.total_attempts * 100, 1)
        if q.total_attempts
        else 0.0,
    )
