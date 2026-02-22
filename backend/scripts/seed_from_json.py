"""
Migrate questions_parsed.json → PostgreSQL (Phase 4 schema).

Usage:
    cd phase4_api
    py -3 scripts/seed_from_json.py

Requires DATABASE_URL_SYNC in .env (psycopg2 format):
    postgresql://user:password@host:5432/dbname
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import os

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL_SYNC")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL_SYNC not set in .env")

DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "questions_parsed.json"
if not DATA_FILE.exists():
    sys.exit(f"ERROR: {DATA_FILE} not found")

EXAM_TYPE_DEFAULT = "Korean Bar Exam"


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_choices(raw) -> dict[int, str]:
    """Return {1: text, 2: text, ...} regardless of input format."""
    if isinstance(raw, dict):
        return {int(k): v for k, v in raw.items()}
    if isinstance(raw, list):
        return {i + 1: v for i, v in enumerate(raw)}
    return {}


def main():
    print(f"Connecting to database…")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    print(f"Loading {DATA_FILE} …")
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    questions = data["questions"]
    print(f"  {len(questions)} questions found")

    # ── 1. Upsert subjects ────────────────────────────────────────────────────
    subject_names = sorted({q["subject"] for q in questions})
    subject_id_map: dict[str, str] = {}

    for name in subject_names:
        cur.execute(
            """
            INSERT INTO subjects (id, name, sort_order)
            VALUES (%s, %s, 0)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (str(uuid.uuid4()), name),
        )
        row = cur.fetchone()
        subject_id_map[name] = str(row[0])

    print(f"  Subjects upserted: {subject_names}")

    # ── 2. Insert questions + choices + flashcards ────────────────────────────
    inserted_q  = 0
    skipped_q   = 0

    for q in questions:
        q_id_str = q["id"]

        # Check if already exists (idempotent re-runs)
        cur.execute("SELECT id FROM questions WHERE id = %s", (q_id_str,))
        if cur.fetchone():
            skipped_q += 1
            continue

        subject_id = subject_id_map[q["subject"]]
        choices_raw = parse_choices(q.get("choices") or {})
        correct_choice = int(q.get("answer") or 1)
        source_year = q.get("year")
        source_name = q.get("exam_session")

        # Insert question
        cur.execute(
            """
            INSERT INTO questions
              (id, subject_id, exam_type, source_year, source_name,
               question_number, stem, correct_choice, explanation,
               tags, is_outdated, needs_revision,
               total_attempts, correct_attempts)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                q_id_str,
                subject_id,
                EXAM_TYPE_DEFAULT,
                source_year,
                source_name,
                q.get("question_number"),
                q["question_text"],
                correct_choice,
                None,           # explanation — empty for now
                [],             # tags array
                False,
                False,
                0,
                0,
            ),
        )

        # Insert choices
        choice_ids: dict[int, str] = {}
        for num, text in sorted(choices_raw.items()):
            c_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO choices (id, question_id, choice_number, content, is_correct)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (c_id, q_id_str, num, text, num == correct_choice),
            )
            choice_ids[num] = c_id

        # Insert question-level flashcard
        cur.execute(
            """
            INSERT INTO flashcards (id, question_id, choice_id, type)
            VALUES (%s, %s, NULL, 'question')
            ON CONFLICT DO NOTHING
            """,
            (str(uuid.uuid4()), q_id_str),
        )

        inserted_q += 1
        if inserted_q % 500 == 0:
            conn.commit()
            print(f"  … {inserted_q} questions committed")

    conn.commit()
    print(f"\nDone.")
    print(f"  Inserted : {inserted_q}")
    print(f"  Skipped  : {skipped_q} (already in DB)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
