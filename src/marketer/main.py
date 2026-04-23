"""FastAPI app — async router-compatible dispatch.

Router contract (see docs/ROUTER CONTRACT.md §3-4):
- POST /tasks must ACK with 2xx (ideally 202) within 10s; no body required.
- Real work runs async. Result is delivered via PATCH {callback_url}.

Additional endpoint:
- POST /tasks/sync — dev-only synchronous pipeline for local testing. Returns
  the CallbackBody in the response body. Not used by router.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from marketer.config import load_settings
from marketer.db import actions_cache
from marketer.db.engine import is_configured as _db_configured
from marketer.llm.gemini import GeminiClient
from marketer.gallery import fetch_gallery_pool
from marketer.persistence import (
    PersistCtx,
    persist_on_complete,
    persist_on_complete_multi,
    persist_on_ingest,
    persist_user_profile,
    update_dispatch_status,
)
from marketer.schemas.internal_context import GalleryPool
from marketer.user_profile import UserProfile, fetch_user_profile
from marketer.reasoner import OVERLAYS as _CODE_OVERLAYS
from marketer.reasoner import reason, reason_multi
from marketer.schemas.enrichment import CallbackBody

settings = load_settings()
logging.basicConfig(
    level=settings.log_level,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
)
logger = logging.getLogger("marketer")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Startup: warm action_types cache and verify DB↔code alignment.

    Hard-fails the boot if an enabled action in the catalog has no matching
    OVERLAYS entry — that means a row was INSERTed without the prompt module
    being deployed, and accepting traffic for it would just produce runtime
    failures. Better to refuse to start.

    Tolerates DB outages on boot (logs + continues in degraded mode); the
    catalog will lazy-refresh on first traffic.
    """
    if _db_configured():
        try:
            await actions_cache.refresh()
            enabled = await actions_cache.enabled_codes()
            missing = enabled - set(_CODE_OVERLAYS.keys())
            if missing:
                raise RuntimeError(
                    "action_types is_enabled=true but no OVERLAYS entry: "
                    f"{sorted(missing)}. Either deploy the prompt module + "
                    "OVERLAYS entry, or set is_enabled=false on these rows."
                )
            logger.info(
                '"startup_actions_aligned enabled=%d overlays=%d"',
                len(enabled),
                len(_CODE_OVERLAYS),
            )
        except RuntimeError:
            raise
        except Exception:
            logger.exception('"startup_actions_cache_failed degraded=true"')
    yield


app = FastAPI(title="MARKETER", version="0.2.0", lifespan=lifespan)


def _get_gemini_client() -> GeminiClient:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
    return GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _check_inbound_auth(authorization: str | None) -> None:
    """Validate the Authorization header when INBOUND_TOKEN is configured."""
    if not settings.inbound_token:
        return
    expected = f"Bearer {settings.inbound_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid_token")


async def _patch_callback(
    callback_url: str,
    body: dict[str, Any],
    correlation_id: str | None,
    task_id: str,
) -> None:
    """PATCH the callback URL with the CallbackBody. Retries on transient errors."""
    worker = logging.getLogger("marketer.worker")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.orch_callback_api_key:
        headers["X-API-Key"] = settings.orch_callback_api_key
    if correlation_id:
        headers["X-Correlation-Id"] = correlation_id

    worker.info(
        '"task_id=%s callback_request url=%s status=%s"',
        task_id, callback_url, body.get("status"),
    )
    attempts = max(1, settings.callback_retry_attempts)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(
                timeout=settings.callback_http_timeout_seconds
            ) as client:
                resp = await client.patch(callback_url, json=body, headers=headers)
            if 200 <= resp.status_code < 300:
                worker.info(
                    '"task_id=%s callback_ok status=%s attempt=%d"',
                    task_id,
                    resp.status_code,
                    attempt,
                )
                return
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            worker.warning(
                '"task_id=%s callback_non2xx status=%s attempt=%d"',
                task_id,
                resp.status_code,
                attempt,
            )
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            worker.warning(
                '"task_id=%s callback_transport_error attempt=%d error=%s"',
                task_id,
                attempt,
                last_error,
            )
        if attempt < attempts:
            await asyncio.sleep(min(2**attempt, 8))

    worker.error(
        '"task_id=%s callback_failed_after_%d_attempts error=%s"',
        task_id,
        attempts,
        last_error,
    )


