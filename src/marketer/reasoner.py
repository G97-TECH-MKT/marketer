"""Reasoner: orchestrates normalize -> prompt -> Gemini -> validate -> assemble.

Sync pipeline for the MVP vertical slice. Returns CallbackBody shape.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from marketer.llm.gemini import GeminiClient, serialize_for_prompt
from marketer.llm.prompts.create_post import CREATE_POST_OVERLAY
from marketer.llm.prompts.create_web import CREATE_WEB_OVERLAY
from marketer.llm.prompts.edit_post import EDIT_POST_OVERLAY
from marketer.llm.prompts.edit_web import EDIT_WEB_OVERLAY
from marketer.llm.prompts.repair import REPAIR_PROMPT_TEMPLATE
from marketer.llm.prompts.system import SYSTEM_PROMPT
from marketer.normalizer import normalize
from marketer.schemas.enrichment import (
    CFPayload,
    CallbackBody,
    CallbackOutputData,
    GalleryStats,
    TraceInfo,
    Warning,
)
from marketer.schemas.internal_context import GalleryPool, InternalContext
from marketer.user_profile import UserProfile
from marketer.validator import validate_and_correct

logger = logging.getLogger(__name__)

OVERLAYS = {
    "create_post": CREATE_POST_OVERLAY,
    "edit_post": EDIT_POST_OVERLAY,
    "create_web": CREATE_WEB_OVERLAY,
    "edit_web": EDIT_WEB_OVERLAY,
}


def _build_prompt_context(ctx: InternalContext, extras_truncation: int) -> str:
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
        "prior_step_outputs": ctx.prior_step_outputs or None,
        "user_insights": ctx.user_insights or None,
    }
    return serialize_for_prompt(payload, truncate_lists=extras_truncation)


def _build_user_prompt(ctx: InternalContext, extras_truncation: int) -> str:
    overlay = OVERLAYS[ctx.action_code]
    rendered = _build_prompt_context(ctx, extras_truncation)
    return f"{overlay}\n\nContext:\n{rendered}\n\nReturn the PostEnrichment JSON now."


def reason(
    envelope_data: dict[str, Any],
    gemini: GeminiClient,
    extras_truncation: int = 10,
    max_output_tokens: int = 16384,
    user_profile: UserProfile | None = None,
    usp_warning: str | None = None,
    gallery_pool: GalleryPool | None = None,
    gallery_warning: str | None = None,
) -> CallbackBody:
    started = time.time()
    warnings: list[Warning] = []

    # --- Normalize ---
    try:
        ctx, normalizer_warnings = normalize(
            envelope_data,
            user_profile=user_profile,
            usp_warning=usp_warning,
            gallery_pool=gallery_pool,
            gallery_warning=gallery_warning,
        )
    except ValueError as exc:
        # Unsupported action_code or missing required field → FAIL the task
        return CallbackBody(status="FAILED", error_message=str(exc))
    warnings.extend(normalizer_warnings)

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
    user_prompt = _build_user_prompt(ctx, extras_truncation)
    enrichment, raw_text, err, usage = gemini.generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_output_tokens=max_output_tokens,
    )

    repair_attempted = False
    if enrichment is None:
        logger.warning(
            "Initial LLM output invalid; attempting one repair", exc_info=err
        )
        repair_attempted = True
        repair_prompt = REPAIR_PROMPT_TEMPLATE.format(
            error=str(err) if err else "schema validation failed",
            previous_output=raw_text,
        )
        enrichment, _, err2, repair_usage = gemini.repair(
            system_prompt=SYSTEM_PROMPT, repair_prompt=repair_prompt
        )
        usage = {
            k: usage.get(k, 0) + repair_usage.get(k, 0)
            for k in ("input_tokens", "output_tokens", "thoughts_tokens")
        }
        if enrichment is None:
            return CallbackBody(
                status="FAILED",
                error_message=f"schema_validation_failed: {err2 or err}",
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
            system_prompt=SYSTEM_PROMPT, repair_prompt=repair_prompt
        )
        usage = {
            k: usage.get(k, 0) + repair_usage2.get(k, 0)
            for k in ("input_tokens", "output_tokens", "thoughts_tokens")
        }
        if enrichment_new is None:
            return CallbackBody(
                status="FAILED",
                error_message=f"schema_validation_failed: {err2 or blocking}",
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
    # Build resources in priority order — deduped, first-seen wins.
    # 1. user_attachments: always forwarded to CF unconditionally (user explicitly chose them)
    # 2. gallery_pool picks: LLM-selected items from the pre-scored shortlist
    # 3. legacy visual_selection: ROUTER-gate images (fallback)
    attachment_urls: list[str] = list(ctx.attachments or [])
    gallery_picks: list[str] = [img.content_url for img in (enrichment.selected_images or [])]
    legacy_urls: list[str] = enrichment.visual_selection.recommended_asset_urls or []
    resources = list(dict.fromkeys(attachment_urls + gallery_picks + legacy_urls))
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


def dry_run_prompt(envelope_data: dict[str, Any], extras_truncation: int = 10) -> str:
    """Build and return the prompt that WOULD be sent — for debugging without burning LLM calls."""
    ctx, _ = normalize(envelope_data)
    return _build_user_prompt(ctx, extras_truncation)
