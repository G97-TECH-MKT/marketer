"""Simulate a ROUTER -> MARKETER -> ROUTER round trip and dump a merged JSON.

Posts the canonical Contrato B body (`tests/fixtures/envelopes/casa_maruja_post.json`)
to a running MARKETER and writes a single file with:

  - router_dispatch:  exactly what ROUTER sends MARKETER (Contrato B, §3)
  - marketer_callback:  the body MARKETER would PATCH back to ROUTER (Contrato C, §4)
  - router_record_after_step: gate_responses + sequence_responses[marketer]
                              as the orchestrator would store it
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default="casa_maruja_post.json")
    parser.add_argument("--url", default=os.environ.get("MARKETER_URL", "http://127.0.0.1:8021"))
    parser.add_argument("--out", default="tmp_merged_marketer_run.json")
    args = parser.parse_args()

    fixture = ROOT / "tests" / "fixtures" / "envelopes" / args.fixture
    dispatch = json.loads(fixture.read_text(encoding="utf-8"))

    headers = {
        "Content-Type": "application/json",
        "X-Task-Id": dispatch["task_id"],
        "X-Correlation-Id": dispatch.get("correlation_id", ""),
        "X-Callback-Url": dispatch["callback_url"],
    }
    url = args.url.rstrip("/") + "/tasks"
    r = httpx.post(url, json=dispatch, headers=headers, timeout=180.0)
    resp = r.json()
    print(f"HTTP {r.status_code}  status={resp.get('status')}")

    task_id = dispatch["task_id"]
    callback_path = f"/api/v1/tasks/{task_id}/callback"
    sequence_order = (
        dispatch.get("payload", {})
        .get("agent_sequence", {})
        .get("current", {})
        .get("step_order", 1)
    )
    latency_ms = (resp.get("output_data") or {}).get("trace", {}).get("latency_ms")

    merged = {
        "transcript": {
            "flow": "router_dispatch -> marketer -> router_callback",
            "agent": "marketer",
            "task_id": task_id,
            "job_id": dispatch.get("job_id"),
            "correlation_id": dispatch.get("correlation_id"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "router_dispatch": {
            "method": "POST",
            "path": "/tasks",
            "headers": headers,
            "body": dispatch,
        },
        "marketer_callback": {
            "method": "PATCH",
            "path": callback_path,
            "headers": {"X-API-Key": "<marketer_agent_token>"},
            "body": resp,
        },
        "router_record_after_step": {
            "job_id": dispatch.get("job_id"),
            "gate_responses": dispatch.get("payload", {}).get("action_execution_gates", {}),
            "sequence_responses": {
                "marketer": {
                    "status": resp.get("status"),
                    "sequence_order": sequence_order,
                    "output_schema": "post_enrichment.v1",
                    "output_data": resp.get("output_data"),
                    "duration_ms": latency_ms,
                }
            },
        },
    }

    out = ROOT / args.out
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Merged JSON written to: {out}")
    print(f"Bytes: {out.stat().st_size}")


if __name__ == "__main__":
    main()