def _build_gallery_task_context(envelope: dict[str, Any]) -> dict[str, Any]:
    """Extract brief snippets from the raw envelope for Stage 1 gallery scoring."""
    payload = envelope.get("payload") or {}
    client_request = payload.get("client_request") or {}
    gates = payload.get("action_execution_gates") or {}
    brief_gate = (gates.get("brief") or {}).get("response") or {}
    brief_data = brief_gate.get("data") if isinstance(brief_gate, dict) else {}
    brief_obj = (brief_data or {}).get("brief") or {}
    form_values = (brief_obj if isinstance(brief_obj, dict) else {}).get(
        "form_values"
    ) or {}

    keywords_raw = form_values.get("FIELD_KEYWORDS_TAGS_INPUT") or []
    keywords = [k for k in keywords_raw if isinstance(k, str)]

    tone_raw = form_values.get("FIELD_COMMUNICATION_STYLE")
    if isinstance(tone_raw, list):
        tone = " ".join(s for s in tone_raw if isinstance(s, str))
    else:
        tone = tone_raw if isinstance(tone_raw, str) else ""

    return {
        "user_request": client_request.get("description") or "",
        "brief_keywords": keywords,
        "brief_tone": tone,
        "action_code": envelope.get("action_code") or "",
        "brief_design_style": form_values.get("FIELD_DESIGN_STYLE") or "",
    }


async def _run_and_callback(
    envelope: dict[str, Any], pctx: PersistCtx | None = None
) -> None:
    """Run reason() and PATCH callback. Any internal exception is captured
    and reported to the router as status=FAILED so the task never hangs.

    When `pctx` is provided (persistence configured on ingest), also writes
    the job + strategy + raw_brief-terminal rows after reason() returns.
    """
    worker = logging.getLogger("marketer.worker")
    task_id = envelope.get("task_id", "unknown")
    callback_url = envelope.get("callback_url") or ""
    correlation_id = envelope.get("correlation_id")

    if callback_url:
        # Let the orchestrator move DISPATCHED -> IN_PROGRESS immediately.
        await _patch_callback(
            callback_url=callback_url,
            body=CallbackBody(status="IN_PROGRESS").model_dump(mode="json"),
            correlation_id=correlation_id,
            task_id=str(task_id),
        )

    account_uuid = (
        (envelope.get("payload") or {}).get("context", {}).get("account_uuid")
    )
    usp_configured = bool(settings.usp_api_key and settings.usp_graphql_url)
    gallery_configured = bool(settings.gallery_api_url and settings.gallery_api_key)

    # Parallel fetch: USP Memory Gateway + Gallery Image Pool (§6.2)
    async def _usp_fetch():
        if not usp_configured or not account_uuid:
            return None
        return await fetch_user_profile(
            account_uuid=account_uuid,
            endpoint=settings.usp_graphql_url,
            api_key=settings.usp_api_key,
            timeout=settings.usp_timeout_seconds,
        )

    async def _gallery_fetch():
        if not gallery_configured or not account_uuid:
            return None, "gallery_api_skipped"
        task_context = _build_gallery_task_context(envelope)
        return await fetch_gallery_pool(
            account_uuid=account_uuid,
            base_url=settings.gallery_api_url,
            api_key=settings.gallery_api_key,
            task_context=task_context,
            vision_candidates=settings.gallery_vision_candidates,
            page_size=settings.gallery_page_size,
            timeout=settings.gallery_timeout_seconds,
        )

    usp_result: UserProfile | None | BaseException
    gallery_result: tuple[GalleryPool | None, str | None] | None | BaseException
    usp_result, gallery_result = await asyncio.gather(  # type: ignore[assignment]
        _usp_fetch(), _gallery_fetch(), return_exceptions=True
    )

    # Resolve USP result
    if isinstance(usp_result, BaseException):
        worker.warning('"task_id=%s usp_gather_exception"', task_id)
        user_profile = None
        usp_warning: str | None = "user_profile_unavailable"
    elif not usp_configured or not account_uuid:
        user_profile = None
        usp_warning = "user_profile_skipped"
    else:
        user_profile = usp_result
        if user_profile is None:
            usp_warning = "user_profile_unavailable"
        elif user_profile.identity is None:
            usp_warning = "user_profile_not_found"
        else:
            usp_warning = None

    # Resolve Gallery result
    if isinstance(gallery_result, BaseException):
        worker.warning('"task_id=%s gallery_gather_exception"', task_id)
        gallery_pool = None
        gallery_warning: str | None = "gallery_api_unavailable"
    elif gallery_result is None:
        gallery_pool = None
        gallery_warning = "gallery_api_skipped"
    else:
        gallery_pool, gallery_warning = gallery_result

    if pctx is not None and user_profile is not None:
        await persist_user_profile(pctx.raw_brief_id, user_profile)

    action_code = envelope.get("action_code")

    # --- subscription_strategy: multi-job branch ---
    if action_code == "subscription_strategy":
        await _run_multi_and_callback(
            envelope, pctx, callback_url, correlation_id, task_id,
            user_profile, usp_warning, gallery_pool, gallery_warning,
        )
        return

    # --- Single-job flow (create_post, edit_post, etc.) ---
    def _sync_work() -> CallbackBody:
        client = GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        return reason(
            envelope,
            gemini=client,
            extras_truncation=settings.extras_list_truncation,
            prompt_text_truncation_chars=settings.prompt_text_truncation_chars,
            max_output_tokens=settings.llm_max_output_tokens,
            user_profile=user_profile,
            usp_warning=usp_warning,
            gallery_pool=gallery_pool,
            gallery_warning=gallery_warning,
        )

    started = time.time()
    try:
        callback = await asyncio.to_thread(_sync_work)
    except Exception as exc:  # noqa: BLE001
        worker.exception('"task_id=%s reason_failed"', task_id)
        callback = CallbackBody(
            status="FAILED",
            error_message=f"internal_error: {type(exc).__name__}: {exc}",
        )
    latency_ms = int((time.time() - started) * 1000)

    if pctx is not None:
        await persist_on_complete(pctx, envelope, callback, latency_ms)

    if not callback_url:
        worker.error('"task_id=%s missing_callback_url — cannot report"', task_id)
        return

    await _patch_callback(
        callback_url=callback_url,
        body=callback.model_dump(mode="json"),
        correlation_id=correlation_id,
        task_id=str(task_id),
    )


