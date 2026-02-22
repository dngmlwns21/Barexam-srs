"""
db_writer.py — Write transformed questions to the SRS PostgreSQL database.

Schema compatibility:
  subjects        (id, name, sort_order)
  questions       (id, subject_id, exam_type, source_year, source_name,
                   question_number, stem, correct_choice, explanation,
                   tags, is_outdated, needs_revision, total_attempts, correct_attempts)
  choices         (id, question_id, choice_number, content, is_correct)
  flashcards      (id, question_id, choice_id, type)   type: 'question'|'choice_ox'
  user_progress   (id, user_id, flashcard_id, ease_factor, interval_days,
                   repetitions, card_state)

Usage:
    python -m data_pipeline.db_writer \
        --input data/transformed.json \
        [--wipe]
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

# Deterministic UUID namespace (same as seed_from_enhanced.py)
NS = uuid.UUID("12345678-1234-5678-1234-567812345678")

# Sub-statement letter → choice_number (101, 102, …)
_LETTER_IDX = {l: 101 + i for i, l in enumerate("가나다라마바사아자차카타파하")}


def _q_uuid(raw_id: str) -> str:
    return str(uuid.uuid5(NS, raw_id))


def _choice_number(letter: str) -> int:
    return _LETTER_IDX.get(letter, 199)


def _exam_type(source: Source) -> str:
    return "Korean Bar Exam" if source == Source.BAR_EXAM else "Law School Mock Exam"


# ── Wipe tables ───────────────────────────────────────────────────────────────

async def wipe_tables(conn: asyncpg.Connection) -> None:
    tables = [
        "user_progress", "review_logs", "study_sessions",
        "flashcards", "choices", "question_tags",
        "questions", "subjects",
    ]
    log.info("Wiping tables …")
    for t in tables:
        await conn.execute(f"TRUNCATE TABLE {t} CASCADE")
        log.info("  TRUNCATED %s", t)


# ── Subject upsert ────────────────────────────────────────────────────────────

async def upsert_subjects(
    conn: asyncpg.Connection,
    subject_names: List[str],
) -> Dict[str, str]:
    """Return {subject_name: uuid_str} map."""
    subject_map: Dict[str, str] = {}
    for name in subject_names:
        row = await conn.fetchrow("SELECT id FROM subjects WHERE name = $1", name)
        if row:
            subject_map[name] = str(row["id"])
        else:
            new_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO subjects (id, name, sort_order) VALUES ($1, $2, 0)"
                " ON CONFLICT (name) DO NOTHING",
                new_id, name,
            )
            # Re-fetch in case of concurrent insert
            row = await conn.fetchrow("SELECT id FROM subjects WHERE name = $1", name)
            subject_map[name] = str(row["id"]) if row else new_id
    return subject_map


# ── Question upsert ───────────────────────────────────────────────────────────

async def upsert_question(
    conn: asyncpg.Connection,
    tq: TransformedQuestion,
    subject_id: str,
) -> Tuple[str, bool]:
    """
    Upsert question row.
    Returns (question_uuid, is_new).
    """
    q_id = _q_uuid(tq.raw_id)
    exam_type   = _exam_type(tq.source)
    source_name = (
        str(tq.exam_session) if tq.exam_session is not None
        else (f"{tq.year}_{tq.month}" if tq.year else None)
    )
    source_year = tq.year or (
        {1:2012,2:2013,3:2014,4:2015,5:2016,6:2017,7:2018,
         8:2019,9:2020,10:2021,11:2022,12:2023,13:2024,14:2025}
        .get(tq.exam_session or 0)
    )

    existing = await conn.fetchrow(
        "SELECT id FROM questions WHERE id = $1", q_id
    )
    if existing:
        await conn.execute(
            """
            UPDATE questions SET
              explanation    = $2,
              tags           = $3,
              is_outdated    = $4,
              needs_revision = $5,
              source_year    = COALESCE(source_year, $6)
            WHERE id = $1
            """,
            q_id,
            tq.overall_explanation,
            tq.tags,
            tq.is_outdated,
            tq.needs_revision,
            source_year,
        )
        return q_id, False

    await conn.execute(
        """
        INSERT INTO questions
          (id, subject_id, exam_type, source_year, source_name,
           question_number, stem, correct_choice, explanation,
           tags, is_outdated, needs_revision, total_attempts, correct_attempts)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,0,0)
        """,
        q_id, subject_id, exam_type, source_year, source_name,
        tq.question_number, tq.stem, tq.correct_choice,
        tq.overall_explanation, tq.tags,
        tq.is_outdated, tq.needs_revision,
    )
    return q_id, True


# ── Choice upsert ─────────────────────────────────────────────────────────────

async def upsert_choices(
    conn: asyncpg.Connection,
    q_id: str,
    tq: TransformedQuestion,
) -> Dict[int, str]:
    """
    Upsert MCQ choices (1–5) and return {choice_number: choice_uuid}.
    """
    choice_id_map: Dict[int, str] = {}

    for num, text in sorted(tq.choices.items()):
        row = await conn.fetchrow(
            "SELECT id FROM choices WHERE question_id = $1 AND choice_number = $2",
            q_id, num,
        )
        if row:
            choice_id_map[num] = str(row["id"])
            await conn.execute(
                "UPDATE choices SET content=$1, is_correct=$2 WHERE id=$3",
                text, num == tq.correct_choice, str(row["id"]),
            )
        else:
            c_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO choices (id, question_id, choice_number, content, is_correct)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (question_id, choice_number) DO NOTHING
                """,
                c_id, q_id, num, text, num == tq.correct_choice,
            )
            choice_id_map[num] = c_id

    return choice_id_map


