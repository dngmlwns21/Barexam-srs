"""Shared helper utilities for router modules."""
from __future__ import annotations

from .models import UserProgress
from .schemas import ChoiceOut, DueCardOut, QuestionCardOut, SM2StateOut


def build_due_card_out(up: UserProgress) -> DueCardOut:
    """Convert a fully-loaded UserProgress ORM row into a DueCardOut schema."""
    fc = up.flashcard
    q  = fc.question
    sm2 = SM2StateOut(
        ease_factor=float(up.ease_factor),
        interval_days=float(up.interval_days),
        repetitions=up.repetitions,
        next_review_at=up.next_review_at,
        last_reviewed_at=up.last_reviewed_at,
        last_rating=up.last_rating,
    )
    return DueCardOut(
        flashcard_id=fc.id,
        type=fc.type,
        question=QuestionCardOut.model_validate(q),   # MCQ choices stripped of is_correct
        choice=ChoiceOut.model_validate(fc.choice) if fc.choice else None,  # OX keeps is_correct
        sm2=sm2,
        personal_note=up.personal_note,
        is_starred=up.is_starred,
    )


# Standard selectinload options for UserProgress → Flashcard → Question/Choice
# Import these in routers:
#   from sqlalchemy.orm import selectinload
#   from .utils import build_due_card_out, UP_LOAD_OPTS
#   stmt = select(UserProgress).options(*UP_LOAD_OPTS) ...
def up_load_opts():
    """Return the standard eager-load options for UserProgress queries."""
    from sqlalchemy.orm import selectinload
    from .models import Flashcard, Question
    return [
        selectinload(UserProgress.flashcard)
        .selectinload(Flashcard.question)
        .selectinload(Question.choices),
        selectinload(UserProgress.flashcard)
        .selectinload(Flashcard.choice),
    ]
