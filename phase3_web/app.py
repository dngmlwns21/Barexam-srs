"""Phase 3 – FastAPI SRS backend for Korean Bar Exam.

Run:
    py -3 -m pip install -r requirements.txt
    py -3 -m uvicorn app:app --reload --port 8000
    # then open http://localhost:8000
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).resolve().parent.parent
DATA_FILE  = ROOT / "data" / "questions_parsed.json"
DB_FILE    = ROOT / "data" / "srs.db"
STATIC_DIR = Path(__file__).parent / "frontend"


# ── Database ──────────────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cards (
                id            TEXT PRIMARY KEY,
                subject       TEXT    NOT NULL,
                interval      INTEGER NOT NULL DEFAULT 0,
                repetition    INTEGER NOT NULL DEFAULT 0,
                ease_factor   REAL    NOT NULL DEFAULT 2.5,
                due_date      TEXT,
                last_reviewed TEXT,
                created_at    TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id     TEXT    NOT NULL,
                rating      INTEGER NOT NULL,
                reviewed_at TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cards_subject  ON cards(subject);
            CREATE INDEX IF NOT EXISTS idx_cards_due      ON cards(due_date);
            CREATE INDEX IF NOT EXISTS idx_reviews_card   ON reviews(card_id);
        """)


# ── Question store ────────────────────────────────────────────────────────────
_questions: dict[str, dict] = {}


def init_questions():
    global _questions
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    _questions = {q["id"]: q for q in data["questions"]}

    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        existing = {r[0] for r in conn.execute("SELECT id FROM cards")}
        new_rows = [
            (q["id"], q["subject"], now)
            for q in data["questions"]
            if q["id"] not in existing
        ]
        if new_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO cards (id, subject, created_at) VALUES (?,?,?)",
                new_rows,
            )


# ── SM-2 ──────────────────────────────────────────────────────────────────────
def sm2(interval: int, repetition: int, ef: float, rating: int):
    """Returns (new_interval, new_repetition, new_ef, due_date_str).

    rating 0-5:  0-2 = fail, 3 = hard-pass, 4 = good, 5 = easy
    """
    if rating < 3:
        new_interval = 1
        new_rep      = 0
    else:
        if repetition == 0:
            new_interval = 1
        elif repetition == 1:
            new_interval = 6
        else:
            new_interval = max(1, round(interval * ef))
        new_rep = repetition + 1

    new_ef = max(1.3, ef + 0.1 - (5 - rating) * (0.08 + (5 - rating) * 0.02))
    due    = (date.today() + timedelta(days=new_interval)).isoformat()
    return new_interval, new_rep, new_ef, due


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    init_questions()
    yield


app = FastAPI(title="Korean Bar Exam SRS", lifespan=lifespan)


# ── Pydantic ──────────────────────────────────────────────────────────────────
class ReviewIn(BaseModel):
    rating: int  # 0-5


# ── API ───────────────────────────────────────────────────────────────────────
@app.get("/api/subjects")
def api_subjects():
    counts: dict[str, int] = {}
    for q in _questions.values():
        counts[q["subject"]] = counts.get(q["subject"], 0) + 1
    return {"subjects": sorted(counts.items(), key=lambda x: -x[1])}


@app.get("/api/stats")
def api_stats(subject: Optional[str] = None):
    today = date.today().isoformat()
    with get_db() as conn:
        def cnt(sql, params=()):
            return conn.execute(sql, params).fetchone()[0]

        if subject:
            total    = cnt("SELECT COUNT(*) FROM cards WHERE subject=?",                      (subject,))
            new_c    = cnt("SELECT COUNT(*) FROM cards WHERE subject=? AND due_date IS NULL", (subject,))
            due_c    = cnt("SELECT COUNT(*) FROM cards WHERE subject=? AND due_date <= ?",    (subject, today))
            done_c   = cnt("SELECT COUNT(*) FROM cards WHERE subject=? AND last_reviewed >= ?", (subject, today))
        else:
            total    = cnt("SELECT COUNT(*) FROM cards")
            new_c    = cnt("SELECT COUNT(*) FROM cards WHERE due_date IS NULL")
            due_c    = cnt("SELECT COUNT(*) FROM cards WHERE due_date <= ?",      (today,))
            done_c   = cnt("SELECT COUNT(*) FROM cards WHERE last_reviewed >= ?", (today,))

    return {"total": total, "new": new_c, "due": due_c, "reviewed_today": done_c}


@app.get("/api/due")
def api_due(subject: Optional[str] = None):
    today = date.today().isoformat()
    with get_db() as conn:
        if subject:
            row = conn.execute(
                "SELECT id FROM cards WHERE subject=? AND due_date <= ? ORDER BY due_date LIMIT 1",
                (subject, today),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT id FROM cards WHERE subject=? AND due_date IS NULL ORDER BY ROWID LIMIT 1",
                    (subject,),
                ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM cards WHERE due_date <= ? ORDER BY due_date LIMIT 1",
                (today,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT id FROM cards WHERE due_date IS NULL ORDER BY ROWID LIMIT 1",
                ).fetchone()

    if not row:
        return {"card": None, "finished": True}

    q = _questions.get(row[0])
    if not q:
        return {"card": None, "finished": True}

    return {
        "finished": False,
        "card": {
            "id":              q["id"],
            "subject":         q["subject"],
            "exam_session":    q.get("exam_session"),
            "question_number": q.get("question_number"),
            "question_text":   q["question_text"],
            "choices":         q.get("choices") or {},
        },
    }


@app.get("/api/answer/{qid}")
def api_answer(qid: str):
    q = _questions.get(qid)
    if not q:
        raise HTTPException(404, "Not found")
    return {"answer": q.get("answer"), "explanation": q.get("explanation")}


@app.post("/api/review/{qid}")
def api_review(qid: str, body: ReviewIn):
    if not 0 <= body.rating <= 5:
        raise HTTPException(422, "rating must be 0-5")
    q = _questions.get(qid)
    if not q:
        raise HTTPException(404, "Not found")

    today = date.today().isoformat()
    now   = datetime.utcnow().isoformat()

    with get_db() as conn:
        row = conn.execute(
            "SELECT interval, repetition, ease_factor FROM cards WHERE id=?", (qid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Card not in DB")

        ni, nr, nef, due = sm2(row["interval"], row["repetition"], row["ease_factor"], body.rating)

        conn.execute(
            """UPDATE cards
               SET interval=?, repetition=?, ease_factor=?, due_date=?, last_reviewed=?
               WHERE id=?""",
            (ni, nr, nef, due, today, qid),
        )
        conn.execute(
            "INSERT INTO reviews (card_id, rating, reviewed_at) VALUES (?,?,?)",
            (qid, body.rating, now),
        )

    return {
        "next_due":     due,
        "interval_days": ni,
        "answer":       q.get("answer"),
        "explanation":  q.get("explanation"),
    }


# ── Static & SPA fallback ─────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    return FileResponse(str(STATIC_DIR / "index.html"))
