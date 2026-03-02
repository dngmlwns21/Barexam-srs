"""dictionary.py — Mini Legal Dictionary: DB-first statute/precedent lookup + law.go.kr fallback."""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..models import Choice, LawStatute, LegalPrecedent, Question, Subject

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
    type: str = Query("all", pattern="^(all|statute|precedent)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    Search Korean legal statutes and precedents.
    Priority: 1) law_statutes / legal_precedents DB  2) law.go.kr open API (fallback + cache)
    type: 'all' | 'statute' | 'precedent'
    """
    results: List[DictResult] = []

    # ── 1. DB search (law_statutes / legal_precedents) ────────────────────────
    if type in ("all", "statute"):
        results.extend(await _search_statutes_db(db, q))
    if type in ("all", "precedent"):
        results.extend(await _search_precedents_db(db, q))

    # ── 2. OX 카드에서 법령/판례 근거 검색 ────────────────────────────────────
    # choices 테이블의 legal_basis·case_citation·keywords에서 검색
    if type in ("all", "statute"):
        results.extend(await _search_choices_legal_basis(db, q))
    if type in ("all", "precedent"):
        results.extend(await _search_choices_case_citation(db, q))

    # ── 3. Fallback to law.go.kr if still no results ─────────────────────────
    if not results:
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                if type in ("all", "statute"):
                    ext_statutes = await _statute_search_api(client, q)
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

    # 중복 제목 제거 후 반환
    seen_titles: set = set()
    deduped: List[DictResult] = []
    for r in results:
        key = (r.type, r.title[:40])
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(r)
    return deduped[:10]


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


# ── Choices-based search helpers ─────────────────────────────────────────────

async def _search_choices_legal_basis(db: AsyncSession, q: str) -> List[DictResult]:
    """choices.legal_basis에서 법령 근거를 검색해 DictResult로 반환."""
    term = f"%{q}%"
    rows = await db.execute(
        select(
            Choice.legal_basis,
            Choice.content,
            Subject.name.label("subject_name"),
        )
        .join(Question, Choice.question_id == Question.id)
        .join(Subject, Question.subject_id == Subject.id)
        .where(
            Choice.legal_basis.isnot(None),
            Choice.legal_basis != "",
            Choice.legal_basis.ilike(term),
            Choice.choice_number >= 101,
        )
        .order_by(func.length(Choice.legal_basis).asc())
        .limit(5)
    )
    seen: set = set()
    results: List[DictResult] = []
    for row in rows.all():
        lb = row.legal_basis or ""
        if lb in seen:
            continue
        seen.add(lb)
        results.append(DictResult(
            type="statute",
            title=lb,
            snippet=f"OX 지문 근거 — {(row.content or '')[:100]}",
            subject=row.subject_name,
        ))
    return results


async def _search_choices_case_citation(db: AsyncSession, q: str) -> List[DictResult]:
    """choices.case_citation에서 판례 인용을 검색해 DictResult로 반환."""
    term = f"%{q}%"
    rows = await db.execute(
        select(
            Choice.case_citation,
            Choice.content,
            Subject.name.label("subject_name"),
        )
        .join(Question, Choice.question_id == Question.id)
        .join(Subject, Question.subject_id == Subject.id)
        .where(
            Choice.case_citation.isnot(None),
            Choice.case_citation != "",
            Choice.case_citation.ilike(term),
            Choice.choice_number >= 101,
        )
        .limit(5)
    )
    seen: set = set()
    results: List[DictResult] = []
    for row in rows.all():
        cc = row.case_citation or ""
        if cc in seen:
            continue
        seen.add(cc)
        results.append(DictResult(
            type="precedent",
            title=cc[:80],
            snippet=f"OX 지문 인용 — {(row.content or '')[:100]}",
            subject=row.subject_name,
        ))
    return results


# ── law.go.kr API helpers ─────────────────────────────────────────────────────

def _parse_law_json(data: dict) -> List[DictResult]:
    results: List[DictResult] = []
    for item in (data.get("LawSearch") or {}).get("law") or []:
        name = item.get("법령명한글") or item.get("법령명", "")
        if not name:
            continue
        results.append(DictResult(
            type="statute",
            title=name,
            snippet=f"{item.get('법령구분명', '')} · 시행 {item.get('시행일자', '')}".strip(" ·"),
            url=f"https://www.law.go.kr/법령/{name}",
            date=item.get("시행일자"),
        ))
    return results


def _parse_law_xml(xml_text: str) -> List[DictResult]:
    results: List[DictResult] = []
    try:
        root = ET.fromstring(xml_text)
        for law in root.iter("law"):
            name = (law.findtext("법령명한글") or law.findtext("법령명") or "").strip()
            if not name:
                continue
            results.append(DictResult(
                type="statute",
                title=name,
                snippet=f"{law.findtext('법령구분명') or ''} · 시행 {law.findtext('시행일자') or ''}".strip(" ·"),
                url=f"https://www.law.go.kr/법령/{name}",
                date=law.findtext("시행일자"),
            ))
    except ET.ParseError:
        pass
    return results


def _parse_prec_json(data: dict) -> List[DictResult]:
    results: List[DictResult] = []
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
    return results


def _parse_prec_xml(xml_text: str) -> List[DictResult]:
    results: List[DictResult] = []
    try:
        root = ET.fromstring(xml_text)
        for prec in root.iter("prec"):
            case_name = (prec.findtext("사건명") or "").strip()
            if not case_name:
                continue
            serial = prec.findtext("판례정보일련번호") or ""
            results.append(DictResult(
                type="precedent",
                title=case_name,
                snippet=(prec.findtext("판시사항") or "")[:250],
                url=f"https://www.law.go.kr/판례/{serial}" if serial else None,
                date=prec.findtext("선고일자"),
            ))
    except ET.ParseError:
        pass
    return results


async def _statute_search_api(client: httpx.AsyncClient, q: str) -> List[DictResult]:
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
        if r.status_code != 200:
            return []
        content_type = r.headers.get("content-type", "")
        if "json" in content_type:
            return _parse_law_json(r.json())
        # JSON 파싱 시도 후 실패하면 XML 폴백
        try:
            return _parse_law_json(r.json())
        except Exception:
            return _parse_law_xml(r.text)
    except Exception as exc:
        log.debug("Statute API error: %s", exc)
    return []


async def _precedent_search_api(client: httpx.AsyncClient, q: str) -> List[DictResult]:
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
        if r.status_code != 200:
            return []
        try:
            return _parse_prec_json(r.json())
        except Exception:
            return _parse_prec_xml(r.text)
    except Exception as exc:
        log.debug("Precedent API error: %s", exc)
    return []


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
