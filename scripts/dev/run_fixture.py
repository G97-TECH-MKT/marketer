#!/usr/bin/env python3
"""POST a fixture envelope to a local MARKETER instance and print the response.

Usage:
  python scripts/dev/run_fixture.py tests/fixtures/envelopes/casa_maruja_post.json
  python scripts/run_fixture.py casa_maruja_post.json
  $env:MARKETER_URL = "http://127.0.0.1:8080"; python scripts/run_fixture.py minimal_post.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = os.environ.get("MARKETER_URL", "http://127.0.0.1:8000")


def _resolve_fixture_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_file():
        return p.resolve()
    candidate = ROOT / "tests" / "fixtures" / "envelopes" / raw
    if candidate.is_file():
        return candidate.resolve()
    raise SystemExit(f"Fixture not found: {raw}")


def main() -> None:
    parser = argparse.ArgumentParser(description="POST fixture JSON to POST /tasks")
    parser.add_argument("fixture", help="Path to JSON or filename under tests/fixtures/envelopes/")
    parser.add_argument(
        "--url",
        default=DEFAULT_BASE,
        help=f"MARKETER base URL (default: {DEFAULT_BASE} or env MARKETER_URL)",
    )
    args = parser.parse_args()
    path = _resolve_fixture_path(args.fixture)
    body = json.loads(path.read_text(encoding="utf-8"))
    url = args.url.rstrip("/") + "/tasks"
    try:
        r = httpx.post(url, json=body, timeout=120.0)
    except httpx.ConnectError as exc:
        raise SystemExit(
            f"Could not connect to {url}. Start the API (uvicorn) or set MARKETER_URL.\n{exc}"
        ) from exc
    print(f"HTTP {r.status_code}  {url}\n")
    try:
        data = r.json()
    except json.JSONDecodeError:
        print(r.text)
        sys.exit(1)
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
