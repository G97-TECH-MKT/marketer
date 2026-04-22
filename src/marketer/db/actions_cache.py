"""In-memory cache for the action_types catalog.

Reads from Postgres on first use and refreshes every 60s. The cache lets the
ingest path validate `action_code` without hitting the DB on every request,
and gives the system a short window of resilience if Postgres briefly hiccups.

When DATABASE_URL is unset (degraded mode), the cache no-ops and `get()`
returns None for every code — callers should treat that as "DB-driven
validation is off" and decide their own fallback.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from marketer.db.engine import is_configured, session_scope
from marketer.db.models import ActionType

logger = logging.getLogger("marketer.actions_cache")

TTL_SECONDS = 60.0


@dataclass(frozen=True)
class ActionRecord:
    """Plain snapshot of an action_types row, decoupled from the SQLAlchemy session."""

    code: str
    surface: str
    mode: str
    prompt_overlay: str
    requires_prior_post: bool
    is_enabled: bool


_cache: dict[str, ActionRecord] = {}
_expires_at: float = 0.0
_lock = asyncio.Lock()


async def _refresh_locked() -> None:
    """Repopulate the cache from DB. Caller MUST hold `_lock`."""
    global _cache, _expires_at
    async with session_scope() as session:
        rows = (await session.execute(select(ActionType))).scalars().all()
        _cache = {
            row.code: ActionRecord(
                code=row.code,
                surface=row.surface,
                mode=row.mode,
                prompt_overlay=row.prompt_overlay,
                requires_prior_post=row.requires_prior_post,
                is_enabled=row.is_enabled,
            )
            for row in rows
        }
        _expires_at = time.monotonic() + TTL_SECONDS
    logger.info(
        '"actions_cache_refreshed count=%d enabled=%d"',
        len(_cache),
        sum(1 for r in _cache.values() if r.is_enabled),
    )


async def _ensure_fresh() -> None:
    """Refresh if expired, with a double-checked lock to avoid stampedes."""
    if not is_configured():
        return
    if _cache and time.monotonic() < _expires_at:
        return
    async with _lock:
        if _cache and time.monotonic() < _expires_at:
            return
        await _refresh_locked()


async def refresh() -> None:
    """Force a refresh now (admin/test helper)."""
    if not is_configured():
        return
    async with _lock:
        await _refresh_locked()


async def get(code: str) -> ActionRecord | None:
    """Return the cached record for `code`, or None if unknown/uncached."""
    await _ensure_fresh()
    return _cache.get(code)


async def enabled_codes() -> set[str]:
    """Set of action codes currently enabled in the catalog."""
    await _ensure_fresh()
    return {code for code, rec in _cache.items() if rec.is_enabled}


def invalidate() -> None:
    """Drop the cache so the next get/enabled_codes triggers a fresh DB read.

    Intended for tests after toggling action_types.is_enabled.
    """
    global _cache, _expires_at
    _cache = {}
    _expires_at = 0.0
