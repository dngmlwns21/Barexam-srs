"""
seed_from_enhanced.py — Phase 2: Seed enriched question data into PostgreSQL

Reads  : data/questions_enhanced.json  (output of batch_process_exams.py)
Updates: PostgreSQL (Neon) via asyncpg

What this script does:
  1. UPSERT subjects
  2. For each question:
       • INSERT new questions (skip if already exists)
       • UPDATE explanation / tags / is_outdated / needs_revision on existing rows
       • INSERT regular MCQ choices (1-5) if not yet present
       • INSERT "question" flashcard if missing
  3. For box-type questions (is_box_type=True, ox_statements present):
       • INSERT sub-statement choices  (choice_number 101, 102, 103 …)
       • INSERT "choice_ox" flashcards for each sub-statement choice
  4. CREATE user_progress rows for every (user, new_flashcard) pair

Box-type choice numbering:
  Regular MCQ choices : 1 – 5
  Sub-statement "가"  : 101
  Sub-statement "나"  : 102
  Sub-statement "다"  : 103  (and so on)
  This avoids collision with the unique constraint (question_id, choice_number).

Usage:
  cd toyproject
  py -3 seed_from_enhanced.py              # seed all
  py -3 seed_from_enhanced.py --limit 200  # first 200 only (test)

Requirements:
  pip install asyncpg python-dotenv
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

try:
    import asyncpg
except ImportError:
    sys.exit("ERROR: run:  pip install asyncpg")

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent
DATA        = ROOT / "data"
INPUT_FILE  = DATA / "questions_enhanced.json"

load_dotenv(ROOT / "phase4_api" / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL not set in phase4_api/.env")

# asyncpg needs postgresql:// not postgresql+asyncpg://
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

NS = uuid.UUID("12345678-1234-5678-1234-567812345678")  # namespace for uuid5

# FIX C-4: Map 변호사시험 session number → exam year
# 제N회 변호사시험 was held in year EXAM_SESSION_YEAR[N].
EXAM_SESSION_YEAR: dict[int, int] = {
    1: 2012, 2: 2013, 3: 2014, 4: 2015,  5: 2016,
    6: 2017, 7: 2018, 8: 2019, 9: 2020, 10: 2021,
    11: 2022, 12: 2023, 13: 2024,
}

# Sub-statement letter → choice_number offset (101, 102, …)
_GA_IDX  = {l: 101 + i for i, l in enumerate("가나다라마바사아자차카타파하")}
_BOX_IDX = {l: 101 + i for i, l in enumerate("ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ")}

def stmt_choice_number(letter: str) -> int:
    return _GA_IDX.get(letter) or _BOX_IDX.get(letter) or 199


def q_uuid(original_id: str) -> str:
    """Deterministic UUID from the original string ID like 'bar_10_법조윤리_001'."""
    return str(uuid.uuid5(NS, original_id))


def parse_choices(raw) -> Dict[int, str]:
    if isinstance(raw, dict):
        return {int(k): v for k, v in raw.items()}
    if isinstance(raw, list):
        return {i + 1: v for i, v in enumerate(raw)}
    return {}


# ── Main ──────────────────────────────────────────────────────────────────────
async def main(limit: Optional[int], wipe: bool = False) -> None:
    if not INPUT_FILE.exists():
        sys.exit(
            f"ERROR: {INPUT_FILE} not found.\n"
            f"Run batch_process_exams.py first."
        )

    print(f"Loading {INPUT_FILE} …")
    data      = json.loads(INPUT_FILE.read_text("utf-8"))
    questions = data["questions"]
    if limit:
        questions = questions[:limit]
        print(f"  --limit: processing first {limit} questions")
    print(f"  {len(questions)} questions to seed")

    print(f"Connecting to database …")
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        await _seed(conn, questions, wipe=wipe)
    finally:
        await conn.close()


async def _wipe_tables(conn: asyncpg.Connection) -> None:
    tables = ["user_progress", "review_logs", "study_sessions",
              "flashcards", "choices", "question_tags", "questions", "subjects"]
    print("  Wiping tables …")
    for t in tables:
        await conn.execute(f"TRUNCATE TABLE {t} CASCADE")
        print(f"    TRUNCATED {t}")


async def _seed(conn: asyncpg.Connection, questions: list, wipe: bool = False) -> None:
    if wipe:
        await _wipe_tables(conn)
    # ── 1. Upsert subjects ────────────────────────────────────────────────────
    subject_names = sorted({q["subject"] for q in questions})
    subject_map: Dict[str, str] = {}   # name → UUID str

    for name in subject_names:
        row = await conn.fetchrow(
            "SELECT id FROM subjects WHERE name = $1", name
        )
        if row:
            subject_map[name] = str(row["id"])
        else:
            new_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO subjects (id, name, sort_order) VALUES ($1, $2, 0)",
                new_id, name,
            )
            subject_map[name] = new_id

    print(f"  Subjects ready: {subject_names}")

    # ── 2. Fetch all existing users (for user_progress) ───────────────────────
    user_rows = await conn.fetch("SELECT id FROM users")
    user_ids  = [str(r["id"]) for r in user_rows]
    print(f"  Users found: {len(user_ids)}")

    # ── 3. Process questions ──────────────────────────────────────────────────
    inserted_q   = 0
    updated_q    = 0
    inserted_fc  = 0   # new flashcards (need user_progress rows)
    new_fc_ids: List[str] = []

    total = len(questions)
    for idx, q in enumerate(questions, 1):
        if idx % 200 == 0 or idx == total:
            print(f"  … {idx}/{total} questions processed")

        q_id_str   = q_uuid(q["id"])  # deterministic UUID
        subject_id = subject_map[q["subject"]]

        choices_raw    = parse_choices(q.get("choices") or {})
        correct_choice = int(q.get("answer") or 1)
        # FIX C-4: derive year from session number when JSON year field is null
        source_year    = q.get("year") or EXAM_SESSION_YEAR.get(q.get("exam_session"))
        source_name    = str(q["exam_session"]) if q.get("exam_session") is not None else None
        explanation    = q.get("explanation")
        tags           = q.get("tags") or []
        is_outdated    = bool(q.get("is_outdated", False))
        needs_revision = bool(q.get("needs_revision", False))
        is_box         = bool(q.get("is_box_type", False))
        ox_stmts       = q.get("ox_statements") or []

        # ── Check if question already exists ─────────────────────────────────
        existing = await conn.fetchrow(
            "SELECT id FROM questions WHERE id = $1", q_id_str
        )

        if existing:
            # Update enriched fields + backfill source_year if it was null
            await conn.execute(
                """
                UPDATE questions
                SET explanation    = $2,
                    tags           = $3,
                    is_outdated    = $4,
                    needs_revision = $5,
                    source_year    = COALESCE(source_year, $6)
                WHERE id = $1
                """,
                q_id_str,
                explanation,
                tags,
                is_outdated,
                needs_revision,
                source_year,
            )
            updated_q += 1
        else:
            # Insert new question
            await conn.execute(
                """
                INSERT INTO questions
                  (id, subject_id, exam_type, source_year, source_name,
                   question_number, stem, correct_choice, explanation,
                   tags, is_outdated, needs_revision,
                   total_attempts, correct_attempts)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                q_id_str,
                subject_id,
                q.get("exam_type") or "Korean Bar Exam",
                source_year,
                source_name,
                q.get("question_number"),
                q["question_text"],
                correct_choice,
                explanation,
                tags,
                is_outdated,
                needs_revision,
                0, 0,
            )
            inserted_q += 1

            # Insert regular MCQ choices (1-5)
            for num, text in sorted(choices_raw.items()):
                c_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO choices (id, question_id, choice_number, content, is_correct)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (question_id, choice_number) DO NOTHING
                    """,
                    c_id, q_id_str, num, text, num == correct_choice,
                )

            # Insert "question" type flashcard
            fc_id = str(uuid.uuid4())
            result = await conn.execute(
                """
                INSERT INTO flashcards (id, question_id, choice_id, type)
                VALUES ($1, $2, NULL, 'question')
                ON CONFLICT DO NOTHING
                """,
                fc_id, q_id_str,
            )
            if result != "INSERT 0 0":
                new_fc_ids.append(fc_id)
                inserted_fc += 1

        # ── Box-type: insert sub-statement choices + choice_ox flashcards ────
        if is_box and ox_stmts:
            for stmt in ox_stmts:
                letter     = stmt.get("letter", "")
                text       = (stmt.get("text") or "").strip()
                is_correct = bool(stmt.get("is_correct", False))
                c_num      = stmt_choice_number(letter)

                if not text:
                    continue  # skip empty extractions

                # Check if sub-statement choice already exists
                existing_c = await conn.fetchrow(
                    "SELECT id FROM choices WHERE question_id=$1 AND choice_number=$2",
                    q_id_str, c_num,
                )

                if existing_c:
                    c_id = str(existing_c["id"])
                    # Update text/is_correct in case it changed
                    await conn.execute(
                        "UPDATE choices SET content=$1, is_correct=$2 WHERE id=$3",
                        text, is_correct, c_id,
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
                        c_id, q_id_str, c_num, text, is_correct,
                    )

                # Insert choice_ox flashcard (idempotent)
                fc_id = str(uuid.uuid4())
                result = await conn.execute(
                    """
                    INSERT INTO flashcards (id, question_id, choice_id, type)
                    VALUES ($1, $2, $3, 'choice_ox')
                    ON CONFLICT DO NOTHING
                    """,
                    fc_id, q_id_str, c_id,
                )
                if result != "INSERT 0 0":
                    new_fc_ids.append(fc_id)
                    inserted_fc += 1

    print(f"\n  Questions - inserted: {inserted_q}, updated: {updated_q}")
    print(f"  Flashcards created:  {inserted_fc}")

    # ── 4. Create user_progress for all new flashcards ────────────────────────
    if new_fc_ids and user_ids:
        print(f"  Creating user_progress for {len(new_fc_ids)} new flashcards × {len(user_ids)} users …")
        up_count = 0
        for fc_id in new_fc_ids:
            for uid in user_ids:
                up_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO user_progress
                      (id, user_id, flashcard_id,
                       ease_factor, interval_days, repetitions)
                    VALUES ($1, $2, $3, 2.50, 0, 0)
                    ON CONFLICT (user_id, flashcard_id) DO NOTHING
                    """,
                    up_id, uid, fc_id,
                )
                up_count += 1
        print(f"  user_progress rows created: {up_count}")

    print("\nSeeding complete.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed questions_enhanced.json into PostgreSQL"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only first N questions (for testing)"
    )
    parser.add_argument(
        "--wipe", action="store_true",
        help="Truncate all tables before seeding"
    )
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.wipe))
