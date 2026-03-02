"""
dict_crawler.py — Bulk-populate law_statutes and legal_precedents from law.go.kr

Usage:
    python -m data_pipeline.dict_crawler --mode laws     # Crawl major laws
    python -m data_pipeline.dict_crawler --mode prec     # Crawl precedents
    python -m data_pipeline.dict_crawler --mode all      # Both
    python -m data_pipeline.dict_crawler --mode auto     # Extract citations from existing questions in DB

The script uses the law.go.kr Open API (OC=openapi for anonymous access).
Get a free API key at https://open.law.go.kr/LSO/main.do for higher rate limits.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

log = logging.getLogger(__name__)

LAW_API_KEY  = os.getenv("LAW_API_KEY", "openapi")
DATABASE_URL = os.getenv("DATABASE_URL", "")

LAW_API_BASE = "https://www.law.go.kr/DRF/lawSearch.do"

# ── Major bar-exam-relevant laws ─────────────────────────────────────────────

MAJOR_LAWS: List[str] = [
    # 기본 6법
    "민법", "형법", "상법", "민사소송법", "형사소송법",
    # 공법
    "헌법재판소법", "행정소송법", "행정심판법", "행정절차법", "행정기본법",
    "국가배상법", "공익사업을 위한 토지 등의 취득 및 보상에 관한 법률",
    # 민사 특별법
    "민사집행법", "채무자 회생 및 파산에 관한 법률", "부동산등기법",
    "가족관계의 등록 등에 관한 법률", "공탁법",
    # 형사 특별법
    "특정경제범죄 가중처벌 등에 관한 법률",
    "성폭력범죄의 처벌 등에 관한 특례법",
    "아동·청소년의 성보호에 관한 법률",
    "형사소송규칙",
    # 상사 특별법
    "주식회사 등의 외부감사에 관한 법률",
    "자본시장과 금융투자업에 관한 법률",
    "어음법", "수표법",
    # 국제사법
    "국제사법", "국제민사사법공조법",
    # 조세
    "국세기본법",
    # 법조윤리
    "변호사법", "법무사법", "공증인법",
    # 기타 자주 출제
    "근로기준법", "부정경쟁방지 및 영업비밀보호에 관한 법률",
    "정보통신망 이용촉진 및 정보보호 등에 관한 법률",
]

# ── Common precedent search terms ────────────────────────────────────────────

PREC_QUERIES: List[str] = [
    # 민법
    "손해배상", "불법행위", "채무불이행", "이행불능", "이행지체",
    "매매계약", "임대차", "하자담보책임", "부당이득", "사해행위",
    "유치권", "저당권", "소유권이전", "선의취득", "점유취득시효",
    "채권양도", "상계", "연대채무", "보증채무", "대리",
    "의사표시", "법률행위", "소멸시효", "취소권",
    # 형법
    "정당방위", "긴급피난", "위법성조각", "착오", "공범",
    "교사범", "방조범", "사기죄", "횡령죄", "배임죄",
    "절도죄", "강도죄", "상해죄", "살인죄", "명예훼손",
    # 형사소송법
    "위법수집증거", "전문증거", "자백", "진술거부권",
    "공소시효", "공소장변경", "증거능력", "공판",
    # 헌법
    "기본권", "평등원칙", "과잉금지", "비례원칙",
    "재산권", "직업의 자유", "표현의 자유",
    # 행정법
    "행정처분", "취소소송", "행정심판", "손실보상",
    "국가배상", "행정행위", "하자", "행정강제",
    # 상법
    "이사의 책임", "주주총회", "주식양도", "이사회",
    "어음", "수표", "보험계약", "상행위",
]

# ── Subject tag mapping for laws ─────────────────────────────────────────────

_LAW_SUBJECT_MAP = {
    "민법": "민법", "민사소송법": "민사소송법", "민사집행법": "민사소송법",
    "형법": "형법", "형사소송법": "형사소송법", "형사소송규칙": "형사소송법",
    "상법": "상법", "어음법": "상법", "수표법": "상법",
    "헌법재판소법": "헌법", "행정소송법": "행정법", "행정심판법": "행정법",
    "행정절차법": "행정법", "행정기본법": "행정법", "국가배상법": "행정법",
    "국제사법": "국제법", "변호사법": "법조윤리", "법무사법": "법조윤리",
}


def _guess_subject(name: str) -> Optional[str]:
    for key, subj in _LAW_SUBJECT_MAP.items():
        if key in name:
            return subj
    return None


# ── law.go.kr API helpers ─────────────────────────────────────────────────────

async def _fetch_laws(client: httpx.AsyncClient, query: str, page: int = 1, display: int = 20) -> List[Dict]:
    params = {
        "OC": LAW_API_KEY,
        "target": "law",
        "type": "JSON",
        "query": query,
        "display": display,
        "page": page,
    }
    try:
        r = await client.get(LAW_API_BASE, params=params, timeout=10.0)
        if r.status_code != 200:
            return []
        data = r.json()
        items = (data.get("LawSearch") or {}).get("law") or []
        if isinstance(items, dict):   # single result returned as dict, not list
            items = [items]
        return items
    except Exception as exc:
        log.warning("Law API error (query=%s): %s", query, exc)
        return []


async def _fetch_precs(client: httpx.AsyncClient, query: str, page: int = 1, display: int = 20) -> List[Dict]:
    params = {
        "OC": LAW_API_KEY,
        "target": "prec",
        "type": "JSON",
        "query": query,
        "display": display,
        "page": page,
    }
    try:
        r = await client.get(LAW_API_BASE, params=params, timeout=10.0)
        if r.status_code != 200:
            return []
        data = r.json()
        items = (data.get("PrecSearch") or {}).get("prec") or []
        if isinstance(items, dict):
            items = [items]
        return items
    except Exception as exc:
        log.warning("Prec API error (query=%s): %s", query, exc)
        return []


# ── DB upsert helpers ─────────────────────────────────────────────────────────

async def _upsert_law(conn: asyncpg.Connection, item: Dict) -> bool:
    name = item.get("법령명한글", "").strip()
    if not name:
        return False
    law_id = name.replace(" ", "_")
    cat    = item.get("법령구분명", "")
    eff    = item.get("시행일자", "")
    url    = f"https://www.law.go.kr/법령/{name}"
    subj   = _guess_subject(name)

    try:
        await conn.execute("""
            INSERT INTO law_statutes (id, law_id, name, category, subject, effective_date, law_url)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (law_id) DO UPDATE
                SET name           = EXCLUDED.name,
                    category       = EXCLUDED.category,
                    subject        = COALESCE(EXCLUDED.subject, law_statutes.subject),
                    effective_date = EXCLUDED.effective_date,
                    law_url        = EXCLUDED.law_url
        """, str(uuid.uuid4()), law_id, name, cat, subj, eff, url)
        return True
    except Exception as exc:
        log.debug("Law upsert failed (%s): %s", name, exc)
        return False


async def _upsert_prec(conn: asyncpg.Connection, item: Dict) -> bool:
    case_name  = (item.get("사건명") or "").strip()
    holding    = (item.get("판시사항") or "").strip()
    serial     = (item.get("판례정보일련번호") or "").strip()
    date_str   = (item.get("선고일자") or "").strip()

    # Extract case number from case_name (e.g. "대법원 2017다1234 판결" → "2017다1234")
    case_num_m = re.search(r"(\d{4}[가-힣]+\d+)", case_name)
    case_number = case_num_m.group(1) if case_num_m else (serial or case_name[:50])
    if not case_number:
        return False

    url = f"https://www.law.go.kr/판례/{serial}" if serial else None

    try:
        await conn.execute("""
            INSERT INTO legal_precedents (id, case_number, case_name, decision_date, holding, serial_number, source_url)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (case_number) DO UPDATE
                SET case_name    = EXCLUDED.case_name,
                    decision_date= EXCLUDED.decision_date,
                    holding      = COALESCE(EXCLUDED.holding, legal_precedents.holding),
                    serial_number= COALESCE(EXCLUDED.serial_number, legal_precedents.serial_number),
                    source_url   = COALESCE(EXCLUDED.source_url, legal_precedents.source_url)
        """, str(uuid.uuid4()), case_number, case_name, date_str, holding or None, serial or None, url)
        return True
    except Exception as exc:
        log.debug("Prec upsert failed (%s): %s", case_number, exc)
        return False


# ── Crawl modes ───────────────────────────────────────────────────────────────

async def crawl_laws(conn: asyncpg.Connection, queries: List[str]) -> int:
    """Fetch and upsert statutes for each query term."""
    total = 0
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for q in queries:
            log.info("  Laws: querying '%s' ...", q)
            for page in range(1, 4):   # up to 3 pages × 20 = 60 per term
                items = await _fetch_laws(client, q, page=page, display=20)
                if not items:
                    break
                for item in items:
                    if await _upsert_law(conn, item):
                        total += 1
                if len(items) < 20:
                    break
                await asyncio.sleep(0.3)
            await asyncio.sleep(0.5)
    return total


async def crawl_precs(conn: asyncpg.Connection, queries: List[str]) -> int:
    """Fetch and upsert precedents for each query term."""
    total = 0
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for q in queries:
            log.info("  Precs: querying '%s' ...", q)
            for page in range(1, 4):
                items = await _fetch_precs(client, q, page=page, display=20)
                if not items:
                    break
                for item in items:
                    if await _upsert_prec(conn, item):
                        total += 1
                if len(items) < 20:
                    break
                await asyncio.sleep(0.3)
            await asyncio.sleep(0.5)
    return total


async def crawl_from_questions(conn: asyncpg.Connection) -> Tuple[int, int]:
    """
    Extract all unique citations from existing question data in the DB
    and look them up via law.go.kr API.

    Returns (laws_added, precs_added).
    """
    rows = await conn.fetch("""
        SELECT DISTINCT
            unnest(ARRAY[q.legal_basis, q.case_citation,
                         c.legal_basis, c.case_citation]) AS citation
        FROM questions q
        LEFT JOIN choices c ON c.question_id = q.id
        WHERE q.legal_basis IS NOT NULL
           OR q.case_citation IS NOT NULL
           OR c.legal_basis IS NOT NULL
           OR c.case_citation IS NOT NULL
    """)
    citations = set()
    for r in rows:
        cit = (r["citation"] or "").strip()
        if cit:
            citations.add(cit)

    log.info("Found %d unique citations in questions/choices", len(citations))

    # Extract case numbers (2017다1234 style) and law names from citations
    case_num_re = re.compile(r"\d{4}[가-힣]+\d+")
    law_name_re = re.compile(r"(민법|형법|상법|헌법|행정|민사소송|형사소송|국제|상법)[^\s\)]+")

    prec_queries: List[str] = []
    law_queries:  List[str] = []

    for cit in citations:
        # Case numbers → precedent search
        for m in case_num_re.findall(cit):
            prec_queries.append(m)
        # Law names → law search
        for m in law_name_re.findall(cit):
            law_queries.append(m[:20])

    # Deduplicate
    prec_queries = list(set(prec_queries))[:200]
    law_queries  = list(set(law_queries))[:100]

    laws_added  = await crawl_laws(conn, law_queries)  if law_queries  else 0
    precs_added = await crawl_precs(conn, prec_queries) if prec_queries else 0
    return laws_added, precs_added


# ── Main ──────────────────────────────────────────────────────────────────────

async def _main(mode: str) -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL not set in environment / .env")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if mode in ("laws", "all"):
            log.info("=== Crawling major laws (%d queries) ===", len(MAJOR_LAWS))
            n = await crawl_laws(conn, MAJOR_LAWS)
            log.info("Laws inserted/updated: %d", n)

        if mode in ("prec", "all"):
            log.info("=== Crawling precedents (%d queries) ===", len(PREC_QUERIES))
            n = await crawl_precs(conn, PREC_QUERIES)
            log.info("Precedents inserted/updated: %d", n)

        if mode == "auto":
            log.info("=== Auto-crawling citations from existing questions ===")
            la, pa = await crawl_from_questions(conn)
            log.info("Laws: %d  Precedents: %d", la, pa)

        # Summary
        law_count  = await conn.fetchval("SELECT COUNT(*) FROM law_statutes")
        prec_count = await conn.fetchval("SELECT COUNT(*) FROM legal_precedents")
        log.info("DB totals → law_statutes: %d  legal_precedents: %d", law_count, prec_count)

    finally:
        await conn.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Populate legal dictionary from law.go.kr")
    parser.add_argument(
        "--mode",
        choices=["laws", "prec", "all", "auto"],
        default="all",
        help="laws: crawl statutes, prec: crawl precedents, all: both, auto: extract from questions",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.mode))
