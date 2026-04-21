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
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from marketer.config import load_settings
from marketer.llm.gemini import GeminiClient
from marketer.persistence import PersistCtx, persist_on_complete, persist_on_ingest
from marketer.reasoner import reason
from marketer.schemas.enrichment import CallbackBody

settings = load_settings()
logging.basicConfig(
    level=settings.log_level,
    format='{"level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
)
logger = logging.getLogger("marketer")

app = FastAPI(title="MARKETER", version="0.2.0")


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

    attempts = max(1, settings.callback_retry_attempts)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.callback_http_timeout_seconds) as client:
                resp = await client.patch(callback_url, json=body, headers=headers)
            if 200 <= resp.status_code < 300:
                worker.info(
                    '"task_id=%s callback_ok status=%s attempt=%d"',
                    task_id, resp.status_code, attempt,
                )
                return
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            worker.warning(
                '"task_id=%s callback_non2xx status=%s attempt=%d"',
                task_id, resp.status_code, attempt,
            )
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            worker.warning(
                '"task_id=%s callback_transport_error attempt=%d error=%s"',
                task_id, attempt, last_error,
            )
        if attempt < attempts:
            await asyncio.sleep(min(2 ** attempt, 8))

    worker.error(
        '"task_id=%s callback_failed_after_%d_attempts error=%s"',
        task_id, attempts, last_error,
    )


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
        )

    started = time.time()
    try:
        # reason() is synchronous and takes 10-15s. Offload to a thread so
        # uvicorn's event loop can flush the prior 202 response and accept
        # other requests.
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

    # Persistence pre-flight. Never blocks the 202 — on any failure
    # pctx is None and the background path runs without DB writes.
    pctx = await persist_on_ingest(envelope)

    background_tasks.add_task(_run_and_callback, envelope, pctx)

    return {"status": "ACCEPTED", "task_id": task_id}


@app.post("/tasks/sync")
async def run_task_sync(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
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
    callback = reason(envelope, gemini=client, extras_truncation=settings.extras_list_truncation)
    return callback.model_dump(mode="json")
