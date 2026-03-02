from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import queries
from ..dependencies import get_current_user, get_db
from ..models import Flashcard, Question, Subject, User, UserProgress
from ..schemas import DailyStatusOut, DeckStatsOut
from ..utils import build_due_card_out, up_load_opts

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_start() -> datetime:
    t = date.today()
    return datetime(t.year, t.month, t.day, tzinfo=timezone.utc)


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

    # 각 과목별 전체 카드 수 계산
    total_cards_q = (
        select(Question.subject_id, func.count().label("cnt"))
        .select_from(Question)
        .group_by(Question.subject_id)
    )
    total_cards_rows = (await db.execute(total_cards_q)).all()
    total_cards_map = {str(r.subject_id): r.cnt for r in total_cards_rows}
    grand_total_cards = sum(total_cards_map.values())

    # 과목+세부분류별 전체 카드 수
    total_subcat_q = (
        select(Question.subject_id, Question.sub_category, func.count().label("cnt"))
        .select_from(Question)
        .where(Question.sub_category.isnot(None))
        .group_by(Question.subject_id, Question.sub_category)
    )
    total_subcat_rows = (await db.execute(total_subcat_q)).all()
    total_subcat_map: dict = {}
    for r in total_subcat_rows:
        total_subcat_map[(str(r.subject_id), r.sub_category)] = r.cnt

    new_today    = await queries.count_new_today(db, uid, _today_start())
    review_today = await queries.count_review_today(db, uid, _today_start())

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

    # Learning by (subject_id, sub_category)
    lrn_subcat_q = (
        select(Question.subject_id, Question.sub_category, func.count().label("cnt"))
        .select_from(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question,  Question.id  == Flashcard.question_id)
        .where(
            UserProgress.user_id == uid,
            UserProgress.card_state.in_(["learning", "lapsed"]),
            UserProgress.learning_due_at <= now,
            Question.sub_category.isnot(None),
        )
        .group_by(Question.subject_id, Question.sub_category)
    )
    lrn_subcat_map: dict = {}
    for r in (await db.execute(lrn_subcat_q)).all():
        lrn_subcat_map[(str(r.subject_id), r.sub_category)] = r.cnt

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

    # Review by (subject_id, sub_category)
    rev_subcat_q = (
        select(Question.subject_id, Question.sub_category, func.count().label("cnt"))
        .select_from(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question,  Question.id  == Flashcard.question_id)
        .where(
            UserProgress.user_id == uid,
            UserProgress.card_state == "review",
            UserProgress.next_review_at <= now,
            Question.sub_category.isnot(None),
        )
        .group_by(Question.subject_id, Question.sub_category)
    )
    rev_subcat_map: dict = {}
    for r in (await db.execute(rev_subcat_q)).all():
        rev_subcat_map[(str(r.subject_id), r.sub_category)] = r.cnt

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

    # New by (subject_id, sub_category)
    new_subcat_q = (
        select(Question.subject_id, Question.sub_category, func.count().label("cnt"))
        .select_from(UserProgress)
        .join(Flashcard, Flashcard.id == UserProgress.flashcard_id)
        .join(Question,  Question.id  == Flashcard.question_id)
        .where(
            UserProgress.user_id == uid,
            UserProgress.card_state == "new",
            Question.sub_category.isnot(None),
        )
        .group_by(Question.subject_id, Question.sub_category)
    )
    new_subcat_map: dict = {}
    for r in (await db.execute(new_subcat_q)).all():
        new_subcat_map[(str(r.subject_id), r.sub_category)] = r.cnt

    # ── Fetch subjects ────────────────────────────────────────────────────────
    subj_result = await db.execute(select(Subject).order_by(Subject.sort_order, Subject.name))
    subjects    = subj_result.scalars().all()

    out: List[DeckStatsOut] = []

    # Overall row
    out.append(DeckStatsOut(
        subject_id=None,
        subject_name="전체 과목",
        sub_category=None,
        new_count=min(new_total_raw, new_remaining),
        learning_count=lrn_total,
        review_count=min(rev_total_raw, review_remaining),
        total_cards=grand_total_cards,
    ))

    # Per-subject rows + sub-category rows
    for subj in subjects:
        sid = str(subj.id)
        s_lrn = lrn_map.get(sid, 0)
        s_rev = rev_map.get(sid, 0)
        s_new = new_map.get(sid, 0)
        out.append(DeckStatsOut(
            subject_id=subj.id,
            subject_name=subj.name,
            sub_category=None,
            new_count=int(s_new * new_scale),
            learning_count=s_lrn,
            review_count=int(s_rev * rev_scale),
            total_cards=total_cards_map.get(sid, 0),
        ))
        # Sub-category rows for this subject
        subcats = sorted({k[1] for k in total_subcat_map if k[0] == sid})
        for sc in subcats:
            key = (sid, sc)
            sc_lrn = lrn_subcat_map.get(key, 0)
            sc_rev = rev_subcat_map.get(key, 0)
            sc_new = new_subcat_map.get(key, 0)
            out.append(DeckStatsOut(
                subject_id=subj.id,
                subject_name=subj.name,
                sub_category=sc,
                new_count=int(sc_new * new_scale),
                learning_count=sc_lrn,
                review_count=int(sc_rev * rev_scale),
                total_cards=total_subcat_map.get(key, 0),
            ))

    return out
