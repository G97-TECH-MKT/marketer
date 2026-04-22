#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick Gallery dry run -- no DB, no Gemini.

Fetches the gallery pool for an account, shows raw items, eligibility results,
Stage 1 scores, and the final vision shortlist.

Usage:
    python scripts/dev/gallery_dry_run.py
    python scripts/dev/gallery_dry_run.py --account-uuid <real-uuid>
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
from marketer.gallery import fetch_gallery_pool, is_eligible, score_image  # noqa: E402
from marketer.main import _build_gallery_task_context  # noqa: E402

from datetime import datetime, timezone  # noqa: E402


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

    task_context = _build_gallery_task_context(envelope)

    print(f"\naccount_uuid     : {account_uuid}")
    print(f"Gallery base URL : {settings.gallery_api_url or '(not configured)'}")
    print(f"Gallery key set  : {'yes' if settings.gallery_api_key else 'no'}")
    print(f"vision_candidates: {settings.gallery_vision_candidates}")
    print(f"page_size        : {settings.gallery_page_size}")

    print(f"\ntask_context for Stage 1 scoring:")
    print(f"  user_request  : {task_context['user_request'][:80]}")
    print(f"  keywords      : {task_context['brief_keywords']}")
    print(f"  tone          : {task_context['brief_tone']}")

    gallery_configured = bool(settings.gallery_api_url and settings.gallery_api_key)
    if not gallery_configured:
        print(
            "\nGallery not configured — add to .env:\n"
            "  GALLERY_API_URL=https://api-dev.orbidi.com/prod-line/space-management\n"
            "  GALLERY_API_KEY=<your-key>"
        )
        return

    # ── Raw fetch (bypass the pool builder to inspect raw items first) ────────
    import httpx

    raw_url = f"{settings.gallery_api_url}/accounts/{account_uuid}/gallery"
    print(f"\nFetching {raw_url} …")

    try:
        async with httpx.AsyncClient(timeout=settings.gallery_timeout_seconds) as client:
            resp = await client.get(
                raw_url,
                params={"page": 1, "size": settings.gallery_page_size},
                headers={"X-API-KEY": settings.gallery_api_key, "Accept": "application/json"},
            )
    except Exception as exc:
        print(f"\nHTTP error: {exc}")
        return

    print(f"HTTP {resp.status_code}")

    if resp.status_code == 404:
        print("→ Account not found in Gallery API (expected for synthetic UUID).")
        print("  Pass a real --account-uuid to get gallery data.")
        return

    if resp.status_code >= 400:
        print(f"→ Error: {resp.text[:300]}")
        return

    body = resp.json()
    print(f"Raw response type : {type(body).__name__}")
    if isinstance(body, dict):
        print(f"Raw response keys : {list(body.keys())}")
        # Pretty print first 500 chars to see structure
        print(f"Raw response peek :\n{json.dumps(body, indent=2, ensure_ascii=False)[:800]}")
    elif isinstance(body, list):
        print(f"Raw response      : list of {len(body)} items")
        if body:
            print(f"First item keys   : {list(body[0].keys()) if isinstance(body[0], dict) else body[0]}")

    # Resolve to list regardless of shape
    if isinstance(body, list):
        raw_items: list[dict] = body
    elif isinstance(body, dict):
        # Common pagination wrappers
        for key in ("data", "items", "results", "content", "gallery"):
            if isinstance(body.get(key), list):
                raw_items = body[key]
                print(f"→ Extracted items from body['{key}']")
                break
        else:
            raw_items = []
            print("→ Could not extract item list from response dict")
    else:
        raw_items = []
    print(f"→ Received {len(raw_items)} raw items")

    # ── Raw items overview ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("RAW ITEMS")
    print("=" * 60)
    for i, item in enumerate(raw_items):
        uuid = item.get("uuid", "?")[:8]
        type_ = item.get("type", "?")
        category = item.get("category", "?")
        used_at = item.get("used_at")
        locked = item.get("locked_until")
        tags = (item.get("metadata") or {}).get("tags") or []
        print(
            f"  [{i+1:2d}] {uuid}… | type={type_:<5} | cat={category[:20]:<20} "
            f"| used={'yes' if used_at else 'no '} | locked={'yes' if locked else 'no '} "
            f"| tags={tags[:3]}"
        )

    # ── Eligibility breakdown ─────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    eligible = [item for item in raw_items if is_eligible(item, now)]
    ineligible = len(raw_items) - len(eligible)

    print(f"\n{'='*60}")
    print("ELIGIBILITY FILTER")
    print("=" * 60)
    print(f"  Total fetched : {len(raw_items)}")
    print(f"  Eligible      : {len(eligible)}")
    print(f"  Rejected      : {ineligible}")

    # ── Stage 1 scoring ───────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("STAGE 1 SCORES (eligible items, sorted)")
    print("=" * 60)
    if not eligible:
        print("  (no eligible items)")
    else:
        scored = sorted(
            [(score_image(item, task_context), item) for item in eligible],
            key=lambda x: x[0],
            reverse=True,
        )
        for rank, (sc, item) in enumerate(scored, 1):
            uuid = item.get("uuid", "?")[:8]
            category = (item.get("category") or "?")[:20]
            tags = (item.get("metadata") or {}).get("tags") or []
            desc = (item.get("description") or "")[:50]
            print(f"  #{rank:2d} score={sc:5.1f} | {uuid}… | {category:<20} | tags={tags[:3]}")
            if desc:
                print(f"           desc: {desc}")

    # ── Vision shortlist ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"VISION SHORTLIST (top {settings.gallery_vision_candidates})")
    print("=" * 60)

    gallery_pool, warning_code = await fetch_gallery_pool(
        account_uuid=account_uuid,
        base_url=settings.gallery_api_url,
        api_key=settings.gallery_api_key,
        task_context=task_context,
        vision_candidates=settings.gallery_vision_candidates,
        page_size=settings.gallery_page_size,
        timeout=settings.gallery_timeout_seconds,
    )

    print(f"warning_code : {warning_code or '(none — success)'}")

    if gallery_pool is None:
        print("gallery_pool : None (fetch failed)")
        return

    print(f"total_fetched  : {gallery_pool.total_fetched}")
    print(f"total_eligible : {gallery_pool.total_eligible}")
    print(f"truncated      : {gallery_pool.truncated}")
    print(f"shortlist size : {len(gallery_pool.shortlist)}")

    if gallery_pool.shortlist:
        print("\nShortlisted images (will be sent to LLM vision):")
        for rank, item in enumerate(gallery_pool.shortlist, 1):
            tags = item.metadata.get("tags") or []
            mood = item.metadata.get("mood") or ""
            subject = (item.metadata.get("subject") or "")[:60]
            print(f"\n  #{rank} uuid={item.uuid[:8]}…  score={item.score:.1f}")
            print(f"     category : {item.category}")
            print(f"     url      : {item.content_url[:70]}…")
            if item.description:
                print(f"     desc     : {item.description[:70]}")
            if tags:
                print(f"     tags     : {tags}")
            if mood:
                print(f"     mood     : {mood[:60]}")
            if subject:
                print(f"     subject  : {subject}")
    else:
        print("\n  (empty shortlist — no eligible images in this account)")

    print()


if __name__ == "__main__":
    asyncio.run(main())
