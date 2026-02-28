"""
seed_laws.py — Seed law_statutes and legal_precedents tables.

Usage:
    py -3.8 backend/scripts/seed_laws.py

Fetches 8 major bar exam statutes from law.go.kr and seeds the DB.
Also seeds legal_precedents from choices.case_citation values.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parents[2]))

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

LAW_API_KEY = os.getenv("LAW_API_KEY", "openapi")

# Major bar exam statutes
BAR_EXAM_LAWS = [
    ("헌법",       "민공법"),
    ("민법",       "민사법"),
    ("형법",       "형사법"),
    ("민사소송법", "민사법"),
    ("형사소송법", "형사법"),
    ("상법",       "민사법"),
    ("행정기본법", "민공법"),
    ("변호사법",   "민공법"),
]


async def fetch_statute(client: httpx.AsyncClient, name: str) -> dict | None:
    params = {
        "OC": LAW_API_KEY,
        "target": "law",
        "type": "JSON",
        "query": name,
        "display": 3,
        "page": 1,
    }
    try:
        r = await client.get("https://www.law.go.kr/DRF/lawSearch.do", params=params, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        laws = (data.get("LawSearch") or {}).get("law") or []
        # Find exact match first
        for item in laws:
            if item.get("법령명한글", "") == name:
                return item
        return laws[0] if laws else None
    except Exception as e:
        print(f"  [warn] fetch_statute({name}): {e}")
        return None


async def seed_statutes(db: AsyncSession) -> None:
    print("=== Seeding law_statutes ===")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for law_name, subject in BAR_EXAM_LAWS:
            # Check if already exists
            existing = await db.execute(
                text("SELECT id FROM law_statutes WHERE name = :n"), {"n": law_name}
            )
            if existing.fetchone():
                print(f"  [skip] {law_name} already exists")
                continue

            item = await fetch_statute(client, law_name)
            if item:
                law_id = item.get("법령ID") or law_name.replace(" ", "_")
                effective_date = item.get("시행일자", "")
                category = item.get("법령구분명", "")
                law_url = f"https://www.law.go.kr/법령/{law_name}"
                await db.execute(
                    text("""
                        INSERT INTO law_statutes (law_id, name, category, subject, effective_date, law_url)
                        VALUES (:law_id, :name, :category, :subject, :effective_date, :law_url)
                        ON CONFLICT (law_id) DO NOTHING
                    """),
                    {
                        "law_id": law_id,
                        "name": law_name,
                        "category": category,
                        "subject": subject,
                        "effective_date": effective_date,
                        "law_url": law_url,
                    },
                )
                print(f"  [ok] {law_name} (시행: {effective_date})")
            else:
                # Insert with minimal data so it still shows up
                await db.execute(
                    text("""
                        INSERT INTO law_statutes (law_id, name, subject, law_url)
                        VALUES (:law_id, :name, :subject, :law_url)
                        ON CONFLICT (law_id) DO NOTHING
                    """),
                    {
                        "law_id": law_name.replace(" ", "_"),
                        "name": law_name,
                        "subject": subject,
                        "law_url": f"https://www.law.go.kr/법령/{law_name}",
                    },
                )
                print(f"  [fallback] {law_name} inserted without API data")

    await db.commit()
    print("law_statutes seeding done.\n")


async def fetch_precedent(client: httpx.AsyncClient, case_number: str) -> dict | None:
    params = {
        "OC": LAW_API_KEY,
        "target": "prec",
        "type": "JSON",
        "query": case_number,
        "display": 3,
        "page": 1,
    }
    try:
        r = await client.get("https://www.law.go.kr/DRF/lawSearch.do", params=params, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        precs = (data.get("PrecSearch") or {}).get("prec") or []
        return precs[0] if precs else None
    except Exception as e:
        print(f"  [warn] fetch_precedent({case_number}): {e}")
        return None


async def seed_precedents(db: AsyncSession) -> None:
    print("=== Seeding legal_precedents from choices.case_citation ===")
    # Get distinct case citations from choices
    result = await db.execute(
        text("""
            SELECT DISTINCT case_citation
            FROM choices
            WHERE case_citation IS NOT NULL AND case_citation != ''
            LIMIT 50
        """)
    )
    citations = [row[0] for row in result.fetchall()]
    print(f"Found {len(citations)} unique case citations in choices table.")

    if not citations:
        print("  No citations found — skipping precedent seed.")
        return

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for citation in citations:
            # Check if already exists
            existing = await db.execute(
                text("SELECT id FROM legal_precedents WHERE case_number = :cn"),
                {"cn": citation},
            )
            if existing.fetchone():
                print(f"  [skip] {citation}")
                continue

            item = await fetch_precedent(client, citation)
            if item:
                serial = item.get("판례정보일련번호", "")
                await db.execute(
                    text("""
                        INSERT INTO legal_precedents
                            (case_number, case_name, court, decision_date, verdict_summary, holding, serial_number, source_url)
                        VALUES
                            (:case_number, :case_name, :court, :decision_date, :verdict_summary, :holding, :serial_number, :source_url)
                        ON CONFLICT (case_number) DO NOTHING
                    """),
                    {
                        "case_number": citation,
                        "case_name": item.get("사건명", ""),
                        "court": item.get("법원명", "대법원"),
                        "decision_date": item.get("선고일자", ""),
                        "verdict_summary": item.get("판결요지", "")[:2000] if item.get("판결요지") else None,
                        "holding": item.get("판시사항", "")[:2000] if item.get("판시사항") else None,
                        "serial_number": serial,
                        "source_url": f"https://www.law.go.kr/판례/{serial}" if serial else None,
                    },
                )
                print(f"  [ok] {citation}")
            else:
                # Insert minimal record so searches still surface citation
                await db.execute(
                    text("""
                        INSERT INTO legal_precedents (case_number)
                        VALUES (:case_number)
                        ON CONFLICT (case_number) DO NOTHING
                    """),
                    {"case_number": citation},
                )
                print(f"  [fallback] {citation} inserted without API data")

    await db.commit()
    print("legal_precedents seeding done.\n")


async def main() -> None:
    async with AsyncSessionLocal() as db:
        await seed_statutes(db)
        await seed_precedents(db)
    await engine.dispose()
    print("=== All done ===")


if __name__ == "__main__":
    asyncio.run(main())