async def _post_dispatcher(
    account_uuid: str,
    product_uuid: str,
    task_id: str,
) -> bool:
    """POST to agentic-task-dispatcher. Returns True on 2xx."""
    worker = logging.getLogger("marketer.worker")
    url = settings.agentic_dispatcher_url
    if not url:
        worker.debug('"task_id=%s dispatcher_skipped reason=no_url"', task_id)
        return False

    body = {"product_uuid": product_uuid, "account_uuid": account_uuid}
    worker.info(
        '"task_id=%s dispatcher_request url=%s body=%s"',
        task_id, url, body,
    )
    attempts = max(1, settings.callback_retry_attempts)
    last_error: str | None = None

    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(
                timeout=settings.callback_http_timeout_seconds
            ) as client:
                resp = await client.post(url, json=body)
            worker.info(
                '"task_id=%s dispatcher_response status=%s body=%s attempt=%d"',
                task_id, resp.status_code, resp.text[:500], attempt,
            )
            if 200 <= resp.status_code < 300:
                return True
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            worker.warning(
                '"task_id=%s dispatcher_error attempt=%d error=%s"',
                task_id, attempt, last_error,
            )
        if attempt < attempts:
            await asyncio.sleep(min(2**attempt, 8))

    worker.error(
        '"task_id=%s dispatcher_failed_after_%d_attempts error=%s"',
        task_id, attempts, last_error,
    )
    return False


_LLM_ACTION_KEYS = {"create_post", "edit_post"}


