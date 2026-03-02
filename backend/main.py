"""Phase 4 — Production-Ready FastAPI SRS Backend.

Run:
    cd backend
    cp .env.example .env       # fill in DATABASE_URL etc.
    pip install -r requirements.txt
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import sys

# Python 3.8 on Windows: ProactorEventLoop has SSL issues with asyncpg.
# Force SelectorEventLoop for compatibility.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .config import settings
from .database import Base, engine
from .routers import auth, cards, dashboard, dictionary, flashcards, mock_cards, questions, reviews, stats, subjects, tags, users, pipeline, chat
from .scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Tables already exist in DB (applied via SQL migrations).
    # Connection is made lazily on first request.
    start_scheduler()
    yield
    stop_scheduler()
    await engine.dispose()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'"
        )
        return response


limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

app = FastAPI(
    title="감자 팩토리",
    version="1.0.0",
    description="변호사시험 SRS 학습 플랫폼",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.all_allowed_origins,
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
app.include_router(mock_cards.router,  prefix="/api/v1/mock",       tags=["Mock"])
app.include_router(pipeline.router,    prefix="/api/v1/pipeline",   tags=["Pipeline"])
app.include_router(chat.router,        prefix="/api/v1/chat",       tags=["Chat"])
app.include_router(dictionary.router,  prefix="/api/v1/dictionary", tags=["Dictionary"])


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "version": "4.0.0"}


@app.get("/", include_in_schema=False)
async def serve_index():
    return FileResponse("frontend/index.html")


app.mount("/static", StaticFiles(directory="frontend"), name="static")


