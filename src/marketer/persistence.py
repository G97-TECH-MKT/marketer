"""Persistence hooks for the ingest path.

Two touch-points:
  1. `persist_on_ingest` — runs before the 202 ack. Upserts the user and
     inserts the raw_brief (the durable log of the ROUTER envelope).
  2. `persist_on_complete` / `persist_on_fail` — run in the background task
     after reason() returns. Ensures a strategy (creating v1 from the first
     successful enrichment's brand_intelligence), creates a jobs row for
     successful runs, and marks raw_brief terminal.

All functions swallow exceptions into warnings/logs — persistence must never
block the callback to ROUTER. If DATABASE_URL is not set, they no-op.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from marketer.db import is_configured, session_scope
from marketer.db.repositories import (
    create_job,
    ensure_strategy,
    get_active_strategy,
    get_action_type,
    insert_raw_brief,
    mark_raw_brief,
    update_raw_brief_user_profile,
    upsert_user,
)
from marketer.schemas.enrichment import CallbackBody
from marketer.schemas.internal_context import SubscriptionJob
from marketer.user_profile import UserProfile

logger = logging.getLogger("marketer.persistence")


@dataclass
class PersistCtx:
    """Handoff between the 202 path and the background task."""

    user_id: UUID
    raw_brief_id: UUID
    action_code: str


def _parse_uuid(value: Any) -> UUID | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _distill_job_input(envelope: dict[str, Any]) -> dict[str, Any]:
    payload = envelope.get("payload") or {}
    client_request = payload.get("client_request") or {}
    context = payload.get("context") or {}
    return {
        "action_code": envelope.get("action_code"),
        "router_task_id": envelope.get("task_id"),
        "router_job_id": envelope.get("job_id"),
        "correlation_id": envelope.get("correlation_id"),
        "user_request": client_request.get("description"),
        "account_uuid": context.get("account_uuid"),
        "client_name": context.get("client_name"),
        "platform": context.get("platform"),
    }


async def persist_on_ingest(envelope: dict[str, Any]) -> PersistCtx | None:
    """Upsert user + insert raw_brief. Returns handoff ctx for the background
    worker, or None if persistence can't/shouldn't happen for this envelope."""
    if not is_configured():
        return None

    payload = envelope.get("payload") or {}
    ctx = payload.get("context") or {}
    user_id = _parse_uuid(ctx.get("account_uuid"))
    router_task_id = _parse_uuid(envelope.get("task_id"))
    action_code = envelope.get("action_code")

    if user_id is None:
        logger.info('"skip_persist reason=missing_account_uuid"')
        return None
    if router_task_id is None:
        logger.info('"skip_persist reason=non_uuid_task_id"')
        return None
    if not isinstance(action_code, str) or not action_code:
        logger.info('"skip_persist reason=missing_action_code"')
        return None

    router_job_id = _parse_uuid(envelope.get("job_id"))
    correlation_id = envelope.get("correlation_id")
    if not isinstance(correlation_id, str):
        correlation_id = None

    try:
        async with session_scope() as session:
            # FK gate — unknown action_code → skip persistence.
            action = await get_action_type(session, action_code)
            if action is None:
                logger.warning(
                    '"skip_persist reason=unknown_action_code action_code=%s"',
                    action_code,
                )
                return None

            await upsert_user(session, user_id=user_id)
            raw_brief = await insert_raw_brief(
                session,
                user_id=user_id,
                router_task_id=router_task_id,
                action_code=action_code,
                envelope=envelope,
                router_job_id=router_job_id,
                router_correlation_id=correlation_id,
            )
            return PersistCtx(
                user_id=user_id,
                raw_brief_id=raw_brief.id,
                action_code=action_code,
            )
    except Exception:
        logger.exception(
            '"persist_on_ingest_failed task_id=%s"', envelope.get("task_id")
        )
        return None


async def persist_user_profile(raw_brief_id: UUID, user_profile: UserProfile) -> None:
    """Best-effort UPDATE of raw_briefs.user_profile. Never blocks enrichment."""
    if not is_configured():
        return
    try:
        async with session_scope() as session:
            await update_raw_brief_user_profile(
                session,
                raw_brief_id=raw_brief_id,
                data=user_profile.to_storage_dict(),
            )
    except Exception:
        logger.warning('"persist_user_profile_failed raw_brief_id=%s"', raw_brief_id)


async def persist_on_complete(
    pctx: PersistCtx, envelope: dict[str, Any], callback: CallbackBody, latency_ms: int
) -> None:
    """After reason() returns: ensure strategy + create job + mark raw_brief."""
    if not is_configured():
        return

    try:
        async with session_scope() as session:
            if callback.status == "COMPLETED" and callback.output_data is not None:
                brand_intelligence = (
                    callback.output_data.enrichment.brand_intelligence.model_dump()
                )
                strategy = await ensure_strategy(
                    session,
                    user_id=pctx.user_id,
                    brand_intelligence_if_new=brand_intelligence,
                )
                await create_job(
                    session,
                    user_id=pctx.user_id,
                    raw_brief_id=pctx.raw_brief_id,
                    strategy_id=strategy.id,
                    action_code=pctx.action_code,
                    job_input=_distill_job_input(envelope),
                    output=callback.model_dump(mode="json"),
                    status="done",
                    latency_ms=latency_ms,
                    callback_payload=callback.model_dump(mode="json"),
                )
                await mark_raw_brief(
                    session, raw_brief_id=pctx.raw_brief_id, status="done"
                )
            else:
                # Failed run — no strategy promotion, no jobs row (FK requires
                # strategy and we don't have brand_intelligence to seed one).
                # If an active strategy already exists, persist a failed job for
                # observability while still marking raw_brief terminal.
                active_strategy = await get_active_strategy(session, pctx.user_id)
                if active_strategy is not None:
                    await create_job(
                        session,
                        user_id=pctx.user_id,
                        raw_brief_id=pctx.raw_brief_id,
                        strategy_id=active_strategy.id,
                        action_code=pctx.action_code,
                        job_input=_distill_job_input(envelope),
                        output=callback.model_dump(mode="json"),
                        status="failed",
                        latency_ms=latency_ms,
                        error={"message": callback.error_message or "unknown_error"},
                        callback_payload=callback.model_dump(mode="json"),
                    )
                await mark_raw_brief(
                    session, raw_brief_id=pctx.raw_brief_id, status="failed"
                )
    except Exception:
        logger.exception(
            '"persist_on_complete_failed raw_brief_id=%s"', pctx.raw_brief_id
        )


async def persist_on_complete_multi(
    pctx: PersistCtx,
    envelope: dict[str, Any],
    results: list[tuple[CallbackBody, SubscriptionJob]],
    latency_ms: int,
) -> list[tuple[UUID, SubscriptionJob]]:
    """After reason_multi() returns: persist N jobs + mark raw_brief terminal.

    Returns list of (job_db_id, SubscriptionJob) for dispatch status tracking.
    """
    if not is_configured():
        return []

    created_jobs: list[tuple[UUID, SubscriptionJob]] = []
    try:
        async with session_scope() as session:
            # Find first successful enrichment for strategy seeding
            first_bi: dict[str, Any] | None = None
            for callback, _job in results:
                if callback.status == "COMPLETED" and callback.output_data is not None:
                    first_bi = (
                        callback.output_data.enrichment.brand_intelligence.model_dump()
                    )
                    break

            strategy = None
            if first_bi is not None:
                strategy = await ensure_strategy(
                    session,
                    user_id=pctx.user_id,
                    brand_intelligence_if_new=first_bi,
                )
            else:
                strategy = await get_active_strategy(session, pctx.user_id)

            any_completed = False
            for callback, job in results:
                if strategy is None:
                    break

                job_input = _distill_job_input(envelope)
                job_input["job_index"] = job.index
                job_input["job_action_key"] = job.action_key
                job_input["user_request"] = job.description

                if callback.status == "COMPLETED":
                    any_completed = True
                    db_job = await create_job(
                        session,
                        user_id=pctx.user_id,
                        raw_brief_id=pctx.raw_brief_id,
                        strategy_id=strategy.id,
                        action_code=job.action_key,
                        job_input=job_input,
                        output=callback.model_dump(mode="json"),
                        status="done",
                        latency_ms=latency_ms,
                        orchestrator_agent=job.orchestrator_agent,
                        dispatch_status="pending",
                        callback_payload=callback.model_dump(mode="json"),
                    )
                    created_jobs.append((db_job.id, job))
                else:
                    db_job = await create_job(
                        session,
                        user_id=pctx.user_id,
                        raw_brief_id=pctx.raw_brief_id,
                        strategy_id=strategy.id,
                        action_code=job.action_key,
                        job_input=job_input,
                        output=callback.model_dump(mode="json"),
                        status="failed",
                        latency_ms=latency_ms,
                        error={"message": callback.error_message or "unknown_error"},
                        orchestrator_agent=job.orchestrator_agent,
                        dispatch_status="failed",
                        callback_payload=callback.model_dump(mode="json"),
                    )
                    created_jobs.append((db_job.id, job))

            final_status = "done" if any_completed else "failed"
            await mark_raw_brief(
                session, raw_brief_id=pctx.raw_brief_id, status=final_status
            )
    except Exception:
        logger.exception(
            '"persist_on_complete_multi_failed raw_brief_id=%s"', pctx.raw_brief_id
        )
    return created_jobs


async def update_dispatch_status(job_id: UUID, status: str) -> None:
    """Update dispatch_status on a job row. Best-effort, never blocks."""
    if not is_configured():
        return
    try:
        async with session_scope() as session:
            from marketer.db.repositories import update_dispatch_status as _repo_update

            await _repo_update(session, job_id=job_id, dispatch_status=status)
    except Exception:
        logger.warning('"update_dispatch_status_failed job_id=%s"', job_id)
