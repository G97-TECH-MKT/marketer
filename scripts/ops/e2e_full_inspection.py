#!/usr/bin/env python3
"""Comprehensive E2E pipeline inspection — Nubiex golden input.

Runs every stage of the marketer pipeline with full intermediate state logged:
  1.  INPUT   — envelope summary, brief fields, image catalog
  2.  USP     — Memory Gateway fetch result + field overrides applied
  3.  CONTEXT — normalized InternalContext (brief, gallery, channels, insights)
  4.  PROMPT  — what the LLM actually sees (truncated)
  5.  OUTPUT  — enrichment result (caption, visual selection, brand DNA, BI)
  6.  DB      — raw_briefs / strategies / jobs after persistence

Prerequisites:
  - Docker Postgres running: docker compose up -d postgres
  - DATABASE_URL in .env pointing to port 5433
  - GEMINI_API_KEY set
  - alembic upgrade head applied

Costs one real Gemini LLM call (~10-15 s).

Usage:
    python scripts/ops/e2e_full_inspection.py
    python scripts/ops/e2e_full_inspection.py --account-uuid <real-uuid>
    python scripts/ops/e2e_full_inspection.py --description "Crea una story..."
    python scripts/ops/e2e_full_inspection.py --no-db   # skip DB persistence
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

os.environ.setdefault("DB_USE_NULL_POOL", "true")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from marketer.config import load_settings  # noqa: E402
from marketer.db.engine import is_configured as _db_is_configured  # noqa: E402
from marketer.llm.gemini import GeminiClient  # noqa: E402
from marketer.normalizer import normalize  # noqa: E402
from marketer.persistence import (  # noqa: E402
    PersistCtx,
    persist_on_complete,
    persist_on_ingest,
    persist_user_profile,
)
from marketer.reasoner import _build_prompt_context, reason  # noqa: E402
from marketer.user_profile import fetch_user_profile  # noqa: E402

# ──────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ──────────────────────────────────────────────────────────────────────────────

W = 78  # terminal width


def _sep(title: str = "", char: str = "═") -> None:
    if title:
        side = (W - len(title) - 2) // 2
        print(f"\n{char * side} {title} {char * (W - side - len(title) - 2)}")
    else:
        print(char * W)


def _h(label: str, value: Any, indent: int = 2) -> None:
    """Print a key: value line, wrapping long strings."""
    prefix = " " * indent + f"{label}: "
    text = str(value) if value is not None else "(none)"
    if len(prefix) + len(text) <= W:
        print(f"{prefix}{text}")
    else:
        lines = textwrap.wrap(text, width=W - indent - 2)
        print(f"{prefix}{lines[0]}")
        for line in lines[1:]:
            print(" " * (indent + 2) + line)


def _jblock(obj: Any, label: str = "", indent: int = 2, max_chars: int = 2000) -> None:
    raw = json.dumps(obj, ensure_ascii=False, default=str)
    if len(raw) > max_chars:
        raw = raw[:max_chars] + " … [truncated]"
    if label:
        print(f"{' ' * indent}{label}:")
        for line in raw.splitlines():
            print(f"{' ' * (indent + 2)}{line}")
    else:
        for line in raw.splitlines():
            print(f"{' ' * indent}{line}")


# ──────────────────────────────────────────────────────────────────────────────
# Section printers
# ──────────────────────────────────────────────────────────────────────────────


def _print_input(envelope: dict[str, Any]) -> None:
    _sep("1. INPUT ENVELOPE")
    payload = envelope.get("payload") or {}
    ctx = payload.get("context") or {}
    cr = payload.get("client_request") or {}
    gates = payload.get("action_execution_gates") or {}

    _h("task_id", envelope.get("task_id"))
    _h("action_code", envelope.get("action_code"))
    _h("correlation_id", envelope.get("correlation_id"))
    print()
    _h("account_uuid", ctx.get("account_uuid"))
    _h("client_name", ctx.get("client_name"))
    _h("platform", ctx.get("platform"))
    print()
    _h("client_request", cr.get("description"))

    # Brief gate
    brief_gate = gates.get("brief") or {}
    if brief_gate.get("passed"):
        bd = (brief_gate.get("response") or {}).get("data") or {}
        profile = bd.get("profile") or {}
        brief_obj = bd.get("brief") or {}
        fv = (brief_obj.get("form_values") or {}) if isinstance(brief_obj, dict) else {}
        print()
        print("  Brief gate [passed]:")
        _h(
            "business_name",
            fv.get("FIELD_COMPANY_NAME") or profile.get("business_name"),
            4,
        )
        _h("category", fv.get("FIELD_COMPANY_CATEGORY"), 4)
        _h("country", fv.get("FIELD_COUNTRY"), 4)
        _h(
            "tone / comm style",
            profile.get("tone") or fv.get("FIELD_COMMUNICATION_STYLE"),
            4,
        )
        _h("value_proposition", fv.get("FIELD_VALUE_PROPOSITION"), 4)
        _h("keywords", fv.get("FIELD_KEYWORDS_TAGS_INPUT"), 4)
        _h("colors", fv.get("FIELD_COLOR_LIST_PICKER"), 4)
    else:
        print("  Brief gate: NOT PASSED")

    # Image catalog
    img_gate = gates.get("image_catalog") or {}
    if img_gate.get("passed"):
        images = (img_gate.get("response") or {}).get("data") or []
        print()
        print(f"  Image catalog [passed] — {len(images)} image(s):")
        for i, img in enumerate(images, 1):
            _h(f"  [{i}] {img.get('name', '?')}", img.get("description"), 4)
            _h("      tags", img.get("tags"), 4)
            _h("      used_previously", img.get("used_previously"), 4)
            _h("      url", img.get("url"), 4)


def _print_usp(
    user_profile: Any, usp_warning: str | None, account_uuid: str | None
) -> None:
    _sep("2. USP MEMORY GATEWAY")
    _h("account_uuid queried", account_uuid)
    _h("usp_warning", usp_warning or "(none — fetch succeeded)")

    if user_profile is None:
        print("  Result: fetch failed or skipped — no user profile available")
        return

    if user_profile.identity is None:
        print("  Result: account not found in USP (identity=null)")
        print(f"  fetched_at: {user_profile.fetched_at}")
        if user_profile.insights:
            print(f"  insights: {len(user_profile.insights)} active")
        return

    identity = user_profile.identity
    print(f"  fetched_at: {user_profile.fetched_at}")
    print()
    print("  Identity:")
    _h("uuid", identity.uuid, 4)
    print()
    print("    company:")
    for k, v in (identity.company or {}).items():
        if v:
            _h(k, v, 6)
    print()
    print("    brand:")
    for k, v in (identity.brand or {}).items():
        if v:
            _h(k, v, 6)
    print()
    print("    socialMedia:")
    for k, v in (identity.social_media or {}).items():
        if v:
            _h(k, v, 6)

    insights = user_profile.insights
    print()
    if insights:
        print(f"  Insights ({len(insights)} active, sorted by confidence desc):")
        for ins in insights[:10]:
            _h(f"  [{ins.key}]", ins.insight, 4)
            _h("    confidence", ins.confidence, 4)
            _h("    source", ins.source_identifier, 4)
    else:
        print("  Insights: (none)")


def _print_context(ctx: Any, warnings: list[Any]) -> None:
    _sep("3. NORMALIZED InternalContext")

    brief = ctx.brief
    if brief:
        print("  FlatBrief (post-USP merge):")
        _h("business_name", brief.business_name, 4)
        _h("category", brief.category, 4)
        _h("country", brief.country, 4)
        _h("tone", brief.tone, 4)
        _h("communication_language", brief.communication_language, 4)
        _h("value_proposition", brief.value_proposition, 4)
        _h(
            "business_description",
            (brief.business_description or "")[:120] + "…"
            if brief.business_description and len(brief.business_description) > 120
            else brief.business_description,
            4,
        )
        _h(
            "target_customer",
            (brief.target_customer or "")[:120] + "…"
            if brief.target_customer and len(brief.target_customer) > 120
            else brief.target_customer,
            4,
        )
        _h("colors", brief.colors, 4)
        _h("keywords", brief.keywords, 4)
        _h("website_url", brief.website_url, 4)
        _h("has_brand_material", brief.has_brand_material, 4)
        if brief.extras:
            _h("extras keys", list(brief.extras.keys()), 4)

    print()
    bt = ctx.brand_tokens
    print("  BrandTokens:")
    _h("palette", bt.palette, 4)
    _h("font_style", bt.font_style, 4)
    _h("design_style", bt.design_style, 4)
    _h("post_content_style", bt.post_content_style, 4)
    _h("communication_style", bt.communication_style, 4)
    _h("voice_from / voice_to", f"{bt.voice_from} → {bt.voice_to}", 4)

    print()
    print(f"  AvailableChannels ({len(ctx.available_channels)}):")
    for ch in ctx.available_channels:
        _h(ch.channel, ch.url_or_handle or ch.label_hint, 4)

    print()
    bf = ctx.brief_facts
    print("  BriefFacts:")
    _h("urls", bf.urls, 4)
    _h("phones", bf.phones, 4)
    _h("emails", bf.emails, 4)
    _h("prices", bf.prices, 4)
    _h("hex_colors", bf.hex_colors, 4)

    print()
    print(
        f"  Gallery ({len(ctx.gallery)} images, raw={ctx.gallery_raw_count}, rejected={ctx.gallery_rejected_count}):"
    )
    for i, img in enumerate(ctx.gallery, 1):
        _h(f"  [{i}] {img.name or img.url[:50]}", img.description, 4)
        _h("      role", img.role, 4)
        _h("      tags", img.tags, 4)
        _h("      used_previously", img.used_previously, 4)

    if ctx.user_insights:
        print()
        print(f"  UserInsights fed to LLM ({len(ctx.user_insights)}):")
        for ins in ctx.user_insights[:5]:
            _h(f"  [{ins.get('key')}]", ins.get("insight"), 4)
            _h("    confidence", ins.get("confidence"), 4)

    print()
    if warnings:
        print(f"  Warnings ({len(warnings)}):")
        for w in warnings:
            _h(w.code, w.message, 4)
    else:
        print("  Warnings: (none)")

    print()
    _h("requested_surface_format", ctx.requested_surface_format)
    _h("surface / mode", f"{ctx.surface} / {ctx.mode}")
    _h("platform", ctx.platform)


def _print_prompt(
    ctx: Any, extras_truncation: int = 10, text_truncation_chars: int = 600
) -> None:
    _sep("4. GEMINI PROMPT CONTEXT (what the LLM sees)")
    prompt_ctx = _build_prompt_context(ctx, extras_truncation, text_truncation_chars)
    max_chars = 1500
    if len(prompt_ctx) > max_chars:
        print(f"  [Context JSON — {len(prompt_ctx)} chars, showing first {max_chars}]")
        print()
        print(prompt_ctx[:max_chars])
        print("  … [truncated — full context sent to Gemini]")
    else:
        print(f"  [Context JSON — {len(prompt_ctx)} chars]")
        print()
        print(prompt_ctx)


def _print_enrichment(callback: Any) -> None:
    _sep("5. ENRICHMENT OUTPUT")
    _h("callback status", callback.status)

    if callback.status == "FAILED":
        _h("error_message", callback.error_message)
        return

    od = callback.output_data
    if od is None:
        print("  output_data: (none)")
        return

    enr = od.enrichment
    _h("schema_version", enr.schema_version)
    _h("surface_format", enr.surface_format)
    _h("content_pillar", enr.content_pillar)
    _h("title", enr.title)
    _h("objective", enr.objective)

    print()
    print("  Caption:")
    _h("hook", enr.caption.hook, 4)
    _h("body", enr.caption.body, 4)
    _h("cta_line", enr.caption.cta_line, 4)

    print()
    print("  CTA:")
    _h("channel", enr.cta.channel, 4)
    _h("url_or_handle", enr.cta.url_or_handle, 4)
    _h("label", enr.cta.label, 4)

    print()
    hs = enr.hashtag_strategy
    print("  Hashtag strategy:")
    _h("intent", hs.intent, 4)
    _h("suggested_volume", hs.suggested_volume, 4)
    _h("themes", hs.themes, 4)

    print()
    print("  Visual selection:")
    vs = enr.visual_selection
    if vs:
        _h("primary_url", getattr(vs, "primary_url", None), 4)
        _h("primary_rationale", getattr(vs, "primary_rationale", None), 4)
        _h("fallback_url", getattr(vs, "fallback_url", None), 4)
        _h("alt_text", getattr(vs, "alt_text", None), 4)

    print()
    print("  Image brief (for generation if no gallery image):")
    ib = enr.image
    _h("concept", ib.concept, 4)
    _h("generation_prompt", ib.generation_prompt, 4)
    _h("alt_text", ib.alt_text, 4)

    print()
    print("  Brand DNA (first 500 chars):")
    bd = enr.brand_dna or ""
    print(f"    {bd[:500]}{'…' if len(bd) > 500 else ''}")

    print()
    bi = enr.brand_intelligence
    print("  Brand Intelligence:")
    _h("business_taxonomy", bi.business_taxonomy, 4)
    _h("funnel_stage_target", bi.funnel_stage_target, 4)
    _h("voice_register", bi.voice_register, 4)
    _h("emotional_beat", bi.emotional_beat, 4)
    _h("rhetorical_device", bi.rhetorical_device, 4)
    _h(
        "audience_persona",
        (bi.audience_persona or "")[:120] + "…"
        if bi.audience_persona and len(bi.audience_persona) > 120
        else bi.audience_persona,
        4,
    )
    _h(
        "unfair_advantage",
        (bi.unfair_advantage or "")[:120] + "…"
        if bi.unfair_advantage and len(bi.unfair_advantage) > 120
        else bi.unfair_advantage,
        4,
    )
    _h("risk_flags", bi.risk_flags, 4)

    print()
    sd = enr.strategic_decisions
    print("  Strategic decisions:")
    _h("surface_format.chosen", sd.surface_format.chosen, 4)
    _h("angle.chosen", sd.angle.chosen, 4)
    _h("voice.chosen", sd.voice.chosen, 4)

    print()
    tr = od.trace
    print("  Trace:")
    _h("task_id", tr.task_id, 4)
    _h("latency_ms", tr.latency_ms, 4)
    _h("gemini_model", tr.gemini_model, 4)
    _h("repair_attempted", tr.repair_attempted, 4)
    _h("degraded", tr.degraded, 4)
    gs = tr.gallery_stats
    if gs:
        _h("gallery_stats", gs.model_dump() if hasattr(gs, "model_dump") else gs, 4)

    print()
    if od.warnings:
        print(f"  Output warnings ({len(od.warnings)}):")
        for w in od.warnings:
            _h(
                w.get("code", "?") if isinstance(w, dict) else w.code,
                w.get("message", "") if isinstance(w, dict) else w.message,
                4,
            )

    print()
    conf = enr.confidence
    if conf:
        _h("confidence", conf.model_dump() if hasattr(conf, "model_dump") else conf, 4)


def _print_db(task_uuid: Any, account_uuid: Any, db_url: str) -> None:
    _sep("6. DATABASE STATE")
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from marketer.db.models import Job, RawBrief, Strategy

    def _sync_url(url: str) -> str:
        if url.startswith("postgresql+asyncpg://"):
            return "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    try:
        from uuid import UUID

        engine = create_engine(_sync_url(db_url))
        with Session(engine) as session:
            raw_brief = session.execute(
                select(RawBrief).where(RawBrief.router_task_id == task_uuid)
            ).scalar_one_or_none()

            if raw_brief is None:
                print(
                    "  raw_briefs: NO ROW FOUND (DB persistence may have been skipped)"
                )
                return

            print("  raw_briefs:")
            _h("id", raw_brief.id, 4)
            _h("status", raw_brief.status, 4)
            _h("received_at", raw_brief.received_at, 4)
            _h("processed_at", raw_brief.processed_at, 4)

            up = raw_brief.user_profile
            if up is None:
                _h("user_profile", "(null — USP data not persisted)", 4)
            else:
                identity_stored = up.get("identity") or {}
                insights_stored = up.get("insights") or []
                _h("user_profile.fetched_at", up.get("fetched_at"), 4)
                _h(
                    "user_profile.identity.company.name",
                    (identity_stored.get("company") or {}).get("name"),
                    4,
                )
                _h(
                    "user_profile.identity.brand.colors",
                    (identity_stored.get("brand") or {}).get("colors"),
                    4,
                )
                _h("user_profile.insights count", len(insights_stored), 4)
                if insights_stored:
                    _h("user_profile.insights[0].key", insights_stored[0].get("key"), 4)

            print()
            account_uuid_uuid = UUID(str(account_uuid)) if account_uuid else None
            strategy = None
            if account_uuid_uuid:
                strategy = session.execute(
                    select(Strategy).where(
                        Strategy.user_id == account_uuid_uuid,
                        Strategy.is_active.is_(True),
                    )
                ).scalar_one_or_none()

            if strategy is None:
                print(
                    "  strategies: NO ACTIVE STRATEGY (first run may skip if action unknown)"
                )
            else:
                print("  strategies:")
                _h("id", strategy.id, 4)
                _h("version", strategy.version, 4)
                _h("is_active", strategy.is_active, 4)
                _h("created_at", strategy.created_at, 4)
                bi = strategy.brand_intelligence or {}
                print("    brand_intelligence:")
                for k in (
                    "business_taxonomy",
                    "voice_register",
                    "emotional_beat",
                    "audience_persona",
                    "unfair_advantage",
                    "rhetorical_device",
                ):
                    v = bi.get(k)
                    if isinstance(v, str) and len(v) > 100:
                        v = v[:97] + "…"
                    _h(k, v, 6)

            print()
            job = session.execute(
                select(Job).where(Job.raw_brief_id == raw_brief.id)
            ).scalar_one_or_none()

            if job is None:
                print("  jobs: NO JOB ROW")
            else:
                print("  jobs:")
                _h("id", job.id, 4)
                _h("status", job.status, 4)
                _h("latency_ms", job.latency_ms, 4)
                out = job.output or {}
                _h("output.status", out.get("status"), 4)
                od = out.get("output_data") or {}
                enr = od.get("enrichment") or {}
                cap = enr.get("caption") or {}
                _h("output.caption.hook", cap.get("hook"), 4)
                cf = od.get("data") or {}
                cr = cf.get("client_request") or ""
                _h(
                    "output.cf.client_request (preview)",
                    cr[:120] if cr else "(none)",
                    4,
                )

        engine.dispose()
    except Exception as exc:
        print(f"  DB read error: {exc}")
        import traceback

        traceback.print_exc()


# ──────────────────────────────────────────────────────────────────────────────
# Main async pipeline
# ──────────────────────────────────────────────────────────────────────────────


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()

    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set — aborting.", file=sys.stderr)
        return 1

    use_db = not args.no_db and _db_is_configured()
    if not use_db and not args.no_db:
        print("WARNING: DATABASE_URL not configured — DB steps will be skipped.")

    # ── Load golden envelope ──────────────────────────────────────────────────
    fixture_path = (
        ROOT / "tests" / "fixtures" / "envelopes" / "nubiex_golden_input.json"
    )
    envelope = json.loads(fixture_path.read_text(encoding="utf-8"))

    task_uuid = uuid4()
    account_uuid_str = args.account_uuid or str(uuid4())
    envelope["task_id"] = str(task_uuid)
    envelope["callback_url"] = f"https://example.test/cb/{task_uuid}"
    envelope["payload"]["context"]["account_uuid"] = account_uuid_str
    if args.description:
        envelope["payload"]["client_request"]["description"] = args.description

    # ── 1. INPUT ──────────────────────────────────────────────────────────────
    _print_input(envelope)

    # ── 2. USP fetch ─────────────────────────────────────────────────────────
    usp_configured = bool(settings.usp_api_key and settings.usp_graphql_url)
    if not usp_configured or not account_uuid_str:
        user_profile = None
        usp_warning: str | None = "user_profile_skipped"
    else:
        print(f"\n[Fetching USP for account_uuid={account_uuid_str} …]")
        user_profile = await fetch_user_profile(
            account_uuid=account_uuid_str,
            endpoint=settings.usp_graphql_url,
            api_key=settings.usp_api_key,
            timeout=settings.usp_timeout_seconds,
        )
        if user_profile is None:
            usp_warning = "user_profile_unavailable"
        elif user_profile.identity is None:
            usp_warning = "user_profile_not_found"
        else:
            usp_warning = None

    _print_usp(user_profile, usp_warning, account_uuid_str)

    # ── 3. Normalize ──────────────────────────────────────────────────────────
    ctx, warnings = normalize(
        envelope,
        user_profile=user_profile,
        usp_warning=usp_warning,
    )
    _print_context(ctx, warnings)

    # ── 4. Prompt preview ─────────────────────────────────────────────────────
    _print_prompt(ctx, extras_truncation=settings.extras_list_truncation)

    # ── DB: ingest persistence before LLM call ────────────────────────────────
    pctx: PersistCtx | None = None
    if use_db:
        pctx = await persist_on_ingest(envelope)
        if pctx is None:
            print(
                "\n[DB] persist_on_ingest returned None — check action_types catalog or DB conn"
            )
        else:
            print(f"\n[DB] raw_brief created: id={pctx.raw_brief_id}")

        if pctx is not None and user_profile is not None:
            await persist_user_profile(pctx.raw_brief_id, user_profile)
            print("[DB] user_profile persisted to raw_briefs.user_profile")

    # ── 5. Gemini call ────────────────────────────────────────────────────────
    _sep("Calling Gemini LLM…")
    print(f"  model={settings.gemini_model}")
    print(f"  timeout={settings.llm_timeout_seconds}s")
    print("  This makes one real API call (~10-15 s).")
    t0 = time.time()

    try:
        gemini = GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        # reason() is synchronous. Fine to call directly from async context
        # (no other coroutines are competing here in a script).
        callback = reason(
            envelope,
            gemini=gemini,
            extras_truncation=settings.extras_list_truncation,
            user_profile=user_profile,
            usp_warning=usp_warning,
        )
    except Exception as exc:
        import traceback

        print(f"\nERROR in reason(): {exc}", file=sys.stderr)
        traceback.print_exc()
        return 2

    elapsed_ms = int((time.time() - t0) * 1000)
    print(f"  Done in {elapsed_ms} ms")

    _print_enrichment(callback)

    # ── DB: completion persistence ─────────────────────────────────────────────
    if use_db and pctx is not None:
        await persist_on_complete(pctx, envelope, callback, elapsed_ms)
        print("\n[DB] persist_on_complete done (strategy upserted, job row created)")

    # ── 6. DB read-back ───────────────────────────────────────────────────────
    if use_db:
        from uuid import UUID

        _print_db(
            task_uuid,
            UUID(account_uuid_str) if account_uuid_str else None,
            settings.database_url,
        )

    _sep("DONE", "═")
    print(f"\n  task_id   : {task_uuid}")
    print(f"  account   : {account_uuid_str}")
    print(f"  total_ms  : {elapsed_ms}")
    print(f"  status    : {callback.status}")
    if use_db and pctx:
        print(f"  raw_brief : {pctx.raw_brief_id}")
    print()

    # Refresh inspector if DB was used
    if use_db:
        try:
            inspector_script = Path(__file__).resolve().parent / "inspector.py"
            if inspector_script.exists():
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from inspector import fetch_runs, render_html  # type: ignore

                out = ROOT / "reports" / "inspector.html"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(render_html(fetch_runs(5)), encoding="utf-8")
                print(f"  Inspector refreshed → {out}")
        except Exception:
            pass

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-uuid", help="Real account UUID to query USP with")
    parser.add_argument("--description", help="Override client_request.description")
    parser.add_argument(
        "--no-db", action="store_true", help="Skip DB persistence steps"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
