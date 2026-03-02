"""dictionary.py — Mini Legal Dictionary: DB-first statute/precedent lookup + law.go.kr fallback."""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..models import LawStatute, LegalPrecedent

log = logging.getLogger(__name__)
router = APIRouter()

LAW_API_KEY = os.getenv("LAW_API_KEY", "")

# Korean case number pattern: 2017다1234, 2020헌바123, etc.
_CASE_NUM_RE = re.compile(r"^\d{4}[가-힣]+\d+")


class DictResult(BaseModel):
    type:    str             # 'statute' | 'precedent'
    title:   str
    snippet: str
    url:     Optional[str] = None
    date:    Optional[str] = None
    subject: Optional[str] = None


@router.get("/search", response_model=List[DictResult])
async def dictionary_search(
    q: str = Query(..., min_length=1, max_length=300),
    type: str = Query("all", regex="^(all|statute|precedent)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search Korean legal statutes and precedents.
    Priority: 1) law_statutes / legal_precedents DB  2) law.go.kr open API (fallback + cache)
    type: 'all' | 'statute' | 'precedent'
    """
    results: List[DictResult] = []

    # ── 1. DB search ──────────────────────────────────────────────────────────
    if type in ("all", "statute"):
        results.extend(await _search_statutes_db(db, q))
    if type in ("all", "precedent"):
        results.extend(await _search_precedents_db(db, q))

    # ── 2. Fallback to law.go.kr if DB returned nothing ──────────────────────
    if not results:
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                if type in ("all", "statute"):
                    ext_statutes = await _statute_search_api(client, q)
                    # Cache results to DB
                    for s in ext_statutes:
                        await _cache_statute(db, s)
                    results.extend(ext_statutes)
                if type in ("all", "precedent"):
                    ext_precs = await _precedent_search_api(client, q)
                    for p in ext_precs:
                        await _cache_precedent(db, p)
                    results.extend(ext_precs)
            try:
                await db.commit()
            except Exception:
                await db.rollback()
        except Exception as exc:
            log.warning("External law search failed: %s", exc)

    if not results:
        return [DictResult(
            type="statute",
            title="검색 결과 없음",
            snippet=f"'{q}'에 대한 법령·판례 정보를 찾을 수 없습니다.",
        )]

    return results[:10]


@router.get("/laws", response_model=List[DictResult])
async def list_laws(db: AsyncSession = Depends(get_db)):
    """Return all law_statutes grouped (flat list, ordered by subject then name)."""
    rows = await db.execute(
        select(LawStatute).order_by(LawStatute.subject, LawStatute.name)
    )
    return [
        DictResult(
            type="statute",
            title=row.name,
            snippet=f"{row.category or ''} · 시행 {row.effective_date or ''}".strip(" ·"),
            url=row.law_url,
            subject=row.subject,
        )
        for row in rows.scalars().all()
    ]


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _search_statutes_db(db: AsyncSession, q: str) -> List[DictResult]:
    term = f"%{q}%"
    # Try exact/prefix match first for better ranking
    rows = await db.execute(
        select(LawStatute)
        .where(LawStatute.name.ilike(term))
        .order_by(
            # Exact match first, then prefix, then contains
            func.length(LawStatute.name).asc()
        )
        .limit(7)
    )
    results = rows.scalars().all()

    # Fallback: full-text search with simple dictionary
    if not results:
        fts_rows = await db.execute(
            text("""
                SELECT * FROM law_statutes
                WHERE to_tsvector('simple', name) @@ plainto_tsquery('simple', :q)
                ORDER BY length(name) ASC
                LIMIT 7
            """),
            {"q": q},
        )
        results = [LawStatute(**dict(r._mapping)) for r in fts_rows.fetchall()]

    return [
        DictResult(
            type="statute",
            title=row.name,
            snippet=f"{row.category or ''} · 시행 {row.effective_date or ''}".strip(" ·"),
            url=row.law_url,
            date=row.effective_date,
            subject=row.subject,
        )
        for row in results
    ]


async def _search_precedents_db(db: AsyncSession, q: str) -> List[DictResult]:
    term = f"%{q}%"
    is_case_num = bool(_CASE_NUM_RE.match(q.strip()))

    if is_case_num:
        # Exact case number lookup (e.g. "2017다1234")
        rows = await db.execute(
            select(LegalPrecedent)
            .where(LegalPrecedent.case_number.ilike(term))
            .limit(7)
        )
    else:
        rows = await db.execute(
            select(LegalPrecedent)
            .where(
                or_(
                    LegalPrecedent.case_number.ilike(term),
                    LegalPrecedent.case_name.ilike(term),
                    LegalPrecedent.holding.ilike(term),
                    LegalPrecedent.verdict_summary.ilike(term),
                )
            )
            .limit(7)
        )
    results = rows.scalars().all()

    # Fallback: full-text search
    if not results:
        fts_rows = await db.execute(
            text("""
                SELECT * FROM legal_precedents
                WHERE to_tsvector('simple',
                    coalesce(case_number,'') || ' ' ||
                    coalesce(case_name,'')   || ' ' ||
                    coalesce(holding,'')
                ) @@ plainto_tsquery('simple', :q)
                LIMIT 7
            """),
            {"q": q},
        )
        results = [LegalPrecedent(**dict(r._mapping)) for r in fts_rows.fetchall()]

    return [
        DictResult(
            type="precedent",
            title=row.case_number + (f" {row.case_name}" if row.case_name else ""),
            snippet=(row.holding or row.verdict_summary or "")[:250],
            url=row.source_url,
            date=row.decision_date,
            subject=row.subject,
        )
        for row in results
    ]


# ── law.go.kr API helpers ─────────────────────────────────────────────────────

async def _statute_search_api(client: httpx.AsyncClient, q: str) -> List[DictResult]:
    results: List[DictResult] = []
    try:
        params = {
            "OC": LAW_API_KEY or "openapi",
            "target": "law",
            "type": "JSON",
            "query": q,
            "display": 5,
            "page": 1,
        }
        r = await client.get("https://www.law.go.kr/DRF/lawSearch.do", params=params)
        if r.status_code == 200:
            data = r.json()
            for item in (data.get("LawSearch") or {}).get("law") or []:
                name = item.get("법령명한글", "")
                if not name:
                    continue
                results.append(DictResult(
                    type="statute",
                    title=name,
                    snippet=f"{item.get('법령구분명', '')} · 시행 {item.get('시행일자', '')}",
                    url=f"https://www.law.go.kr/법령/{name}",
                    date=item.get("시행일자"),
                ))
    except Exception as exc:
        log.debug("Statute API error: %s", exc)
    return results


async def _precedent_search_api(client: httpx.AsyncClient, q: str) -> List[DictResult]:
    results: List[DictResult] = []
    try:
        params = {
            "OC": LAW_API_KEY or "openapi",
            "target": "prec",
            "type": "JSON",
            "query": q,
            "display": 5,
            "page": 1,
        }
        r = await client.get("https://www.law.go.kr/DRF/lawSearch.do", params=params)
        if r.status_code == 200:
            data = r.json()
            for item in (data.get("PrecSearch") or {}).get("prec") or []:
                case_name = item.get("사건명", "")
                판시사항 = item.get("판시사항", "")
                serial = item.get("판례정보일련번호", "")
                if not case_name:
                    continue
                results.append(DictResult(
                    type="precedent",
                    title=case_name,
                    snippet=판시사항[:250] if 판시사항 else "",
                    url=f"https://www.law.go.kr/판례/{serial}" if serial else None,
                    date=item.get("선고일자"),
                ))
    except Exception as exc:
        log.debug("Precedent API error: %s", exc)
    return results


# ── DB cache writers ──────────────────────────────────────────────────────────

async def _cache_statute(db: AsyncSession, result: DictResult) -> None:
    """Insert statute into DB if not already present (best-effort)."""
    try:
        existing = await db.execute(
            select(LawStatute).where(LawStatute.name == result.title).limit(1)
        )
        if existing.scalars().first():
            return
        law_id = result.title.replace(" ", "_")
        db.add(LawStatute(
            law_id=law_id,
            name=result.title,
            effective_date=result.date,
            law_url=result.url,
        ))
    except Exception as exc:
        log.debug("Cache statute failed: %s", exc)


async def _cache_precedent(db: AsyncSession, result: DictResult) -> None:
    """Insert precedent into DB if not already present (best-effort)."""
    try:
        case_number = result.title.split(" ")[0] if result.title else result.title
        existing = await db.execute(
            select(LegalPrecedent).where(LegalPrecedent.case_number == case_number).limit(1)
        )
        if existing.scalars().first():
            return
        db.add(LegalPrecedent(
            case_number=case_number,
            case_name=result.title,
            decision_date=result.date,
            holding=result.snippet or None,
            source_url=result.url,
        ))
    except Exception as exc:
        log.debug("Cache precedent failed: %s", exc)
