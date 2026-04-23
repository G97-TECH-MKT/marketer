"""Alembic environment.

Reads DATABASE_URL from the process env (and .env if present). Migrations run
with a sync driver (psycopg3) even when the app uses asyncpg — we strip the
+asyncpg suffix if present so the same DATABASE_URL works for both.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

from marketer.pg_url import normalize_sync_psycopg_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _load_dotenv_if_present() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _resolve_database_url() -> str:
    _load_dotenv_if_present()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Export it or add to .env before running alembic.\n"
            "Example: postgresql+asyncpg://marketer:password@localhost:5432/marketer"
        )
    return normalize_sync_psycopg_url(url)


# No declarative metadata — migrations are hand-written DDL.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_resolve_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
