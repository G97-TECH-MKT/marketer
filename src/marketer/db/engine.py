"""Async SQLAlchemy engine + session factory.

DATABASE_URL is read from env (via Settings). Empty → is_configured() is False
and callers should skip persistence (degraded mode).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from marketer.config import load_settings
from marketer.pg_url import (
    coerce_asyncpg_query,
    coerce_plain_postgresql_to_asyncpg_scheme,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def is_configured() -> bool:
    return bool(load_settings().database_url)


def _build_engine() -> AsyncEngine:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set; persistence disabled")
    url = coerce_asyncpg_query(
        coerce_plain_postgresql_to_asyncpg_scheme(settings.database_url)
    )
    if settings.db_use_null_pool:
        return create_async_engine(url, poolclass=NullPool)
    return create_async_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async session with commit on success, rollback on exception."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
