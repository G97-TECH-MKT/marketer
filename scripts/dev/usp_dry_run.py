#!/usr/bin/env python3
"""Quick USP dry run — no DB, no Gemini.

Fetches user profile from the USP Memory Gateway and runs normalize()
to show exactly what context the agent would receive.

Usage:
    python scripts/dev/usp_dry_run.py
    python scripts/dev/usp_dry_run.py --account-uuid <real-uuid>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

os.environ["DATABASE_URL"] = ""  # no DB

from marketer.config import load_settings  # noqa: E402
from marketer.normalizer import normalize  # noqa: E402
from marketer.user_profile import fetch_user_profile  # noqa: E402


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--account-uuid", default=None, help="Override account_uuid")
    args = parser.parse_args()

    settings = load_settings()

    # ── Load golden envelope ──────────────────────────────────────────────────
    fixture = ROOT / "tests" / "fixtures" / "envelopes" / "nubiex_golden_input.json"
    envelope = json.loads(fixture.read_text(encoding="utf-8"))
    account_uuid = args.account_uuid or envelope["payload"]["context"]["account_uuid"]
    envelope["payload"]["context"]["account_uuid"] = account_uuid

    print(f"\naccount_uuid : {account_uuid}")
    print(f"USP endpoint : {settings.usp_graphql_url or '(not configured)'}")
    print(f"USP key set  : {'yes' if settings.usp_api_key else 'no'}")

    # ── USP fetch ─────────────────────────────────────────────────────────────
    usp_configured = bool(settings.usp_api_key and settings.usp_graphql_url)
    if not usp_configured:
        print("\nUSP not configured — set USP_GRAPHQL_URL and USP_API_KEY in .env")
        user_profile = None
        usp_warning = "user_profile_skipped"
    else:
        print("\nFetching from USP …")
        user_profile = await fetch_user_profile(
            account_uuid=account_uuid,
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

    # ── Print USP result ──────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("USP RESULT")
    print("=" * 60)
    print(f"usp_warning  : {usp_warning or '(none — success)'}")

    if user_profile is None:
        print("user_profile : None (fetch failed or skipped)")
    elif user_profile.identity is None:
        print(f"user_profile : fetched at {user_profile.fetched_at}")
        print("identity     : null (account not found in USP)")
        print("              This UUID is not registered in USP yet.")
        print("              Pass a real --account-uuid to get identity data.")
    else:
        identity = user_profile.identity
        print(f"fetched_at   : {user_profile.fetched_at}")
        print("\ncompany:")
        for k, v in identity.company.items():
            if v:
                print(f"  {k}: {v}")
        print("\nbrand:")
        for k, v in identity.brand.items():
            if v:
                print(f"  {k}: {v}")
        print("\nsocialMedia:")
        for k, v in identity.social_media.items():
            if v:
                print(f"  {k}: {v}")
        insights = user_profile.insights
        print(f"\ninsights ({len(insights)} active):")
        for ins in insights:
            print(f"  [{ins.confidence}] {ins.key}: {ins.insight[:80]}")

    # ── Normalize ─────────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("NORMALIZED CONTEXT")
    print("=" * 60)
    ctx, warnings = normalize(
        envelope, user_profile=user_profile, usp_warning=usp_warning
    )

    brief = ctx.brief
    if brief:
        print("\nFlatBrief (after USP overrides):")
        print(f"  business_name : {brief.business_name}")
        print(f"  category      : {brief.category}")
        print(f"  country       : {brief.country}")
        print(f"  tone          : {brief.tone}")
        print(f"  colors        : {brief.colors}")
        print(f"  keywords      : {brief.keywords}")
        print(f"  website_url   : {brief.website_url}")

    print(f"\nGallery: {len(ctx.gallery)} images")
    for img in ctx.gallery:
        print(f"  {img.name} | {img.role} | used_previously={img.used_previously}")

    print(f"\nChannels: {len(ctx.available_channels)}")
    for ch in ctx.available_channels:
        print(f"  {ch.channel}: {ch.url_or_handle or ch.label_hint}")

    if ctx.user_insights:
        print(f"\nUserInsights fed to LLM: {len(ctx.user_insights)}")
        for ins in ctx.user_insights:
            print(
                f"  [{ins.get('confidence')}] {ins.get('key')}: {str(ins.get('insight', ''))[:80]}"
            )
    else:
        print("\nUserInsights: (none)")

    print(f"\nWarnings: {[w.code for w in warnings]}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
