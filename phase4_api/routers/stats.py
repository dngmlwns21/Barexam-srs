from __future__ import annotations
# fixed: use func.count() with select_from to avoid UUID type resolution bug
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
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

    # Reviewed today
    reviewed_res = await db.execute(
        select(func.count())
        .select_from(ReviewLog)
        .where(
            ReviewLog.user_id == current_user.id,
            func.date(ReviewLog.reviewed_at) == today,
        )
    )
    reviewed_today = reviewed_res.scalar_one()

    # Correct today
    correct_res = await db.execute(
        select(func.count())
        .select_from(ReviewLog)
        .where(
            ReviewLog.user_id == current_user.id,
            func.date(ReviewLog.reviewed_at) == today,
            ReviewLog.was_correct == True,
        )
    )
    correct_today = correct_res.scalar_one()

    # 7-day accuracy
    acc_total_res = await db.execute(
        select(func.count())
        .select_from(ReviewLog)
        .where(ReviewLog.user_id == current_user.id, ReviewLog.reviewed_at >= week_ago)
    )
    acc_correct_res = await db.execute(
        select(func.count())
        .select_from(ReviewLog)
        .where(
            ReviewLog.user_id == current_user.id,
            ReviewLog.reviewed_at >= week_ago,
            ReviewLog.was_correct == True,
        )
    )
    acc_total   = acc_total_res.scalar_one() or 0
    acc_correct = acc_correct_res.scalar_one() or 0
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
    now   = datetime.now(timezone.utc)
    today = date.today()

    subjects_res = await db.execute(select(Subject))
    subjects = subjects_res.scalars().all()

    out = []
    for s in subjects:
        # Total questions in subject
        total_res = await db.execute(
            select(func.count())
            .select_from(Question)
            .where(Question.subject_id == s.id)
        )
        total = total_res.scalar_one()

        # Due for this user in this subject
        due_res = await db.execute(
            select(func.count())
            .select_from(UserProgress)
            .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
            .join(Question, Question.id == Flashcard.question_id)
            .where(
                UserProgress.user_id == current_user.id,
                Question.subject_id == s.id,
                UserProgress.next_review_at <= now,
            )
        )
        due = due_res.scalar_one()

        # Reviewed today in this subject
        rev_res = await db.execute(
            select(func.count())
            .select_from(ReviewLog)
            .join(Flashcard, Flashcard.id == ReviewLog.flashcard_id)
            .join(Question, Question.id == Flashcard.question_id)
            .where(
                ReviewLog.user_id == current_user.id,
                Question.subject_id == s.id,
                func.date(ReviewLog.reviewed_at) == today,
            )
        )
        reviewed_today = rev_res.scalar_one()

        # Overall accuracy for this subject
        acc_t_res = await db.execute(
            select(func.count())
            .select_from(ReviewLog)
            .join(Flashcard, Flashcard.id == ReviewLog.flashcard_id)
            .join(Question, Question.id == Flashcard.question_id)
            .where(ReviewLog.user_id == current_user.id, Question.subject_id == s.id)
        )
        acc_c_res = await db.execute(
            select(func.count())
            .select_from(ReviewLog)
            .join(Flashcard, Flashcard.id == ReviewLog.flashcard_id)
            .join(Question, Question.id == Flashcard.question_id)
            .where(
                ReviewLog.user_id == current_user.id,
                Question.subject_id == s.id,
                ReviewLog.was_correct == True,
            )
        )
        acc_t = acc_t_res.scalar_one() or 0
        acc_c = acc_c_res.scalar_one() or 0

        out.append(
            SubjectStatsOut(
                subject_id=s.id,
                subject_name=s.name,
                total=total,
                due=due,
                reviewed_today=reviewed_today,
                accuracy_all=round(acc_c / acc_t * 100, 1) if acc_t else 0.0,
            )
        )

    return out


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
