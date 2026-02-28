"""dictionary.py — Mini Legal Dictionary: DB-first citation lookup + law.go.kr fallback."""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..models import Choice, Question

log = logging.getLogger(__name__)
router = APIRouter()

LAW_API_KEY = os.getenv("LAW_API_KEY", "")  # optional — register at open.law.go.kr


class DictResult(BaseModel):
    type:    str             # 'statute' | 'precedent' | 'card'
    title:   str
    snippet: str
    url:     Optional[str] = None
    date:    Optional[str] = None


@router.get("/search", response_model=List[DictResult])
async def dictionary_search(
    q: str = Query(..., min_length=1, max_length=300),
    db: AsyncSession = Depends(get_db),
):
    """
    Search Korean legal citations.
    Priority: 1) our own flashcard DB  2) law.go.kr open API (if key set)  3) scrape fallback.
    """
    results: List[DictResult] = []

    # ── 1. Search our own DB ──────────────────────────────────────────────────
    db_results = await _search_db(db, q)
    results.extend(db_results)

    # ── 2. External: law.go.kr (statutes + precedents) ────────────────────────
    if len(results) < 8:
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                ext = await _search_law_go_kr(client, q)
                # Deduplicate by title
                existing_titles = {r.title for r in results}
                results.extend(r for r in ext if r.title not in existing_titles)
        except Exception as exc:
            log.warning("External law search failed: %s", exc)

    return results[:10] if results else [
        DictResult(type="card", title="검색 결과 없음",
                   snippet=f"'{q}'에 대한 법령·판례 정보를 찾을 수 없습니다.")
    ]


# ── DB search ─────────────────────────────────────────────────────────────────

async def _search_db(db: AsyncSession, q: str) -> List[DictResult]:
    """Search legal_basis / case_citation / explanation_core in choices + questions."""
    results: List[DictResult] = []
    term = f"%{q}%"

    # Search choices
    choice_res = await db.execute(
        select(Choice).where(
            or_(
                Choice.legal_basis.ilike(term),
                Choice.case_citation.ilike(term),
                Choice.explanation_core.ilike(term),
                Choice.content.ilike(term),
            )
        ).limit(5)
    )
    for c in choice_res.scalars().all():
        title = c.legal_basis or c.case_citation or c.content[:60]
        snippet = c.explanation_core or c.explanation or c.content
        results.append(DictResult(
            type="card",
            title=title,
            snippet=snippet[:250] if snippet else "",
        ))

    # Search questions
    q_res = await db.execute(
        select(Question).where(
            or_(
                Question.legal_basis.ilike(term),
                Question.case_citation.ilike(term),
                Question.explanation_core.ilike(term),
                Question.overall_explanation.ilike(term),
            )
        ).limit(3)
    )
    for qobj in q_res.scalars().all():
        if qobj.legal_basis or qobj.case_citation:
            title = qobj.legal_basis or qobj.case_citation or ""
            results.append(DictResult(
                type="statute" if qobj.legal_basis else "precedent",
                title=title,
                snippet=(qobj.explanation_core or qobj.overall_explanation or "")[:250],
            ))

    return results


# ── law.go.kr external search ─────────────────────────────────────────────────

async def _search_law_go_kr(client: httpx.AsyncClient, q: str) -> List[DictResult]:
    results: List[DictResult] = []
    results.extend(await _statute_search(client, q))
    results.extend(await _precedent_search(client, q))
    return results


async def _statute_search(client: httpx.AsyncClient, q: str) -> List[DictResult]:
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


async def _precedent_search(client: httpx.AsyncClient, q: str) -> List[DictResult]:
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
