"""Pure SM-2 algorithm — no I/O, no DB dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class SM2State:
    ease_factor: float   # min 1.30, default 2.50
    interval_days: float # fractional days (e.g. 0.00694 ≈ 10 min)
    repetitions: int


def compute_next_sm2(
    rating: int,
    state: SM2State,
    hard_minutes: int = 10,
    good_days: int = 1,
    easy_days: int = 3,
) -> SM2State:
    """
    rating semantics:
      0-1  Again  — reset, short re-study interval
      2    Hard   — correct but difficult, short interval, EF penalty
      3    Good   — correct, standard SM-2 progression
      4    Easy   — correct with ease, extended interval
      5    Perfect— perfect recall, maximum EF boost
    """
    ef = state.ease_factor
    iv = state.interval_days
    rp = state.repetitions

    if rating <= 1:          # Again
        new_rp = 0
        new_iv = hard_minutes / 1440.0
        new_ef = max(1.30, ef - 0.20)

    elif rating == 2:        # Hard
        new_rp = rp + 1
        new_iv = hard_minutes / 1440.0
        new_ef = max(1.30, ef - 0.15)

    elif rating == 3:        # Good
        new_rp = rp + 1
        if rp == 0:
            new_iv = float(good_days)
        elif rp == 1:
            new_iv = float(good_days * 3)
        else:
            new_iv = round(iv * ef, 4)
        new_ef = ef          # no EF change on Good

    else:                    # Easy (4 or 5)
        bonus = 0.10 if rating == 4 else 0.15
        new_rp = rp + 1
        if rp == 0:
            new_iv = float(easy_days)
        elif rp == 1:
            new_iv = float(easy_days * 4)
        else:
            new_iv = round(iv * ef * 1.3, 4)
        new_ef = min(2.50, ef + bonus)

    return SM2State(
        ease_factor=round(max(1.30, new_ef), 2),
        interval_days=max(new_iv, hard_minutes / 1440.0),
        repetitions=new_rp,
    )


def calc_next_review_at(
    interval_days: float,
    vacation_mode: bool = False,
    vacation_started_at: Optional[datetime] = None,
) -> datetime:
    """
    Returns the UTC datetime when the card is next due.
    If vacation mode is active, shifts the due date forward
    by the duration already spent in vacation.
    """
    now = datetime.now(timezone.utc)
    due = now + timedelta(days=interval_days)

    if vacation_mode and vacation_started_at is not None:
        vs = vacation_started_at
        if vs.tzinfo is None:
            vs = vs.replace(tzinfo=timezone.utc)
        vacation_elapsed = now - vs
        due += vacation_elapsed   # push forward so cards don't pile up

    return due
