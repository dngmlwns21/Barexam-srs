"""
Populate law_statutes and legal_precedents from existing choices.legal_basis
and choices.case_citation data (no external API needed).

Python 3.8 compatible — uses Optional[str] instead of str | None.
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from typing import Dict, List, Optional, Set, Tuple

import asyncpg

sys.stdout.reconfigure(encoding='utf-8')

DB_URL = (
    "postgresql://neondb_owner:npg_8bkXAjRLr2IJ"
    "@ep-young-violet-aiv96fxy-pooler.c-4.us-east-1.aws.neon.tech"
    "/neondb?sslmode=require"
)

# ── regex ──────────────────────────────────────────────────────────────────────
_ARTICLE_RE  = re.compile(r'\s*(제\s*\d+[^\s,·;、]+|\d+\s*조[^\s,·;、]*)')
_CASE_NUM_RE = re.compile(r'\d{4}[가-힣]+\d+')

# Strip trailing article / clause references to get the bare law name
_STRIP_SUFFIXES = re.compile(
    r'\s*(제\s*\d+.{0,30}|'         # 제750조 이하...
    r'\d+\s*조.{0,20}|'             # 750조...
    r',\s*동법.{0,30}|'             # , 동법 ...
    r'\s+단서|'
    r'\s+본문|'
    r'\(.*?\))$'
)

_LAW_SUBJECT_MAP: Dict[str, str] = {
    "민법": "민법", "민사소송법": "민사소송법", "민사집행법": "민사소송법",
    "형법": "형법", "형사소송법": "형사소송법", "형사소송규칙": "형사소송법",
    "상법": "상법", "어음법": "상법", "수표법": "상법",
    "헌법재판소법": "헌법", "행정소송법": "행정법", "행정심판법": "행정법",
    "행정절차법": "행정법", "행정기본법": "행정법", "국가배상법": "행정법",
    "공익사업": "행정법",
    "국제사법": "국제법", "국제민사사법공조법": "국제법",
    "변호사법": "법조윤리", "법무사법": "법조윤리", "공증인법": "법조윤리",
    "채무자 회생": "민법", "부동산등기법": "민법", "부동산 실권리자": "민법",
    "주택임대차": "민법", "상가건물 임대차": "민법",
    "집합건물": "민법", "신탁법": "민법", "가족관계": "민법",
    "국세기본법": "행정법", "근로기준법": "행정법",
    "특정경제범죄": "형법", "성폭력범죄": "형법",
    "자본시장": "상법",
}


def guess_subject(name: str) -> Optional[str]:
    for key, subj in _LAW_SUBJECT_MAP.items():
        if key in name:
            return subj
    return None


def extract_law_name(lb: str) -> Optional[str]:
    """Return bare law name from a legal_basis string, or None if not parseable."""
    lb = lb.strip()
    if not lb:
        return None

    # If the string is very long it's likely a full paragraph, not a citation
    if len(lb) > 80:
        return None

    # Remove parenthetical remarks
    lb = re.sub(r'\(.*?\)', '', lb)

    # Apply suffix stripping (article refs)
    name = _STRIP_SUFFIXES.sub('', lb).strip()

    # If still too long it's not a law name
    if len(name) > 50:
        return None

    # Must end with "법" or "규칙" or "령" or "예" (Korean law name convention)
    if not re.search(r'[법칙령예규]$', name):
        # Could still be a valid short abbreviation like "민법"
        if len(name) < 3:
            return None

    # Must have at least 2 Korean characters
    if len(re.findall(r'[가-힣]', name)) < 2:
        return None

    return name if name else None


def extract_case_numbers(cc: str) -> List[str]:
    """Extract all case number patterns like 2017다1234 from a citation string."""
    return _CASE_NUM_RE.findall(cc)



async def main():
    conn = await asyncpg.connect(DB_URL)
    try:
        # ── 1. Extract law names from choices.legal_basis ──────────────────────
        print("Fetching legal_basis values ...", flush=True)
        lb_rows = await conn.fetch(
            "SELECT DISTINCT legal_basis FROM choices "
            "WHERE legal_basis IS NOT NULL AND legal_basis != '' AND choice_number >= 101"
        )
        print(f"  Unique legal_basis strings: {len(lb_rows)}", flush=True)

        law_names: Set[str] = set()
        for r in lb_rows:
            raw = r["legal_basis"] or ""
            # Some legal_basis may contain multiple laws separated by comma or semicolon
            parts = re.split(r'[,;·/]', raw)
            for part in parts:
                name = extract_law_name(part.strip())
                if name:
                    law_names.add(name)

        print(f"  Unique law names extracted: {len(law_names)}", flush=True)

        # Batch upsert laws
        law_rows = [
            (str(uuid.uuid4()), name.replace(" ", "_"), name, guess_subject(name),
             "https://www.law.go.kr/법령/" + name)
            for name in sorted(law_names)
        ]
        await conn.executemany(
            """
            INSERT INTO law_statutes (id, law_id, name, subject, law_url)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (law_id) DO UPDATE
                SET subject = COALESCE(EXCLUDED.subject, law_statutes.subject),
                    law_url = EXCLUDED.law_url
            """,
            law_rows
        )
        print(f"  law_statutes upserted: {len(law_rows)}", flush=True)

        # ── 2. Extract case numbers from choices.case_citation ─────────────────
        print("Fetching case_citation values ...", flush=True)
        cc_rows = await conn.fetch(
            "SELECT DISTINCT case_citation FROM choices "
            "WHERE case_citation IS NOT NULL AND case_citation != '' AND choice_number >= 101"
        )
        print(f"  Unique case_citation strings: {len(cc_rows)}", flush=True)

        case_nums: Set[str] = set()
        for r in cc_rows:
            raw = r["case_citation"] or ""
            for cn in extract_case_numbers(raw):
                case_nums.add(cn)

        print(f"  Unique case numbers extracted: {len(case_nums)}", flush=True)

        # Batch upsert precedents
        prec_rows = [
            (str(uuid.uuid4()), cn)
            for cn in sorted(case_nums)
        ]
        await conn.executemany(
            "INSERT INTO legal_precedents (id, case_number) VALUES ($1, $2) "
            "ON CONFLICT (case_number) DO NOTHING",
            prec_rows
        )
        print(f"  legal_precedents upserted: {len(prec_rows)}", flush=True)

        # ── 3. Summary ─────────────────────────────────────────────────────────
        law_total  = await conn.fetchval("SELECT COUNT(*) FROM law_statutes")
        prec_total = await conn.fetchval("SELECT COUNT(*) FROM legal_precedents")
        print(f"\nDB totals → law_statutes: {law_total}, legal_precedents: {prec_total}", flush=True)

    finally:
        await conn.close()


asyncio.run(main())
