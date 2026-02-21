from __future__ import annotations

import ssl
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


def _clean_url(url: str) -> str:
    """Remove ssl/sslmode query params — passed via connect_args instead."""
    parsed = urlparse(url)
    params = {k: v for k, v in parse_qs(parsed.query).items()
              if k not in ("ssl", "sslmode")}
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


_ssl_ctx = ssl.create_default_context()

engine = create_async_engine(
    _clean_url(settings.database_url),
    echo=settings.debug,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    connect_args={"ssl": _ssl_ctx},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass
