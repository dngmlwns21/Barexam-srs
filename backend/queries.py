import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ReviewLog


async def count_new_today(db: AsyncSession, user_id: uuid.UUID, today_start: datetime) -> int:
    """Count flashcards introduced from 'new' state today."""
    result = await db.execute(
        select(func.count(func.distinct(ReviewLog.flashcard_id))).where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.prev_card_state == "new",
        )
    )
    return result.scalar() or 0


async def count_review_today(db: AsyncSession, user_id: uuid.UUID, today_start: datetime) -> int:
    """Count distinct 'review'-state flashcards reviewed today."""
    result = await db.execute(
        select(func.count(func.distinct(ReviewLog.flashcard_id))).where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= today_start,
            ReviewLog.prev_card_state == "review",
        )
    )
    return result.scalar() or 0