async def _run_multi_and_callback(
    envelope: dict[str, Any],
    pctx: PersistCtx | None,
    callback_url: str,
    correlation_id: str | None,
    task_id: Any,
    user_profile: UserProfile | None,
    usp_warning: str | None,
    gallery_pool: GalleryPool | None,
    gallery_warning: str | None,
) -> None:
    """Multi-job branch for subscription_strategy.

    Jobs are split by orchestrator_agent:
    - job-router (action_key in _LLM_ACTION_KEYS): run through LLM → PATCH callback
    - prod-line: skip LLM, persist + POST to agentic-task-dispatcher
    """
    worker = logging.getLogger("marketer.worker")
    from marketer.normalizer import _extract_subscription_jobs
    from marketer.schemas.internal_context import SubscriptionJob

    # --- Split jobs: LLM vs passthrough ---
    payload = envelope.get("payload") or {}
    client_request = payload.get("client_request") or {}
    all_jobs, _job_warnings = _extract_subscription_jobs(client_request)

    llm_jobs = [j for j in all_jobs if j.action_key in _LLM_ACTION_KEYS]
    passthrough_jobs = [j for j in all_jobs if j.action_key not in _LLM_ACTION_KEYS]

    worker.info(
        '"task_id=%s jobs_split llm=%d passthrough=%d"',
        task_id, len(llm_jobs), len(passthrough_jobs),
    )

    # --- LLM path: only for job-router enrichable jobs ---
    results: list[tuple[CallbackBody, SubscriptionJob | None]] = []
    latency_ms = 0

    if llm_jobs:
        # Build a modified envelope with only LLM-eligible jobs
        llm_envelope = {**envelope}
        llm_payload = {**payload}
        llm_cr = {**client_request}
        llm_cr["jobs"] = [
            {"action_key": j.action_key, "description": j.description,
             "quantity": 1, "orchestrator_agent": j.orchestrator_agent or "job-router",
             "slug": j.slug or "", "product_uuid": j.product_uuid or ""}
            for j in llm_jobs
        ]
        llm_payload["client_request"] = llm_cr
        llm_envelope["payload"] = llm_payload

        def _sync_multi():
            client = GeminiClient(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                timeout_seconds=settings.llm_timeout_seconds,
            )
            return reason_multi(
                llm_envelope,
                gemini=client,
                extras_truncation=settings.extras_list_truncation,
                prompt_text_truncation_chars=settings.prompt_text_truncation_chars,
                max_output_tokens=settings.llm_max_output_tokens,
                user_profile=user_profile,
                usp_warning=usp_warning,
                gallery_pool=gallery_pool,
                gallery_warning=gallery_warning,
            )

        started = time.time()
        try:
            results = await asyncio.to_thread(_sync_multi)
        except Exception as exc:  # noqa: BLE001
            worker.exception('"task_id=%s reason_multi_failed"', task_id)
            results = [
                (CallbackBody(status="FAILED", error_message=f"internal_error: {type(exc).__name__}: {exc}"), None)
            ]
        latency_ms = int((time.time() - started) * 1000)

    # --- Persist LLM results ---
    created_jobs: list[tuple[Any, SubscriptionJob]] = []
    if pctx is not None:
        valid_results = [(cb, job) for cb, job in results if job is not None]
        if valid_results:
            created_jobs = await persist_on_complete_multi(
                pctx, envelope, valid_results, latency_ms
            )
        elif llm_jobs and not valid_results:
            from marketer.db import is_configured, session_scope
            from marketer.db.repositories import mark_raw_brief
            if is_configured():
                try:
                    async with session_scope() as session:
                        await mark_raw_brief(
                            session, raw_brief_id=pctx.raw_brief_id, status="failed"
                        )
                except Exception:
                    worker.exception('"persist_multi_fallback_failed"')

    # --- Persist passthrough jobs (no enrichment, just dispatch) ---
    passthrough_db_jobs: list[tuple[Any, SubscriptionJob]] = []
    if pctx is not None and passthrough_jobs:
        from marketer.db import is_configured, session_scope
        from marketer.db.repositories import ensure_strategy, get_active_strategy, create_job
        if is_configured():
            try:
                async with session_scope() as session:
                    strategy = await get_active_strategy(session, pctx.user_id)
                    if strategy is None:
                        # Try to get strategy from LLM results
                        for cb, _j in results:
                            if cb.status == "COMPLETED" and cb.output_data:
                                bi = cb.output_data.enrichment.brand_intelligence.model_dump()
                                strategy = await ensure_strategy(
                                    session, user_id=pctx.user_id, brand_intelligence_if_new=bi
                                )
                                break
                    if strategy is not None:
                        for job in passthrough_jobs:
                            db_job = await create_job(
                                session,
                                user_id=pctx.user_id,
                                raw_brief_id=pctx.raw_brief_id,
                                strategy_id=strategy.id,
                                action_code=job.action_key,
                                job_input={
                                    "action_code": job.action_key,
                                    "slug": job.slug,
                                    "product_uuid": job.product_uuid,
                                    "orchestrator_agent": job.orchestrator_agent,
                                    "job_index": job.index,
                                    "description": job.description,
                                },
                                status="done",
                                latency_ms=0,
                                orchestrator_agent=job.orchestrator_agent,
                                dispatch_status="pending",
                            )
                            passthrough_db_jobs.append((db_job.id, job))
            except Exception:
                worker.exception('"persist_passthrough_failed"')

    # --- Dispatch: PATCH to router for LLM jobs, POST to dispatcher for passthrough ---
    account_uuid = (
        (envelope.get("payload") or {}).get("context", {}).get("account_uuid") or ""
    )

    # LLM job callbacks → PATCH to router
    job_index_to_db_id: dict[int, Any] = {
        sub_job.index: db_id for db_id, sub_job in created_jobs
    }
    for callback_body, job in results:
        if job is None:
            if callback_url:
                await _patch_callback(
                    callback_url=callback_url,
                    body=callback_body.model_dump(mode="json"),
                    correlation_id=correlation_id,
                    task_id=str(task_id),
                )
            continue

        if callback_url:
            await _patch_callback(
                callback_url=callback_url,
                body=callback_body.model_dump(mode="json"),
                correlation_id=correlation_id,
                task_id=str(task_id),
            )
        db_job_id = job_index_to_db_id.get(job.index)
        if db_job_id is not None:
            await update_dispatch_status(db_job_id, "ok")

    # Passthrough jobs → POST to agentic-task-dispatcher
    for db_job_id, job in passthrough_db_jobs:
        dispatch_ok = await _post_dispatcher(
            account_uuid=account_uuid,
            product_uuid=job.product_uuid or "",
            task_id=str(task_id),
        )
        await update_dispatch_status(
            db_job_id, "ok" if dispatch_ok else "failed"
        )

    worker.info(
        '"task_id=%s subscription_strategy_done llm_items=%d passthrough_items=%d"',
        task_id, len(results), len(passthrough_db_jobs),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready")
def ready() -> dict[str, str]:
    if not settings.gemini_api_key:
        return {"status": "unhealthy", "detail": "GEMINI_API_KEY not set"}
    return {"status": "ready"}


@app.post("/tasks", status_code=202)
async def run_task(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Router dispatch entrypoint. ACK with 202 and run the work async.

    Result is PATCHed to `envelope.callback_url` when done.
    """
    _check_inbound_auth(authorization)

    try:
        envelope = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid_json: {exc}")
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=400, detail="envelope must be an object")

    task_id = envelope.get("task_id")
    callback_url = envelope.get("callback_url")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    if not callback_url:
        raise HTTPException(status_code=400, detail="callback_url is required")
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")

    # Validate action_code against the DB catalog (cached). Skipped silently
    # when persistence is not configured — in that mode the existing
    # hardcoded check inside normalizer is the only line of defense.
    if _db_configured():
        action = await actions_cache.get(envelope.get("action_code", ""))
        if action is None:
            raise HTTPException(
                status_code=422,
                detail=f"action_unknown: '{envelope.get('action_code')}' is not in action_types catalog",
            )
        if not action.is_enabled:
            raise HTTPException(
                status_code=422,
                detail=f"action_not_enabled: '{action.code}' is gated off (action_types.is_enabled=false)",
            )

    # Persistence pre-flight. Never blocks the 202 — on any failure
    # pctx is None and the background path runs without DB writes.
    pctx = await persist_on_ingest(envelope)

    background_tasks.add_task(_run_and_callback, envelope, pctx)

    return {"status": "ACCEPTED", "task_id": task_id}


@app.post("/tasks/sync")
async def run_task_sync(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any] | list[dict[str, Any]]:
    """Dev-only synchronous endpoint. Returns CallbackBody inline.

    Not used by router. For local curl/pytest and prompt iteration.
    """
    _check_inbound_auth(authorization)

    try:
        envelope = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid_json: {exc}")
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=400, detail="envelope must be an object")

    client = _get_gemini_client()
    pctx = await persist_on_ingest(envelope)

    started = time.time()

    if envelope.get("action_code") == "subscription_strategy":
        from marketer.normalizer import _extract_subscription_jobs

        payload = envelope.get("payload") or {}
        cr = payload.get("client_request") or {}
        all_jobs, _ = _extract_subscription_jobs(cr)
        llm_jobs = [j for j in all_jobs if j.action_key in _LLM_ACTION_KEYS]
        passthrough_jobs = [j for j in all_jobs if j.action_key not in _LLM_ACTION_KEYS]

        results = []
        if llm_jobs:
            llm_envelope = {**envelope}
            llm_payload = {**payload}
            llm_cr = {**cr}
            llm_cr["jobs"] = [
                {"action_key": j.action_key, "description": j.description,
                 "quantity": 1, "orchestrator_agent": j.orchestrator_agent or "job-router",
                 "slug": j.slug or "", "product_uuid": j.product_uuid or ""}
                for j in llm_jobs
            ]
            llm_payload["client_request"] = llm_cr
            llm_envelope["payload"] = llm_payload
            results = reason_multi(
                llm_envelope,
                gemini=client,
                extras_truncation=settings.extras_list_truncation,
                prompt_text_truncation_chars=settings.prompt_text_truncation_chars,
                max_output_tokens=settings.llm_max_output_tokens,
            )

        latency_ms = int((time.time() - started) * 1000)
        if pctx is not None:
            valid_results = [(cb, job) for cb, job in results if job is not None]
            if valid_results:
                await persist_on_complete_multi(pctx, envelope, valid_results, latency_ms)

        # Passthrough jobs: persist in DB + dispatch to agentic-task-dispatcher
        account_uuid = (
            (envelope.get("payload") or {}).get("context", {}).get("account_uuid") or ""
        )
        if pctx is not None and passthrough_jobs:
            from marketer.db import is_configured, session_scope
            from marketer.db.repositories import ensure_strategy, get_active_strategy, create_job as _create_job
            if is_configured():
                try:
                    async with session_scope() as session:
                        strategy = await get_active_strategy(session, pctx.user_id)
                        if strategy is None:
                            for cb, _j in results:
                                if cb.status == "COMPLETED" and cb.output_data:
                                    bi = cb.output_data.enrichment.brand_intelligence.model_dump()
                                    strategy = await ensure_strategy(
                                        session, user_id=pctx.user_id, brand_intelligence_if_new=bi
                                    )
                                    break
                        if strategy is not None:
                            for j in passthrough_jobs:
                                await _create_job(
                                    session,
                                    user_id=pctx.user_id,
                                    raw_brief_id=pctx.raw_brief_id,
                                    strategy_id=strategy.id,
                                    action_code=j.action_key,
                                    job_input={
                                        "action_code": j.action_key, "slug": j.slug,
                                        "product_uuid": j.product_uuid,
                                        "orchestrator_agent": j.orchestrator_agent,
                                        "job_index": j.index, "description": j.description,
                                    },
                                    status="done",
                                    latency_ms=0,
                                    orchestrator_agent=j.orchestrator_agent,
                                    dispatch_status="pending",
                                )
                except Exception:
                    logging.getLogger("marketer.worker").exception('"sync_persist_passthrough_failed"')

        passthrough_responses = []
        for j in passthrough_jobs:
            dispatch_ok = await _post_dispatcher(
                account_uuid=account_uuid,
                product_uuid=j.product_uuid or "",
                task_id=str(envelope.get("task_id", "unknown")),
            )
            passthrough_responses.append({
                "status": "COMPLETED" if dispatch_ok else "FAILED",
                "output_data": None,
                "passthrough": True,
                "job_index": j.index,
                "action_key": j.action_key,
                "slug": j.slug,
                "orchestrator_agent": j.orchestrator_agent,
                "product_uuid": j.product_uuid,
                "dispatch_status": "ok" if dispatch_ok else "failed",
            })

        llm_responses = [cb.model_dump(mode="json") for cb, _job in results]
        return llm_responses + passthrough_responses

    callback = reason(
        envelope,
        gemini=client,
        extras_truncation=settings.extras_list_truncation,
        prompt_text_truncation_chars=settings.prompt_text_truncation_chars,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    latency_ms = int((time.time() - started) * 1000)
    if pctx is not None:
        await persist_on_complete(pctx, envelope, callback, latency_ms)
    return callback.model_dump(mode="json")
