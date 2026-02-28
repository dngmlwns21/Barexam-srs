"""scheduler.py — Background citation-verification job (APScheduler)."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import or_, select

from .database import AsyncSessionLocal
from .models import Question

log = logging.getLogger(__name__)

LAW_API_KEY = os.getenv("LAW_API_KEY", "")

_scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


# ── Public control ─────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.add_job(
            _citation_check_job,
            trigger="interval",
            hours=24,
            id="citation_check",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        _scheduler.start()
        log.info("Citation-check scheduler started (runs every 24h)")


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Citation-check scheduler stopped")


# ── Job ───────────────────────────────────────────────────────────────────────

async def _citation_check_job() -> None:
    """
    Scan questions with citations that haven't been checked in 7+ days.
    Flags needs_revision=True if a possible amendment/overturn is detected.
    """
    log.info("Citation check job started")
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Question).where(
                or_(
                    Question.last_citation_check_at.is_(None),
                    Question.last_citation_check_at < cutoff,
                ),
                or_(
                    Question.legal_basis.isnot(None),
                    Question.case_citation.isnot(None),
                ),
            ).limit(100)
        )
        questions = result.scalars().all()
        log.info("Checking %d questions with citations", len(questions))

        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            for q in questions:
                try:
                    await _check_one(db, client, q)
                    await asyncio.sleep(0.3)  # gentle rate-limit
                except Exception as exc:
                    log.warning("Check failed for %s: %s", q.id, exc)

        await db.commit()
    log.info("Citation check job done")


async def _check_one(db, client: httpx.AsyncClient, q: Question) -> None:
    now = datetime.now(timezone.utc)
    flagged = False
    reasons: list[str] = []

    if q.legal_basis and LAW_API_KEY:
        amended = await _is_statute_amended(client, q.legal_basis, q.source_year)
        if amended:
            flagged = True
            reasons.append(f"법령 개정 감지: {q.legal_basis}")

    if q.case_citation:
        overturned = await _is_precedent_overturned(client, q.case_citation)
        if overturned:
            flagged = True
            reasons.append(f"판례 변경 가능성: {q.case_citation}")

    q.last_citation_check_at = now
    if flagged:
        q.citation_check_status = "needs_review"
        if not q.needs_revision:
            q.needs_revision = True
            q.outdated_reason = "; ".join(reasons)
        log.info("Flagged question %s: %s", q.id, reasons)
    else:
        q.citation_check_status = "ok"

    await db.flush()


# ── External checks ───────────────────────────────────────────────────────────

async def _is_statute_amended(
    client: httpx.AsyncClient, legal_basis: str, exam_year: int | None
) -> bool:
    """Return True if the statute's most-recent amendment post-dates exam_year."""
    if not LAW_API_KEY or not exam_year:
        return False
    # Extract law name (e.g., '민법')
    law_name_match = re.match(r"^([가-힣]+법[가-힣]*)", legal_basis)
    if not law_name_match:
        return False
    law_name = law_name_match.group(1)
    try:
        r = await client.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": LAW_API_KEY, "target": "law", "type": "JSON",
                    "query": law_name, "display": 1},
        )
        if r.status_code != 200:
            return False
        data = r.json()
        items = (data.get("LawSearch") or {}).get("law") or []
        if not items:
            return False
        effective_date = items[0].get("시행일자", "")  # e.g. "20230601"
        if len(effective_date) >= 4:
            amendment_year = int(effective_date[:4])
            if amendment_year > exam_year:
                return True
    except Exception as exc:
        log.debug("Statute amendment check failed: %s", exc)
    return False


async def _is_precedent_overturned(client: httpx.AsyncClient, case_citation: str) -> bool:
    """
    Conservative check: if we can't find the case at all in law.go.kr,
    flag it for human review. Full overturn detection needs subscription APIs.
    """
    if not LAW_API_KEY:
        return False
    case_nums = re.findall(r"\d{4}[가-힣]+\d+", case_citation)
    if not case_nums:
        return False
    try:
        r = await client.get(
            "https://www.law.go.kr/DRF/lawSearch.do",
            params={"OC": LAW_API_KEY, "target": "prec", "type": "JSON",
                    "query": case_nums[0], "display": 1},
        )
        if r.status_code != 200:
            return False
        data = r.json()
        items = (data.get("PrecSearch") or {}).get("prec") or []
        # If zero results for a specific case number, flag for review
        return len(items) == 0
    except Exception as exc:
        log.debug("Precedent check failed: %s", exc)
    return False
