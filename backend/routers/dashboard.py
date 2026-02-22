from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, ReviewLog, Subject, User, UserProgress
from ..schemas import DailyStatusOut, DeckStatsOut
from ..utils import build_due_card_out, up_load_opts

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_start() -> datetime:
    t = date.today()
    return datetime(t.year, t.month, t.day, tzinfo=timezone.utc)


async def _count_new_today(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(func.distinct(ReviewLog.flashcard_id)))
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= _today_start(),
            ReviewLog.prev_card_state == "new",
        )
    )
    return result.scalar() or 0


async def _count_review_today(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(func.distinct(ReviewLog.flashcard_id)))
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= _today_start(),
            ReviewLog.prev_card_state == "review",
        )
    )
    return result.scalar() or 0


# ── Daily status ──────────────────────────────────────────────────────────────

@router.get("/daily-status", response_model=DailyStatusOut)
async def get_daily_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now        = datetime.now(timezone.utc)
    ts         = _today_start()
    opts       = up_load_opts()

    done_result = await db.execute(
        select(UserProgress)
        .options(*opts)
        .where(
            UserProgress.user_id == current_user.id,
            UserProgress.last_reviewed_at >= ts,
        )
        .order_by(UserProgress.last_reviewed_at.desc())
    )
    done_rows = done_result.scalars().all()

    todo_result = await db.execute(
        select(UserProgress)
        .options(*opts)
        .where(
            UserProgress.user_id == current_user.id,
            UserProgress.next_review_at <= now,
            or_(
                UserProgress.last_reviewed_at.is_(None),
                UserProgress.last_reviewed_at < ts,
            ),
        )
        .order_by(UserProgress.next_review_at.asc())
    )
    todo_rows = todo_result.scalars().all()

    return DailyStatusOut(
        done=[build_due_card_out(up) for up in done_rows],
        todo=[build_due_card_out(up) for up in todo_rows],
    )


# ── Deck stats ────────────────────────────────────────────────────────────────

@router.get("/deck-stats", response_model=List[DeckStatsOut])
async def get_deck_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Per-subject card counts for the Anki-style home dashboard.
    Returns one row per subject plus one row for '전체 과목' (subject_id=None).
    Counts reflect cards available to study right now, respecting daily limits.
    """
    now  = datetime.now(timezone.utc)
    uid  = current_user.id

    new_today    = await _count_new_today(db, uid)
    review_today = await _count_review_today(db, uid)

    new_remaining    = max(0, (current_user.daily_new_limit    or 20)  - new_today)
    review_remaining = max(0, (current_user.daily_review_limit or 200) - review_today)

    # ── Learning / lapsed due now — no daily limit ────────────────────────────
    learning_q = (
        select(Question.subject_id, func.count().label("cnt"))
        .select_from(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question,  Question.id  == Flashcard.question_id)
        .where(
            UserProgress.user_id == uid,
            UserProgress.card_state.in_(["learning", "lapsed"]),
            UserProgress.learning_due_at <= now,
        )
        .group_by(Question.subject_id)
    )
    lrn_rows = (await db.execute(learning_q)).all()
    lrn_map  = {str(r.subject_id): r.cnt for r in lrn_rows}
    lrn_total = sum(lrn_map.values())

    # ── Review due — capped by daily limit ────────────────────────────────────
    review_q = (
        select(Question.subject_id, func.count().label("cnt"))
        .select_from(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question,  Question.id  == Flashcard.question_id)
        .where(
            UserProgress.user_id == uid,
            UserProgress.card_state == "review",
            UserProgress.next_review_at <= now,
        )
        .group_by(Question.subject_id)
    )
    rev_rows = (await db.execute(review_q)).all()
    rev_map  = {str(r.subject_id): r.cnt for r in rev_rows}
    rev_total_raw = sum(rev_map.values())
    # Scale counts proportionally to daily limit
    rev_scale = (review_remaining / rev_total_raw) if rev_total_raw > review_remaining > 0 else 1.0

    # ── New cards — capped by daily limit ─────────────────────────────────────
    new_q = (
        select(Question.subject_id, func.count().label("cnt"))
        .select_from(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question,  Question.id  == Flashcard.question_id)
        .where(
            UserProgress.user_id == uid,
            UserProgress.card_state == "new",
        )
        .group_by(Question.subject_id)
    )
    new_rows = (await db.execute(new_q)).all()
    new_map  = {str(r.subject_id): r.cnt for r in new_rows}
    new_total_raw = sum(new_map.values())
    new_scale = (new_remaining / new_total_raw) if new_total_raw > new_remaining > 0 else 1.0

    # ── Fetch subjects ────────────────────────────────────────────────────────
    subj_result = await db.execute(select(Subject).order_by(Subject.sort_order, Subject.name))
    subjects    = subj_result.scalars().all()

    out: List[DeckStatsOut] = []

    # Overall row
    out.append(DeckStatsOut(
        subject_id=None,
        subject_name="전체 과목",
        new_count=min(new_total_raw, new_remaining),
        learning_count=lrn_total,
        review_count=min(rev_total_raw, review_remaining),
    ))

    # Per-subject rows
    for subj in subjects:
        sid = str(subj.id)
        s_lrn = lrn_map.get(sid, 0)
        s_rev = rev_map.get(sid, 0)
        s_new = new_map.get(sid, 0)
        out.append(DeckStatsOut(
            subject_id=subj.id,
            subject_name=subj.name,
            new_count=int(s_new * new_scale),
            learning_count=s_lrn,
            review_count=int(s_rev * rev_scale),
        ))

    return out
