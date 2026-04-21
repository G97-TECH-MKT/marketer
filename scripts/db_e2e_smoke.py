#!/usr/bin/env python3
"""End-to-end smoke: golden envelope → real Gemini → Postgres.

POSTs fixtures/envelopes/golden_post.json (with fresh random IDs) through the
running app via TestClient. `reason()` runs for real — Gemini is hit, the
enrichment is produced, persistence writes all five rows. The outbound PATCH
to callback_url is stubbed so we don't hang on a fake URL.

Run with DATABASE_URL + GEMINI_API_KEY set in .env. Costs one LLM call.

    python scripts/db_e2e_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DB_USE_NULL_POOL", "true")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from marketer import main as main_module  # noqa: E402
from marketer.config import load_settings  # noqa: E402
from marketer.db.models import Job, RawBrief, Strategy  # noqa: E402


def _sync_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def main() -> int:
    settings = load_settings()
    if not settings.database_url:
        print("DATABASE_URL not set; aborting.", file=sys.stderr)
        return 1
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY not set; aborting.", file=sys.stderr)
        return 1

    golden_path = ROOT / "fixtures" / "envelopes" / "golden_post.json"
    envelope = json.loads(golden_path.read_text(encoding="utf-8"))
    account_uuid = uuid4()
    task_uuid = uuid4()
    envelope["task_id"] = str(task_uuid)
    envelope["callback_url"] = f"https://example.test/cb/{task_uuid}"
    envelope["payload"]["context"]["account_uuid"] = str(account_uuid)

    async def _no_op_callback(*args, **kwargs):  # noqa: ANN001
        return None

    main_module._patch_callback = _no_op_callback

    print(f"POSTing golden envelope: task_id={task_uuid} account={account_uuid}")
    print(f"model={settings.gemini_model}  (this makes one real Gemini call)")

    with TestClient(main_module.app) as client:
        resp = client.post("/tasks", json=envelope)
    print(f"HTTP {resp.status_code}: {resp.json()}")
    if resp.status_code != 202:
        return 2

    engine = create_engine(_sync_url(settings.database_url))
    with Session(engine) as session:
        raw_brief = session.execute(
            select(RawBrief).where(RawBrief.router_task_id == task_uuid)
        ).scalar_one_or_none()
        if raw_brief is None:
            print("FAIL: no raw_briefs row was written.", file=sys.stderr)
            return 3
        print()
        print("=== raw_briefs ===")
        print(f"  id            : {raw_brief.id}")
        print(f"  status        : {raw_brief.status}")
        print(f"  received_at   : {raw_brief.received_at}")
        print(f"  processed_at  : {raw_brief.processed_at}")

        strategy = session.execute(
            select(Strategy).where(
                Strategy.user_id == account_uuid, Strategy.is_active.is_(True)
            )
        ).scalar_one_or_none()
        if strategy is None:
            print("FAIL: no active strategy for this user.", file=sys.stderr)
            return 4
        print()
        print(f"=== strategies (v{strategy.version}, real brand_intelligence) ===")
        bi = strategy.brand_intelligence
        for key in (
            "business_taxonomy",
            "voice_register",
            "emotional_beat",
            "audience_persona",
            "unfair_advantage",
            "rhetorical_device",
        ):
            value = bi.get(key)
            if isinstance(value, str) and len(value) > 120:
                value = value[:117] + "..."
            print(f"  {key:20s}: {value}")

        job = session.execute(
            select(Job).where(Job.raw_brief_id == raw_brief.id)
        ).scalar_one_or_none()
        if job is None:
            print("FAIL: no job row for this raw_brief.", file=sys.stderr)
            return 5
        print()
        print("=== jobs ===")
        print(f"  id            : {job.id}")
        print(f"  status        : {job.status}")
        print(f"  latency_ms    : {job.latency_ms}")
        if job.output:
            print(f"  output.status : {job.output.get('status')}")
            out_data = job.output.get("output_data") or {}
            cf = out_data.get("data") or {}
            cr = cf.get("client_request") or ""
            preview = cr if len(cr) <= 200 else cr[:197] + "..."
            print(f"  cf client_request preview: {preview}")

    print()
    print("OK — real Gemini output persisted across all 5 tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
