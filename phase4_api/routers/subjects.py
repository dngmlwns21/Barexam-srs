from __future__ import annotations

from typing import List
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, Subject, UserProgress
from ..schemas import SubjectOut

router = APIRouter()


@router.get("/", response_model=List[SubjectOut])
async def list_subjects(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Total questions per subject
    q_counts = await db.execute(
        select(Question.subject_id, func.count(Question.id).label("total"))
        .group_by(Question.subject_id)
    )
    total_map = {row.subject_id: row.total for row in q_counts}

    # Due flashcards per subject for this user
    now = datetime.now(timezone.utc)
    due_q = (
        select(Question.subject_id, func.count(UserProgress.id).label("due"))
        .join(Flashcard, Flashcard.question_id == Question.id)
        .join(UserProgress, UserProgress.flashcard_id == Flashcard.id)
        .where(
            UserProgress.user_id == current_user.id,
            UserProgress.next_review_at <= now,
        )
        .group_by(Question.subject_id)
    )
    due_counts = await db.execute(due_q)
    due_map = {row.subject_id: row.due for row in due_counts}

    subjects_result = await db.execute(select(Subject).order_by(Subject.sort_order, Subject.name))
    subjects = subjects_result.scalars().all()

    return [
        SubjectOut(
            id=s.id,
            name=s.name,
            description=s.description,
            total_questions=total_map.get(s.id, 0),
            due_count=due_map.get(s.id, 0),
        )
        for s in subjects
    ]
