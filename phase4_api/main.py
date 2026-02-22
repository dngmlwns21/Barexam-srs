"""Phase 4 — Production-Ready FastAPI SRS Backend.

Run:
    cd phase4_api
    cp .env.example .env       # fill in DATABASE_URL etc.
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import sys

# Python 3.8 on Windows: ProactorEventLoop has SSL issues with asyncpg.
# Force SelectorEventLoop for compatibility.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import settings
from .database import Base, engine
from .routers import auth, cards, dashboard, flashcards, questions, reviews, stats, subjects, tags, users

FRONTEND_DIR = Path(__file__).parent.parent / "phase3_web" / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Tables already exist in Supabase (applied via 001_initial.sql).
    # Connection is made lazily on first request.
    yield
    await engine.dispose()


app = FastAPI(
    title="Korean Bar Exam SRS — Phase 4",
    version="4.0.0",
    description=(
        "Production-ready Spaced-Repetition API with SM-2, "
        "choice-level O/X splitting, user auth, and peer statistics."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,        prefix="/api/v1/auth",       tags=["Auth"])
app.include_router(users.router,       prefix="/api/v1/users",      tags=["Users"])
app.include_router(subjects.router,    prefix="/api/v1/subjects",   tags=["Subjects"])
app.include_router(questions.router,   prefix="/api/v1/questions",  tags=["Questions"])
app.include_router(flashcards.router,  prefix="/api/v1/flashcards", tags=["Flashcards"])
app.include_router(reviews.router,     prefix="/api/v1/reviews",    tags=["Reviews"])
app.include_router(stats.router,       prefix="/api/v1/stats",      tags=["Stats"])
app.include_router(tags.router,        prefix="/api/v1/tags",       tags=["Tags"])
app.include_router(dashboard.router,   prefix="/api/v1/dashboard",  tags=["Dashboard"])
app.include_router(cards.router,       prefix="/api/v1/cards",      tags=["Cards"])


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "version": "4.0.0"}


# ── Frontend static files ──────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/", include_in_schema=False)
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")
