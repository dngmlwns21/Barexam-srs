"""pipeline.py — Admin endpoints for data pipeline operations."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..models import LawStatute, LegalPrecedent

log = logging.getLogger(__name__)
router = APIRouter()

LAW_API_BASE = "https://www.law.go.kr/DRF/lawSearch.do"

_LAW_SUBJECT_MAP: Dict[str, str] = {
    "민법": "민법", "민사소송법": "민사소송법", "민사집행법": "민사소송법",
    "형법": "형법", "형사소송법": "형사소송법", "형사소송규칙": "형사소송법",
    "상법": "상법", "어음법": "상법", "수표법": "상법",
    "헌법재판소법": "헌법", "행정소송법": "행정법", "행정심판법": "행정법",
    "행정절차법": "행정법", "행정기본법": "행정법", "국가배상법": "행정법",
    "국제사법": "국제법", "국제민사사법공조법": "국제법",
    "변호사법": "법조윤리", "법무사법": "법조윤리", "공증인법": "법조윤리",
}

MAJOR_LAWS: List[str] = [
    "민법", "형법", "상법", "민사소송법", "형사소송법",
    "헌법재판소법", "행정소송법", "행정심판법", "행정절차법", "행정기본법",
    "국가배상법", "민사집행법", "채무자 회생 및 파산에 관한 법률",
    "부동산등기법", "형사소송규칙",
    "특정경제범죄 가중처벌 등에 관한 법률",
    "성폭력범죄의 처벌 등에 관한 특례법",
    "주식회사 등의 외부감사에 관한 법률",
    "자본시장과 금융투자업에 관한 법률",
    "어음법", "수표법", "국제사법", "변호사법", "근로기준법",
    "주택임대차보호법", "신탁법", "국세기본법",
]

PREC_QUERIES: List[str] = [
    "손해배상", "불법행위", "채무불이행", "매매계약", "임대차",
    "부당이득", "사해행위", "유치권", "저당권", "선의취득",
    "채권양도", "소멸시효", "정당방위", "착오", "공범",
    "사기죄", "횡령죄", "배임죄", "위법수집증거", "전문증거",
    "공소시효", "기본권", "과잉금지", "재산권", "행정처분",
    "취소소송", "손실보상", "국가배상", "이사의 책임", "주주총회",
]


def _guess_subject(name: str) -> Optional[str]:
    for key, subj in _LAW_SUBJECT_MAP.items():
        if key in name:
            return subj
    return None


async def _crawl_laws(
    db: AsyncSession, client: httpx.AsyncClient, oc_key: str
) -> int:
    total = 0
    for query in MAJOR_LAWS:
        for page in range(1, 4):
            try:
                r = await client.get(LAW_API_BASE, params={
                    "OC": oc_key, "target": "law", "type": "JSON",
                    "query": query, "display": 20, "page": page,
                }, timeout=10.0)
                if r.status_code != 200:
                    break
                items = (r.json().get("LawSearch") or {}).get("law") or []
                if isinstance(items, dict):
                    items = [items]
                if not items:
                    break
                for item in items:
                    name = (item.get("법령명한글") or item.get("법령명") or "").strip()
                    if not name:
                        continue
                    law_id = name.replace(" ", "_")
                    existing = await db.execute(
                        select(LawStatute).where(LawStatute.law_id == law_id).limit(1)
                    )
                    if not existing.scalars().first():
                        db.add(LawStatute(
                            id=uuid.uuid4(),
                            law_id=law_id,
                            name=name,
                            category=item.get("법령구분명"),
                            subject=_guess_subject(name),
                            effective_date=item.get("시행일자"),
                            law_url=f"https://www.law.go.kr/법령/{name}",
                        ))
                        total += 1
                if len(items) < 20:
                    break
                await asyncio.sleep(0.3)
            except Exception as exc:
                log.warning("Law crawl error (query=%s page=%d): %s", query, page, exc)
                break
        await asyncio.sleep(0.5)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
    return total


async def _crawl_precs(
    db: AsyncSession, client: httpx.AsyncClient, oc_key: str
) -> int:
    total = 0
    case_num_re = re.compile(r"(\d{4}[가-힣]+\d+)")
    for query in PREC_QUERIES:
        for page in range(1, 3):
            try:
                r = await client.get(LAW_API_BASE, params={
                    "OC": oc_key, "target": "prec", "type": "JSON",
                    "query": query, "display": 20, "page": page,
                }, timeout=10.0)
                if r.status_code != 200:
                    break
                items = (r.json().get("PrecSearch") or {}).get("prec") or []
                if isinstance(items, dict):
                    items = [items]
                if not items:
                    break
                for item in items:
                    case_name = (item.get("사건명") or "").strip()
                    holding = (item.get("판시사항") or "").strip()
                    serial = (item.get("판례정보일련번호") or "").strip()
                    date_str = (item.get("선고일자") or "").strip()
                    m = case_num_re.search(case_name)
                    case_number = m.group(1) if m else (serial or case_name[:50])
                    if not case_number:
                        continue
                    existing = await db.execute(
                        select(LegalPrecedent)
                        .where(LegalPrecedent.case_number == case_number).limit(1)
                    )
                    if not existing.scalars().first():
                        db.add(LegalPrecedent(
                            id=uuid.uuid4(),
                            case_number=case_number,
                            case_name=case_name,
                            decision_date=date_str,
                            holding=holding or None,
                            source_url=f"https://www.law.go.kr/판례/{serial}" if serial else None,
                        ))
                        total += 1
                if len(items) < 20:
                    break
                await asyncio.sleep(0.3)
            except Exception as exc:
                log.warning("Prec crawl error (query=%s page=%d): %s", query, page, exc)
                break
        await asyncio.sleep(0.5)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
    return total


async def _do_crawl(db: AsyncSession, oc_key: str) -> None:
    log.info("Dict crawl started (OC=%s)", oc_key)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        laws = await _crawl_laws(db, client, oc_key)
        precs = await _crawl_precs(db, client, oc_key)
    log.info("Dict crawl done — laws_added=%d precs_added=%d", laws, precs)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/run", status_code=202)
async def run_pipeline(background_tasks: BackgroundTasks):
    """Triggers a full data pipeline run (placeholder)."""
    return {"message": "Pipeline run triggered. Check server logs for progress."}


@router.post("/crawl-dict", status_code=202)
async def crawl_dict(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Crawl law.go.kr for statutes and precedents and populate the DB.
    Uses LAW_API_KEY env var (OC key registered with law.go.kr).
    Runs asynchronously in the background.
    """
    oc_key = os.getenv("LAW_API_KEY", "openapi")
    background_tasks.add_task(_do_crawl, db, oc_key)
    return {
        "message": "Dictionary crawl started in background.",
        "oc_key_preview": oc_key[:6] + "***",
        "law_queries": len(MAJOR_LAWS),
        "prec_queries": len(PREC_QUERIES),
    }
