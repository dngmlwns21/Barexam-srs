from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings

# Always resolve .env relative to this file (phase4_api/.env)
_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    # Database
    database_url: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/srs_db"
    )
    database_url_sync: str = (
        "postgresql://postgres:password@localhost:5432/srs_db"
    )

    # JWT
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24   # 24 hours
    refresh_token_expire_days: int = 30

    # CORS
    allowed_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
    ]
    # Comma-separated extra origins injected at runtime (e.g. Cloud Run URL)
    extra_allowed_origins: str = ""

    @property
    def all_allowed_origins(self) -> List[str]:
        origins = list(self.allowed_origins)
        if self.extra_allowed_origins:
            origins.extend(
                o.strip() for o in self.extra_allowed_origins.split(",") if o.strip()
            )
        return origins

    # SM-2 global defaults (overridable per user)
    sm2_hard_interval_minutes: int = 10
    sm2_good_interval_days: int = 1
    sm2_easy_interval_days: int = 3

    debug: bool = False

    class Config:
        env_file = str(_ENV_FILE)
        env_file_encoding = "utf-8"
        extra = "ignore"   # silently ignore unknown keys in .env


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