# ── OX choice upsert ──────────────────────────────────────────────────────────

async def upsert_ox_choices(
    conn: asyncpg.Connection,
    q_id: str,
    tq: TransformedQuestion,
) -> Dict[str, str]:
    """
    Upsert sub-statement choices (choice_number 101+).
    Returns {letter: choice_uuid}.
    """
    ox_choice_map: Dict[str, str] = {}

    for stmt in tq.ox_statements:
        c_num = _choice_number(stmt.letter)

        # Build enriched content: statement + importance tag + provision
        content_parts = [stmt.statement]
        if stmt.legal_provision:
            content_parts.append(f"[조문: {stmt.legal_provision}]")
        if stmt.precedent:
            content_parts.append(f"[판례: {stmt.precedent}]")
        content = "\n".join(content_parts)

        # Build explanation with all metadata
        exp_parts = [stmt.explanation]
        if stmt.is_revised and stmt.revision_note:
            exp_parts.append(f"⚠️ 개정 주의: {stmt.revision_note}")
        explanation = "\n".join(exp_parts)

        row = await conn.fetchrow(
            "SELECT id FROM choices WHERE question_id=$1 AND choice_number=$2",
            q_id, c_num,
        )
        if row:
            c_id = str(row["id"])
            await conn.execute(
                "UPDATE choices SET content=$1, is_correct=$2 WHERE id=$3",
                content, stmt.is_correct, c_id,
            )
        else:
            c_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO choices
                  (id, question_id, choice_number, content, is_correct)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (question_id, choice_number) DO NOTHING
                """,
                c_id, q_id, c_num, content, stmt.is_correct,
            )

        ox_choice_map[stmt.letter] = c_id

    return ox_choice_map


# ── Flashcard upsert ──────────────────────────────────────────────────────────

async def upsert_flashcard(
    conn: asyncpg.Connection,
    q_id: str,
    choice_id: Optional[str],
    fc_type: str,  # 'question' | 'choice_ox'
) -> Tuple[str, bool]:
    """
    Upsert one flashcard row.
    Returns (flashcard_uuid, is_new).
    """
    if fc_type == "question":
        existing = await conn.fetchrow(
            "SELECT id FROM flashcards WHERE question_id=$1 AND choice_id IS NULL",
            q_id,
        )
    else:
        existing = await conn.fetchrow(
            "SELECT id FROM flashcards WHERE question_id=$1 AND choice_id=$2",
            q_id, choice_id,
        )

    if existing:
        return str(existing["id"]), False

    fc_id  = str(uuid.uuid4())
    result = await conn.execute(
        """
        INSERT INTO flashcards (id, question_id, choice_id, type)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT DO NOTHING
        """,
        fc_id, q_id, choice_id, fc_type,
    )
    is_new = result != "INSERT 0 0"
    return fc_id, is_new


# ── User progress ─────────────────────────────────────────────────────────────

async def create_user_progress(
    conn: asyncpg.Connection,
    flashcard_ids: List[str],
    user_ids: List[str],
) -> int:
    """Create user_progress rows for new flashcards × all users."""
    count = 0
    for fc_id in flashcard_ids:
        for uid in user_ids:
            up_id = str(uuid.uuid4())
            result = await conn.execute(
                """
                INSERT INTO user_progress
                  (id, user_id, flashcard_id,
                   ease_factor, interval_days, repetitions, card_state)
                VALUES ($1, $2, $3, 2.50, 0, 0, 'new')
                ON CONFLICT (user_id, flashcard_id) DO NOTHING
                """,
                up_id, uid, fc_id,
            )
            if result != "INSERT 0 0":
                count += 1
    return count


# ── SRSWriter main class ───────────────────────────────────────────────────────

class SRSWriter:
    """High-level writer: TransformedQuestion list → PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url.replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        self._conn: Optional[asyncpg.Connection] = None

    async def __aenter__(self):
        self._conn = await asyncpg.connect(self._url)
        return self

    async def __aexit__(self, *args):
        if self._conn:
            await self._conn.close()

    async def write_all(
        self,
        questions: List[TransformedQuestion],
        wipe: bool = False,
    ) -> Dict[str, int]:
        """
        Write all transformed questions to DB.
        Returns stats dict.
        """
        conn = self._conn
        assert conn

        if wipe:
            await wipe_tables(conn)

        # Fetch all user IDs
        user_rows = await conn.fetch("SELECT id FROM users")
        user_ids  = [str(r["id"]) for r in user_rows]
        log.info("Users found: %d", len(user_ids))

        # Collect unique subjects
        subject_names = sorted({q.subject for q in questions})
        subject_map   = await upsert_subjects(conn, subject_names)
        log.info("Subjects: %s", subject_names)

        stats = dict(
            questions_new=0, questions_updated=0,
            flashcards_new=0, user_progress_new=0,
        )
        new_fc_ids: List[str] = []

        total = len(questions)
        for i, tq in enumerate(questions, 1):
            if i % 50 == 0 or i == total:
                log.info("  %d/%d …", i, total)

            subject_id = subject_map.get(tq.subject, "")
            if not subject_id:
                log.warning("Unknown subject '%s' — skipping", tq.subject)
                continue

            try:
                async with conn.transaction():
                    # 1. Question
                    q_id, is_new_q = await upsert_question(conn, tq, subject_id)
                    if is_new_q:
                        stats["questions_new"] += 1
                    else:
                        stats["questions_updated"] += 1

                    # 2. MCQ choices (1–5)
                    await upsert_choices(conn, q_id, tq)

                    # 3. 'question' type flashcard
                    fc_id, is_new_fc = await upsert_flashcard(conn, q_id, None, "question")
                    if is_new_fc:
                        new_fc_ids.append(fc_id)
                        stats["flashcards_new"] += 1

                    # 4. OX choices + 'choice_ox' flashcards
                    if tq.ox_statements:
                        ox_map = await upsert_ox_choices(conn, q_id, tq)
                        for letter, c_id in ox_map.items():
                            fc_id, is_new_fc = await upsert_flashcard(
                                conn, q_id, c_id, "choice_ox"
                            )
                            if is_new_fc:
                                new_fc_ids.append(fc_id)
                                stats["flashcards_new"] += 1

            except Exception as exc:
                log.error("Failed on %s: %s", tq.raw_id, exc)
                continue

        # 5. User progress for new flashcards
        if new_fc_ids and user_ids:
            up_count = await create_user_progress(conn, new_fc_ids, user_ids)
            stats["user_progress_new"] = up_count

        return stats


# ── CLI ───────────────────────────────────────────────────────────────────────

async def _main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL not set")

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Input file not found: {input_path}")

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("questions", [])
    questions = [TransformedQuestion.model_validate(q) for q in items]

    if args.limit:
        questions = questions[: args.limit]

    log.info("Writing %d questions to DB …", len(questions))

    async with SRSWriter(db_url) as writer:
        stats = await writer.write_all(questions, wipe=args.wipe)

    print("\n── Result ────────────────────────────────")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write transformed questions to SRS DB")
    parser.add_argument("--input", required=True)
    parser.add_argument("--wipe",  action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    asyncio.run(_main(parser.parse_args()))
