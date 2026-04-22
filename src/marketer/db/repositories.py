"""Persistence operations.

Stateless async functions; each takes an AsyncSession and leaves transaction
boundaries to the caller (via session_scope()).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from marketer.db.models import ActionType, Job, RawBrief, Strategy, User


async def upsert_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    brand: dict[str, Any] | None = None,
) -> User:
    """Ensure a users row for this account_uuid. Brand only applied on insert."""
    stmt = (
        pg_insert(User)
        .values(id=user_id, brand=brand or {})
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.execute(stmt)
    row = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    return row


async def insert_raw_brief(
    session: AsyncSession,
    *,
    user_id: UUID,
    router_task_id: UUID,
    action_code: str,
    envelope: dict[str, Any],
    router_job_id: UUID | None = None,
    router_correlation_id: str | None = None,
) -> RawBrief:
    """Insert a raw_briefs row. If router_task_id already exists (ROUTER retry
    or dev replay), returns the existing row."""
    stmt = (
        pg_insert(RawBrief)
        .values(
            user_id=user_id,
            router_task_id=router_task_id,
            router_job_id=router_job_id,
            router_correlation_id=router_correlation_id,
            action_code=action_code,
            envelope=envelope,
        )
        .on_conflict_do_nothing(index_elements=["router_task_id"])
    )
    await session.execute(stmt)
    row = (
        await session.execute(
            select(RawBrief).where(RawBrief.router_task_id == router_task_id)
        )
    ).scalar_one()
    return row


async def get_action_type(session: AsyncSession, code: str) -> ActionType | None:
    return (
        await session.execute(select(ActionType).where(ActionType.code == code))
    ).scalar_one_or_none()


async def get_active_strategy(session: AsyncSession, user_id: UUID) -> Strategy | None:
    stmt = select(Strategy).where(
        Strategy.user_id == user_id, Strategy.is_active.is_(True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_strategy(
    session: AsyncSession,
    *,
    user_id: UUID,
    brand_intelligence: dict[str, Any],
    version: int = 1,
) -> Strategy:
    row = Strategy(
        user_id=user_id,
        version=version,
        is_active=True,
        brand_intelligence=brand_intelligence,
    )
    session.add(row)
    await session.flush()
    return row


async def ensure_strategy(
    session: AsyncSession,
    *,
    user_id: UUID,
    brand_intelligence_if_new: dict[str, Any],
) -> Strategy:
    """Return the user's active strategy, creating v1 from the given
    brand_intelligence if none exists yet."""
    existing = await get_active_strategy(session, user_id)
    if existing is not None:
        return existing
    return await create_strategy(
        session,
        user_id=user_id,
        brand_intelligence=brand_intelligence_if_new,
    )


async def create_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    raw_brief_id: UUID | None,
    strategy_id: UUID,
    action_code: str,
    job_input: dict[str, Any],
    output: dict[str, Any] | None = None,
    status: str = "pending",
    latency_ms: int | None = None,
    error: dict[str, Any] | None = None,
) -> Job:
    now = datetime.now(timezone.utc)
    row = Job(
        user_id=user_id,
        raw_brief_id=raw_brief_id,
        strategy_id=strategy_id,
        action_code=action_code,
        input=job_input,
        output=output,
        status=status,
        error=error,
        latency_ms=latency_ms,
        started_at=now if status in ("running", "done", "failed") else None,
        completed_at=now if status in ("done", "failed") else None,
    )
    session.add(row)
    await session.flush()
    return row


async def mark_raw_brief(
    session: AsyncSession, *, raw_brief_id: UUID, status: str
) -> None:
    await session.execute(
        update(RawBrief)
        .where(RawBrief.id == raw_brief_id)
        .values(status=status, processed_at=datetime.now(timezone.utc))
    )
