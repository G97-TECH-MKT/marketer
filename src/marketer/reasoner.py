"""Reasoner: orchestrates normalize -> prompt -> Gemini -> validate -> assemble.

Sync pipeline for the MVP vertical slice. Returns CallbackBody shape.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from marketer.llm.gemini import GeminiClient, is_timeout_exception, serialize_for_prompt
from marketer.llm.prompts.create_post import CREATE_POST_OVERLAY
from marketer.llm.prompts.create_web import CREATE_WEB_OVERLAY
from marketer.llm.prompts.edit_post import EDIT_POST_OVERLAY
from marketer.llm.prompts.edit_web import EDIT_WEB_OVERLAY
from marketer.llm.prompts.repair import REPAIR_PROMPT_TEMPLATE
from marketer.llm.prompts.subscription_strategy import SUBSCRIPTION_STRATEGY_OVERLAY
from marketer.llm.prompts.system import SYSTEM_PROMPT
from marketer.normalizer import normalize
from marketer.schemas.enrichment import (
    CFPayload,
    CallbackBody,
    CallbackOutputData,
    GalleryStats,
    MultiEnrichmentOutput,
    PostEnrichment,
    TraceInfo,
    Warning,
)
from marketer.schemas.internal_context import (
    GalleryPool,
    InternalContext,
    SubscriptionJob,
)
from marketer.user_profile import UserProfile
from marketer.validator import validate_and_correct

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(os.environ.get("PROMPTS_DUMP_DIR", "reports/prompts"))


def _dump_prompt(
    task_id: str, system_prompt: str, user_prompt: str, response: str = ""
) -> None:
    """Write the full prompt exchange to reports/prompts/ when LOG_LEVEL=DEBUG."""
    if os.environ.get("LOG_LEVEL", "INFO").upper() != "DEBUG":
        return
    try:
        PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_id = task_id.replace("/", "_")[:40]
        path = PROMPTS_DIR / f"{ts}_{safe_id}.md"
        content = (
            f"# Prompt dump — {task_id}\n"
            f"**Timestamp:** {ts}\n\n"
            f"---\n\n"
            f"## System Prompt\n\n```\n{system_prompt}\n```\n\n"
            f"---\n\n"
            f"## User Prompt\n\n```\n{user_prompt}\n```\n\n"
        )
        if response:
            content += f"---\n\n## LLM Response\n\n```json\n{response}\n```\n"
        path.write_text(content, encoding="utf-8")
        logger.debug("Prompt dumped to %s", path)
    except Exception:
        logger.debug("Failed to dump prompt", exc_info=True)


OVERLAYS = {
    "create_post": CREATE_POST_OVERLAY,
    "edit_post": EDIT_POST_OVERLAY,
    "create_web": CREATE_WEB_OVERLAY,
    "edit_web": EDIT_WEB_OVERLAY,
    "subscription_strategy": SUBSCRIPTION_STRATEGY_OVERLAY,
    "create_prod_line": CREATE_POST_OVERLAY,
}


def _format_reasoning_error(prefix: str, err: Any) -> str:
    if is_timeout_exception(err if isinstance(err, Exception) else None):
        return f"llm_timeout: {err}"
    return f"{prefix}: {err}"


def _is_truncated_json_error(err: Exception | None) -> bool:
    if err is None:
        return False
    text = str(err).lower()
    markers = (
        "json_invalid",
        "eof while parsing",
        "unterminated string",
        "unexpected end of json input",
    )
    return any(marker in text for marker in markers)


def _compact_prior_step_outputs(
    prior_step_outputs: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]] | None:
    """Keep only compact, high-signal metadata from previous steps.

    Full previous outputs can carry large blobs (brand_dna, cf_post_brief, etc.).
    Sending all of that back to the LLM inflates token usage and latency.
    """
    if not prior_step_outputs:
        return None

    compact: dict[str, dict[str, Any]] = {}
    for step_code, output_data in prior_step_outputs.items():
        if not isinstance(output_data, dict):
            continue

        step_summary: dict[str, Any] = {}

        data = output_data.get("data")
        if isinstance(data, dict):
            resources = data.get("resources")
            if isinstance(resources, list):
                step_summary["resources_count"] = len(resources)
            total_items = data.get("total_items")
            if isinstance(total_items, int):
                step_summary["total_items"] = total_items

        enrichment = output_data.get("enrichment")
        if isinstance(enrichment, dict):
            for key in ("surface_format", "content_pillar", "title", "objective"):
                value = enrichment.get(key)
                if isinstance(value, str) and value:
                    step_summary[key] = value
            cta = enrichment.get("cta")
            if isinstance(cta, dict) and isinstance(cta.get("channel"), str):
                step_summary["cta_channel"] = cta["channel"]

        trace = output_data.get("trace")
        if isinstance(trace, dict):
            trace_summary: dict[str, Any] = {}
            for key in ("action_code", "surface", "mode", "latency_ms"):
                value = trace.get(key)
                if isinstance(value, (str, int)):
                    trace_summary[key] = value
            if trace_summary:
                step_summary["trace"] = trace_summary

        warnings = output_data.get("warnings")
        if isinstance(warnings, list):
            warning_codes: list[str] = []
            for warning in warnings:
                if isinstance(warning, dict) and isinstance(warning.get("code"), str):
                    warning_codes.append(warning["code"])
            if warning_codes:
                step_summary["warning_codes"] = warning_codes[:5]

        if step_summary:
            compact[step_code] = step_summary

    return compact or None


def _build_prompt_context(
    ctx: InternalContext, extras_truncation: int, text_truncation_chars: int
) -> str:
    """Render the InternalContext as compact JSON for the LLM.

    Strips raw_envelope and trims long list fields to the configured cap.
    Includes the v2 anchors (brand_tokens, available_channels, brief_facts,
    requested_surface_format, prior_post) so the LLM can compose against them.
    """
    gallery_pool_shortlist = None
    if ctx.gallery_pool and ctx.gallery_pool.shortlist:
        gallery_pool_shortlist = [
            {
                "uuid": item.uuid,
                "content_url": item.content_url,
                "category": item.category,
                "description": item.description,
                "score": round(item.score, 2),
                "metadata": item.metadata,
            }
            for item in ctx.gallery_pool.shortlist
        ]

    payload: dict[str, Any] = {
        "action_code": ctx.action_code,
        "surface": ctx.surface,
        "mode": ctx.mode,
        "user_request": ctx.user_request,
        "requested_surface_format": ctx.requested_surface_format,
        "context": {
            "account_uuid": ctx.account_uuid,
            "client_name": ctx.client_name,
            "platform": ctx.platform,
            "post_id": ctx.post_id,
            "website_id": ctx.website_id,
            "section_id": ctx.section_id,
        },
        "brief": ctx.brief.model_dump() if ctx.brief else None,
        "brand_tokens": ctx.brand_tokens.model_dump(),
        "available_channels": [c.model_dump() for c in ctx.available_channels],
        "brief_facts": ctx.brief_facts.model_dump(),
        "prior_post": ctx.prior_post.model_dump() if ctx.prior_post else None,
        "user_attachments": ctx.attachments if ctx.attachments else None,
        "gallery_pool": gallery_pool_shortlist,
        "gallery": [item.model_dump() for item in ctx.gallery],
        "prior_step_outputs": _compact_prior_step_outputs(ctx.prior_step_outputs),
        "user_insights": ctx.user_insights or None,
    }
    return serialize_for_prompt(
        payload,
        truncate_lists=extras_truncation,
        truncate_text=text_truncation_chars,
    )


def _build_user_prompt(
    ctx: InternalContext, extras_truncation: int, text_truncation_chars: int
) -> str:
    overlay = OVERLAYS[ctx.action_code]
    rendered = _build_prompt_context(ctx, extras_truncation, text_truncation_chars)
    if ctx.action_code == "subscription_strategy" and ctx.subscription_jobs:
        jobs_json = serialize_for_prompt(
            {
                "subscription_jobs": [
                    {
                        "action_key": j.action_key,
                        "description": j.description,
                        "index": j.index,
                    }
                    for j in ctx.subscription_jobs
                ]
            },
            truncate_lists=extras_truncation,
            truncate_text=text_truncation_chars,
        )
        return (
            f"{overlay}\n\n"
            f"subscription_jobs:\n{jobs_json}\n\n"
            f"Context:\n{rendered}\n\n"
            f'Return the JSON object {{"items": [PostEnrichment, ...]}} now — one per job, in order.'
        )
    return f"{overlay}\n\nContext:\n{rendered}\n\nReturn the PostEnrichment JSON now."


def reason(
    envelope_data: dict[str, Any],
    gemini: GeminiClient,
    extras_truncation: int = 10,
    prompt_text_truncation_chars: int = 600,
    max_output_tokens: int = 16384,
    user_profile: UserProfile | None = None,
    usp_warning: str | None = None,
    gallery_pool: GalleryPool | None = None,
    gallery_warning: str | None = None,
) -> CallbackBody:
    started = time.time()
    warnings: list[Warning] = []

    # --- Normalize ---
    task_id = envelope_data.get("task_id", "unknown")
    logger.info(
        '"task_id=%s reason_start action_code=%s"',
        task_id,
        envelope_data.get("action_code"),
    )
    try:
        ctx, normalizer_warnings = normalize(
            envelope_data,
            user_profile=user_profile,
            usp_warning=usp_warning,
            gallery_pool=gallery_pool,
            gallery_warning=gallery_warning,
        )
    except ValueError as exc:
        logger.warning('"task_id=%s normalize_failed error=%s"', task_id, exc)
        return CallbackBody(status="FAILED", error_message=str(exc))
    warnings.extend(normalizer_warnings)
    logger.info(
        '"task_id=%s normalize_ok brief=%s gallery=%d warnings=%d"',
        ctx.task_id,
        "yes" if ctx.brief else "no",
        len(ctx.gallery),
        len(normalizer_warnings),
    )

    # --- create_web is out of scope in this iteration (edit_web is supported) ---
    if ctx.action_code == "create_web":
        return CallbackBody(
            status="FAILED",
            error_message="create_web_not_supported_in_this_iteration: MARKETER v1 does not support web creation",
        )

    # --- edit_post requires the prior post (caption/image) ----------------------
    if ctx.action_code == "edit_post" and ctx.prior_post is None:
        return CallbackBody(
            status="FAILED",
            error_message=(
                "prior_post_missing: edit_post requires prior_post (caption and/or image_url) "
                "in client_request.context.prior_post, payload.extras.prior_post, or "
                "agent_sequence.previous[*].output_data"
            ),
        )

    # --- Build prompt and call Gemini ---
    user_prompt = _build_user_prompt(
        ctx, extras_truncation, prompt_text_truncation_chars
    )
    logger.info(
        '"task_id=%s llm_call_start model=%s max_tokens=%d"',
        ctx.task_id,
        gemini.model_name,
        max_output_tokens,
    )
    enrichment, raw_text, err, usage = gemini.generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_output_tokens=max_output_tokens,
    )
    _dump_prompt(ctx.task_id, SYSTEM_PROMPT, user_prompt, raw_text)
    logger.info(
        '"task_id=%s llm_call_done ok=%s tokens_in=%d tokens_out=%d len=%d"',
        ctx.task_id,
        enrichment is not None,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        len(raw_text),
    )

    repair_attempted = False
    if enrichment is None:
        if is_timeout_exception(err):
            return CallbackBody(
                status="FAILED",
                error_message=f"llm_timeout: {err}",
            )
        logger.warning(
            "Initial LLM output invalid; attempting one repair", exc_info=err
        )
        repair_attempted = True
        repair_prompt = REPAIR_PROMPT_TEMPLATE.format(
            error=str(err) if err else "schema validation failed",
            previous_output=raw_text,
        )
        enrichment, _, err2, repair_usage = gemini.repair(
            system_prompt=SYSTEM_PROMPT,
            repair_prompt=repair_prompt,
            max_output_tokens=max_output_tokens,
        )
        usage = {
            k: usage.get(k, 0) + repair_usage.get(k, 0)
            for k in ("input_tokens", "output_tokens", "thoughts_tokens")
        }
        if enrichment is None and _is_truncated_json_error(err2 or err):
            logger.warning(
                "Repair output appears truncated; retrying with compact repair"
            )
            compact_repair_prompt = (
                repair_prompt
                + "\n\nYour previous output was truncated. Rewrite the full JSON from "
                "scratch. Be concise in all long text fields, keep strings short, and "
                "close all JSON objects and strings."
            )
            enrichment, _, err3, repair_usage2 = gemini.repair(
                system_prompt=SYSTEM_PROMPT,
                repair_prompt=compact_repair_prompt,
                max_output_tokens=max(max_output_tokens, 16384),
            )
            usage = {
                k: usage.get(k, 0) + repair_usage2.get(k, 0)
                for k in ("input_tokens", "output_tokens", "thoughts_tokens")
            }
            if enrichment is None:
                err2 = err3
        if enrichment is None:
            return CallbackBody(
                status="FAILED",
                error_message=_format_reasoning_error(
                    "schema_validation_failed", err2 or err
                ),
            )
        warnings.append(
            Warning(
                code="schema_repair_used",
                message="Schema repair succeeded after initial failure",
            )
        )

    # --- Validate + correct ---
    enrichment, validator_warnings, blocking = validate_and_correct(enrichment, ctx)
    if blocking and not repair_attempted:
        # One repair chance on action-alignment failures
        repair_attempted = True
        repair_prompt = REPAIR_PROMPT_TEMPLATE.format(
            error="; ".join(blocking),
            previous_output=enrichment.model_dump_json(),
        )
        enrichment_new, _, err2, repair_usage2 = gemini.repair(
            system_prompt=SYSTEM_PROMPT,
            repair_prompt=repair_prompt,
            max_output_tokens=max_output_tokens,
        )
        usage = {
            k: usage.get(k, 0) + repair_usage2.get(k, 0)
            for k in ("input_tokens", "output_tokens", "thoughts_tokens")
        }
        if enrichment_new is None:
            return CallbackBody(
                status="FAILED",
                error_message=_format_reasoning_error(
                    "schema_validation_failed", err2 or blocking
                ),
            )
        enrichment = enrichment_new
        enrichment, more_warnings, still_blocking = validate_and_correct(
            enrichment, ctx
        )
        validator_warnings = more_warnings
        if still_blocking:
            return CallbackBody(
                status="FAILED",
                error_message=f"schema_validation_failed: {still_blocking}",
            )
        warnings.append(
            Warning(
                code="schema_repair_used",
                message="Schema repair succeeded after action-alignment failure",
            )
        )
    elif blocking:
        return CallbackBody(
            status="FAILED",
            error_message=f"schema_validation_failed: {blocking}",
        )
    warnings.extend(validator_warnings)

    # --- Assemble CallbackBody ---
    degraded = any(
        w.code in ("brief_missing", "gallery_empty", "gallery_all_filtered")
        for w in warnings
    )
    trace = TraceInfo(
        task_id=ctx.task_id,
        action_code=ctx.action_code,
        surface=ctx.surface,
        mode=ctx.mode,
        latency_ms=int((time.time() - started) * 1000),
        gemini_model=gemini.model_name,
        repair_attempted=repair_attempted,
        degraded=degraded,
        gallery_stats=GalleryStats(
            raw_count=ctx.gallery_raw_count,
            accepted_count=len(ctx.gallery),
            rejected_count=ctx.gallery_rejected_count,
            truncated=ctx.gallery_truncated,
        ),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        thoughts_tokens=usage.get("thoughts_tokens", 0),
    )
    # Build resources: attachments always first, then LLM-chosen URLs.
    attachment_urls: list[str] = list(ctx.attachments or [])
    gallery_picks: list[str] = list(enrichment.selected_asset_urls or [])
    resources = list(dict.fromkeys(attachment_urls + gallery_picks))
    total_items = (
        len(resources) if enrichment.surface_format == "carousel" and resources else 1
    )
    cf_payload = CFPayload(
        total_items=total_items,
        client_dna=enrichment.brand_dna,
        client_request=enrichment.cf_post_brief,
        resources=resources,
    )
    return CallbackBody(
        status="COMPLETED",
        output_data=CallbackOutputData(
            data=cf_payload,
            enrichment=enrichment,
            warnings=warnings,
            trace=trace,
        ),
    )


def _assemble_single_callback(
    enrichment: PostEnrichment,
    ctx: InternalContext,
    warnings: list[Warning],
    gemini_model: str,
    repair_attempted: bool,
    usage: dict,
    latency_ms: int,
    job_index: int | None = None,
    job_action_key: str | None = None,
    total_jobs: int | None = None,
) -> CallbackBody:
    """Build a COMPLETED CallbackBody from a validated PostEnrichment."""
    degraded = any(
        w.code in ("brief_missing", "gallery_empty", "gallery_all_filtered")
        for w in warnings
    )
    trace = TraceInfo(
        task_id=ctx.task_id,
        action_code=ctx.action_code,
        surface=ctx.surface,
        mode=ctx.mode,
        latency_ms=latency_ms,
        gemini_model=gemini_model,
        repair_attempted=repair_attempted,
        degraded=degraded,
        gallery_stats=GalleryStats(
            raw_count=ctx.gallery_raw_count,
            accepted_count=len(ctx.gallery),
            rejected_count=ctx.gallery_rejected_count,
            truncated=ctx.gallery_truncated,
        ),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        thoughts_tokens=usage.get("thoughts_tokens", 0),
        job_index=job_index,
        job_action_key=job_action_key,
        total_jobs=total_jobs,
    )
    attachment_urls: list[str] = list(ctx.attachments or [])
    gallery_picks: list[str] = list(enrichment.selected_asset_urls or [])
    resources = list(dict.fromkeys(attachment_urls + gallery_picks))
    total_items = (
        len(resources) if enrichment.surface_format == "carousel" and resources else 1
    )
    cf_payload = CFPayload(
        total_items=total_items,
        client_dna=enrichment.brand_dna,
        client_request=enrichment.cf_post_brief,
        resources=resources,
    )
    return CallbackBody(
        status="COMPLETED",
        output_data=CallbackOutputData(
            data=cf_payload,
            enrichment=enrichment,
            warnings=warnings,
            trace=trace,
        ),
    )


def reason_multi(
    envelope_data: dict[str, Any],
    gemini: GeminiClient,
    extras_truncation: int = 10,
    prompt_text_truncation_chars: int = 600,
    max_output_tokens: int = 16384,
    user_profile: UserProfile | None = None,
    usp_warning: str | None = None,
    gallery_pool: GalleryPool | None = None,
    gallery_warning: str | None = None,
) -> list[tuple[CallbackBody, SubscriptionJob | None]]:
    """Multi-job variant of reason() for subscription_strategy.

    Returns one (CallbackBody, SubscriptionJob | None) pair per valid job.
    Early failures (normalize error, no valid jobs) return a single pair with
    job=None — the caller must handle this as a global failure.
    """
    started = time.time()
    warnings: list[Warning] = []

    # --- Normalize ---
    task_id = envelope_data.get("task_id", "unknown")
    logger.info('"task_id=%s reason_multi_start"', task_id)
    try:
        ctx, normalizer_warnings = normalize(
            envelope_data,
            user_profile=user_profile,
            usp_warning=usp_warning,
            gallery_pool=gallery_pool,
            gallery_warning=gallery_warning,
        )
    except ValueError as exc:
        logger.warning('"task_id=%s normalize_failed error=%s"', task_id, exc)
        return [(CallbackBody(status="FAILED", error_message=str(exc)), None)]
    warnings.extend(normalizer_warnings)
    logger.info(
        '"task_id=%s normalize_ok subscription_jobs=%d brief=%s gallery=%d warnings=%d"',
        ctx.task_id,
        len(ctx.subscription_jobs or []),
        "yes" if ctx.brief else "no",
        len(ctx.gallery),
        len(normalizer_warnings),
    )

    if not ctx.subscription_jobs:
        return [
            (
                CallbackBody(
                    status="FAILED", error_message="no valid subscription jobs"
                ),
                None,
            )
        ]

    jobs = ctx.subscription_jobs
    total_jobs = len(jobs)

    # --- Build prompt and call Gemini (single call for all jobs) ---
    user_prompt = _build_user_prompt(
        ctx, extras_truncation, prompt_text_truncation_chars
    )

    # Scale max_output_tokens for multi-job (more items = more tokens needed)
    scaled_tokens = min(max_output_tokens * total_jobs, 65536)

    logger.info(
        '"task_id=%s llm_call_start model=%s max_tokens=%d items=%d"',
        ctx.task_id,
        gemini.model_name,
        scaled_tokens,
        total_jobs,
    )
    _single_parse, raw_text, err, usage = gemini.generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_output_tokens=scaled_tokens,
    )
    _dump_prompt(ctx.task_id, SYSTEM_PROMPT, user_prompt, raw_text)
    logger.info(
        '"task_id=%s llm_call_done ok=%s tokens_in=%d tokens_out=%d len=%d"',
        ctx.task_id,
        err is None,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        len(raw_text),
    )

    # For multi-job, we need to parse as MultiEnrichmentOutput, not PostEnrichment.
    # The generate_structured call tries PostEnrichment first — ignore that parse.
    # Re-parse the raw_text as MultiEnrichmentOutput.
    multi_output: MultiEnrichmentOutput | None = None
    parse_err: Exception | None = err
    if raw_text:
        try:
            multi_output = MultiEnrichmentOutput.model_validate_json(raw_text)
        except Exception as exc:
            parse_err = exc
            multi_output = None

    # --- Repair cycle ---
    repair_attempted = False
    if multi_output is None:
        if is_timeout_exception(parse_err):
            return [
                (
                    CallbackBody(
                        status="FAILED", error_message=f"llm_timeout: {parse_err}"
                    ),
                    job,
                )
                for job in jobs
            ]
        logger.warning(
            "Multi-output LLM parse failed; attempting repair", exc_info=parse_err
        )
        repair_attempted = True
        repair_prompt = REPAIR_PROMPT_TEMPLATE.format(
            error=str(parse_err) if parse_err else "schema validation failed",
            previous_output=raw_text,
        )
        _, repair_text, err2, repair_usage = gemini.repair(
            system_prompt=SYSTEM_PROMPT,
            repair_prompt=repair_prompt,
            max_output_tokens=scaled_tokens,
        )
        usage = {
            k: usage.get(k, 0) + repair_usage.get(k, 0)
            for k in ("input_tokens", "output_tokens", "thoughts_tokens")
        }
        if repair_text:
            try:
                multi_output = MultiEnrichmentOutput.model_validate_json(repair_text)
            except Exception as exc2:
                err2 = exc2
        if multi_output is None:
            error_msg = _format_reasoning_error(
                "schema_validation_failed", err2 or parse_err
            )
            return [
                (CallbackBody(status="FAILED", error_message=error_msg), job)
                for job in jobs
            ]
        warnings.append(
            Warning(
                code="schema_repair_used",
                message="Schema repair succeeded after initial failure",
            )
        )

    # --- Validate each enrichment and assemble callbacks ---
    latency_ms = int((time.time() - started) * 1000)
    results: list[tuple[CallbackBody, SubscriptionJob | None]] = []

    for idx, job in enumerate(jobs):
        if idx >= len(multi_output.items):
            # LLM returned fewer items than expected
            results.append(
                (
                    CallbackBody(
                        status="FAILED",
                        error_message=f"llm_returned_fewer_items: expected {total_jobs}, got {len(multi_output.items)}",
                    ),
                    job,
                )
            )
            continue

        enrichment = multi_output.items[idx]
        item_warnings = list(warnings)  # shared warnings + per-item

        enrichment, validator_warnings, blocking = validate_and_correct(enrichment, ctx)
        item_warnings.extend(validator_warnings)

        if blocking:
            results.append(
                (
                    CallbackBody(
                        status="FAILED",
                        error_message=f"schema_validation_failed: {blocking}",
                    ),
                    job,
                )
            )
            continue

        cb = _assemble_single_callback(
            enrichment=enrichment,
            ctx=ctx,
            warnings=item_warnings,
            gemini_model=gemini.model_name,
            repair_attempted=repair_attempted,
            usage=usage,
            latency_ms=latency_ms,
            job_index=job.index,
            job_action_key=job.action_key,
            total_jobs=total_jobs,
        )
        results.append((cb, job))

    return results


def dry_run_prompt(
    envelope_data: dict[str, Any],
    extras_truncation: int = 10,
    prompt_text_truncation_chars: int = 600,
) -> str:
    """Build and return the prompt that WOULD be sent — for debugging without burning LLM calls."""
    ctx, _ = normalize(envelope_data)
    return _build_user_prompt(ctx, extras_truncation, prompt_text_truncation_chars)
