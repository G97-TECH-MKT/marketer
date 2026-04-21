"""Persistence layer — async SQLAlchemy over Postgres (asyncpg).

Schema is owned by alembic/versions/001_initial_schema.py. Models here mirror
that schema for ORM access; they are NOT the source of truth for DDL.
"""

from __future__ import annotations

from marketer.db.engine import (
    dispose_engine,
    get_engine,
    get_sessionmaker,
    is_configured,
    session_scope,
)

__all__ = [
    "dispose_engine",
    "get_engine",
    "get_sessionmaker",
    "is_configured",
    "session_scope",
]
