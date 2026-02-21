from __future__ import annotations

import math
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..dependencies import get_current_user, get_db
from ..models import (
    Choice,
    Flashcard,
    Question,
    QuestionTag,
    Subject,
    Tag,
    UserProgress,
)
from ..schemas import (
    AnswerOut,
    ChoiceOut,
    NoteIn,
    PaginatedQuestions,
    QuestionListOut,
    QuestionOut,
    QuestionStatsOut,
    SetTagsIn,
    StarIn,
)

router = APIRouter()


def _difficulty_pct(total: int, correct: int) -> float:
    if total == 0:
        return 0.0
    return round(correct / total * 100, 1)


@router.get("/", response_model=PaginatedQuestions)
async def list_questions(
    subject_id:  Optional[uuid.UUID] = Query(None),
    exam_type:   Optional[str]       = Query(None),
    source_year: Optional[int]       = Query(None),
    tags:        Optional[List[str]] = Query(None),
    is_outdated: Optional[bool]      = Query(None),
    page:        int                 = Query(1, ge=1),
    limit:       int                 = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    stmt = select(Question)

    if subject_id:
        stmt = stmt.where(Question.subject_id == subject_id)
    if exam_type:
        stmt = stmt.where(Question.exam_type == exam_type)
    if source_year:
        stmt = stmt.where(Question.source_year == source_year)
    if tags:
        # All specified tags must be present (overlap operator &&)
        from sqlalchemy.dialects.postgresql import array
        stmt = stmt.where(Question.tags.overlap(tags))
    if is_outdated is not None:
        stmt = stmt.where(Question.is_outdated == is_outdated)

    # Count total
    from sqlalchemy import func
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate
    stmt = stmt.offset((page - 1) * limit).limit(limit).order_by(
        Question.source_year.desc().nullslast(),
        Question.question_number,
    )
    result = await db.execute(stmt)
    questions = result.scalars().all()

    return PaginatedQuestions(
        items=[QuestionListOut.model_validate(q) for q in questions],
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit) if total else 0,
    )


@router.get("/{question_id}", response_model=QuestionOut)
async def get_question(
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(Question)
        .options(selectinload(Question.choices))
        .where(Question.id == question_id)
    )
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return QuestionOut.model_validate(q)


@router.get("/{question_id}/answer", response_model=AnswerOut)
async def get_answer(
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return AnswerOut(answer=q.correct_choice, explanation=q.explanation)


@router.get("/{question_id}/stats", response_model=QuestionStatsOut)
async def get_question_stats(
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
        difficulty_pct=_difficulty_pct(q.total_attempts, q.correct_attempts),
    )


@router.put("/{question_id}/note", status_code=status.HTTP_200_OK)
async def set_note(
    question_id: uuid.UUID,
    body: NoteIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Upserts personal_note on the question-level flashcard's user_progress."""
    fc_result = await db.execute(
        select(Flashcard).where(
            Flashcard.question_id == question_id,
            Flashcard.type == "question",
        )
    )
    fc = fc_result.scalar_one_or_none()
    if not fc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flashcard not found")

    up_result = await db.execute(
        select(UserProgress).where(
            UserProgress.user_id == current_user.id,
            UserProgress.flashcard_id == fc.id,
        )
    )
    up = up_result.scalar_one_or_none()
    if not up:
        up = UserProgress(user_id=current_user.id, flashcard_id=fc.id)
        db.add(up)

    up.personal_note = body.personal_note
    await db.commit()
    return {"personal_note": up.personal_note}


@router.put("/{question_id}/star", status_code=status.HTTP_200_OK)
async def set_star(
    question_id: uuid.UUID,
    body: StarIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    fc_result = await db.execute(
        select(Flashcard).where(
            Flashcard.question_id == question_id,
            Flashcard.type == "question",
        )
    )
    fc = fc_result.scalar_one_or_none()
    if not fc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flashcard not found")

    up_result = await db.execute(
        select(UserProgress).where(
            UserProgress.user_id == current_user.id,
            UserProgress.flashcard_id == fc.id,
        )
    )
    up = up_result.scalar_one_or_none()
    if not up:
        up = UserProgress(user_id=current_user.id, flashcard_id=fc.id)
        db.add(up)

    up.is_starred = body.is_starred
    await db.commit()
    return {"is_starred": up.is_starred}


@router.post("/{question_id}/flashcards/split", status_code=status.HTTP_201_CREATED)
async def split_to_choice_ox(
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """
    Creates one choice_ox Flashcard per Choice for the question.
    Idempotent — silently skips already-existing splits.
    """
    q_result = await db.execute(
        select(Question)
        .options(selectinload(Question.choices))
        .where(Question.id == question_id)
    )
    q = q_result.scalar_one_or_none()
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    if not q.choices:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question has no choices")

    existing_result = await db.execute(
        select(Flashcard.choice_id).where(
            Flashcard.question_id == question_id,
            Flashcard.type == "choice_ox",
        )
    )
    existing_choice_ids = {r for r in existing_result.scalars()}

    created = 0
    for choice in q.choices:
        if choice.id not in existing_choice_ids:
            db.add(Flashcard(question_id=question_id, choice_id=choice.id, type="choice_ox"))
            created += 1

    await db.commit()
    return {"created": created, "total_splits": len(q.choices)}


@router.delete("/{question_id}/flashcards/split", status_code=status.HTTP_200_OK)
async def remove_choice_ox_splits(
    question_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    result = await db.execute(
        select(Flashcard).where(
            Flashcard.question_id == question_id,
            Flashcard.type == "choice_ox",
        )
    )
    flashcards = result.scalars().all()
    for fc in flashcards:
        await db.delete(fc)
    await db.commit()
    return {"deleted": len(flashcards)}


@router.post("/{question_id}/tags", status_code=status.HTTP_200_OK)
async def set_question_tags(
    question_id: uuid.UUID,
    body: SetTagsIn,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    q_result = await db.execute(select(Question).where(Question.id == question_id))
    q = q_result.scalar_one_or_none()
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")

    # Delete old associations
    old = await db.execute(
        select(QuestionTag).where(QuestionTag.question_id == question_id)
    )
    for qt in old.scalars():
        await db.delete(qt)

    # Validate tags exist
    tag_names: list[str] = []
    for tag_id in body.tag_ids:
        tag_result = await db.execute(select(Tag).where(Tag.id == tag_id))
        tag = tag_result.scalar_one_or_none()
        if tag:
            db.add(QuestionTag(question_id=question_id, tag_id=tag_id))
            tag_names.append(tag.name)

    # Keep denormalized array in sync
    q.tags = tag_names
    await db.commit()
    return {"tags": tag_names}
