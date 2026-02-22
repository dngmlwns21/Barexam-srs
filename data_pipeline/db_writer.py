"""
db_writer.py — Write transformed questions to the SRS PostgreSQL database.
Updated for Phase 2: Uses specific columns for legal_basis, case_citation, etc.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import asyncpg
from dotenv import load_dotenv

from .models import OX_LETTERS, ImportanceGrade, Source, TransformedQuestion

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

log = logging.getLogger(__name__)

# Deterministic UUID namespace
NS = uuid.UUID("12345678-1234-5678-1234-567812345678")

_LETTER_IDX = {l: 101 + i for i, l in enumerate("가나다라마바사아자차카타파하")}

def _q_uuid(raw_id: str) -> str:
    return str(uuid.uuid5(NS, raw_id))

def _choice_number(letter: str) -> int:
    return _LETTER_IDX.get(letter, 199)

def _exam_type(source: Source) -> str:
    return "Korean Bar Exam" if source == Source.BAR_EXAM else "Law School Mock Exam"

async def wipe_tables(conn: asyncpg.Connection) -> None:
    tables = ["user_progress", "review_logs", "study_sessions", "flashcards", "choices", "question_tags", "questions", "subjects"]
    for t in tables:
        await conn.execute(f"TRUNCATE TABLE {t} CASCADE")

async def upsert_subjects(conn: asyncpg.Connection, subject_names: List[str]) -> Dict[str, str]:
    subject_map: Dict[str, str] = {}
    for name in subject_names:
        row = await conn.fetchrow("SELECT id FROM subjects WHERE name = $1", name)
        if row:
            subject_map[name] = str(row["id"])
        else:
            new_id = str(uuid.uuid4())
            await conn.execute("INSERT INTO subjects (id, name, sort_order) VALUES ($1, $2, 0) ON CONFLICT (name) DO NOTHING", new_id, name)
            row = await conn.fetchrow("SELECT id FROM subjects WHERE name = $1", name)
            subject_map[name] = str(row["id"]) if row else new_id
    return subject_map

async def upsert_question(conn: asyncpg.Connection, tq: TransformedQuestion, subject_id: str) -> Tuple[str, bool]:
    q_id = _q_uuid(tq.raw_id)
    exam_type = _exam_type(tq.source)
    source_name = str(tq.exam_session) if tq.exam_session is not None else (f"{tq.year}_{tq.month}" if tq.year else None)
    source_year = tq.year
    
    existing = await conn.fetchrow("SELECT id FROM questions WHERE id = $1", q_id)
    if existing:
        await conn.execute(
            "UPDATE questions SET overall_explanation=$2, tags=$3, is_outdated=$4, needs_revision=$5 WHERE id=$1",
            q_id, tq.overall_explanation, tq.tags, tq.is_outdated, tq.needs_revision
        )
        return q_id, False

    await conn.execute(
        """INSERT INTO questions (id, subject_id, exam_type, source_year, source_name, question_number, stem, correct_choice, overall_explanation, tags, is_outdated, needs_revision)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
        q_id, subject_id, exam_type, source_year, source_name, tq.question_number, tq.stem, tq.correct_choice, tq.overall_explanation, tq.tags, tq.is_outdated, tq.needs_revision
    )
    return q_id, True

async def upsert_ox_choices(conn: asyncpg.Connection, q_id: str, tq: TransformedQuestion) -> Dict[str, str]:
    ox_choice_map: Dict[str, str] = {}
    for stmt in tq.ox_statements:
        c_num = _choice_number(stmt.letter)
        row = await conn.fetchrow("SELECT id FROM choices WHERE question_id=$1 AND choice_number=$2", q_id, c_num)
        
        # In Phase 2, we write directly to the new columns
        params = [
            stmt.statement, stmt.is_correct, stmt.legal_basis, stmt.case_citation, 
            stmt.explanation_core, json.dumps(stmt.keywords), stmt.explanation
        ]
        
        if row:
            c_id = str(row["id"])
            await conn.execute(
                "UPDATE choices SET content=$1, is_correct=$2, legal_basis=$3, case_citation=$4, explanation_core=$5, keywords=$6, explanation=$7 WHERE id=$8",
                *params, c_id
            )
        else:
            c_id = str(uuid.uuid4())
            await conn.execute(
                """INSERT INTO choices (id, question_id, choice_number, content, is_correct, legal_basis, case_citation, explanation_core, keywords, explanation)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                c_id, q_id, c_num, *params
            )
        ox_choice_map[stmt.letter] = c_id
    return ox_choice_map

async def upsert_flashcard(conn: asyncpg.Connection, q_id: str, choice_id: Optional[str], fc_type: str) -> Tuple[str, bool]:
    if fc_type == "question":
        existing = await conn.fetchrow("SELECT id FROM flashcards WHERE question_id=$1 AND choice_id IS NULL", q_id)
    else:
        existing = await conn.fetchrow("SELECT id FROM flashcards WHERE question_id=$1 AND choice_id=$2", q_id, choice_id)

    if existing: return str(existing["id"]), False
    fc_id = str(uuid.uuid4())
    await conn.execute("INSERT INTO flashcards (id, question_id, choice_id, type) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING", fc_id, q_id, choice_id, fc_type)
    return fc_id, True

class SRSWriter:
    def __init__(self, database_url: str):
        self._url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._conn = None

    async def __aenter__(self):
        self._conn = await asyncpg.connect(self._url)
        return self

    async def __aexit__(self, *args):
        if self._conn: await self._conn.close()

    async def write_all(self, questions: List[TransformedQuestion], wipe: bool = False) -> Dict[str, int]:
        conn = self._conn
        if wipe: await wipe_tables(conn)
        user_rows = await conn.fetch("SELECT id FROM users")
        user_ids = [str(r["id"]) for r in user_rows]
        subject_names = sorted({q.subject for q in questions})
        subject_map = await upsert_subjects(conn, subject_names)
        
        stats = {"questions": 0, "flashcards": 0}
        for tq in questions:
            sid = subject_map.get(tq.subject)
            if not sid: continue
            q_id, _ = await upsert_question(conn, tq, sid)
            ox_map = await upsert_ox_choices(conn, q_id, tq)
            for c_id in ox_map.values():
                fc_id, is_new = await upsert_flashcard(conn, q_id, c_id, "choice_ox")
                if is_new:
                    stats["flashcards"] += 1
                    for uid in user_ids:
                        await conn.execute("INSERT INTO user_progress (id, user_id, flashcard_id, ease_factor, interval_days, repetitions, card_state) VALUES ($1, $2, $3, 2.5, 0, 0, 'new') ON CONFLICT DO NOTHING", str(uuid.uuid4()), uid, fc_id)
            stats["questions"] += 1
        return stats
