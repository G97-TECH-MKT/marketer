#!/usr/bin/env python3
"""Real end-to-end smoke test for the async dispatch refactor.

Flow:
  1. Spin up a mock callback server on 127.0.0.1:9000 (stdlib http.server).
  2. Spin up uvicorn serving marketer.main:app on 127.0.0.1:8000.
  3. POST casa_maruja_post.json to /tasks with callback_url pointing to the mock.
  4. Assert HTTP 202 and body {"status":"ACCEPTED",...} within 1s.
  5. Wait up to 60s for the mock to receive a PATCH.
  6. Validate the PATCH body is a well-formed CallbackBody with status=COMPLETED.

Usage:
  PYTHONPATH=src python scripts/ops/smoke_async_roundtrip.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "envelopes" / "casa_maruja_post.json"

MOCK_PORT = 9000
MARKETER_PORT = 8000

_received: dict = {}


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_PATCH(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        _received["method"] = "PATCH"
        _received["path"] = self.path
        _received["headers"] = dict(self.headers)
        try:
            _received["body"] = json.loads(raw.decode("utf-8"))
        except Exception:
            _received["body"] = raw.decode("utf-8", errors="replace")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ack":true}')

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Silence default access logs
        pass


def _start_mock_server() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", MOCK_PORT), _CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _start_marketer() -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "marketer.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(MARKETER_PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_ready(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready within {timeout}s")


def main() -> int:
    envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))
    envelope["callback_url"] = (
        f"http://127.0.0.1:{MOCK_PORT}/api/v1/tasks/{envelope['task_id']}/callback"
    )

    mock = _start_mock_server()
    print(f"[mock] callback receiver listening on :{MOCK_PORT}")

    marketer = _start_marketer()
    try:
        _wait_for_ready(f"http://127.0.0.1:{MARKETER_PORT}/ready")
        print(f"[marketer] ready on :{MARKETER_PORT}")

        t_post = time.time()
        resp = httpx.post(
            f"http://127.0.0.1:{MARKETER_PORT}/tasks",
            json=envelope,
            timeout=15.0,
        )
        ack_latency = time.time() - t_post

        if resp.status_code != 202:
            print(f"FAIL: expected 202, got {resp.status_code}: {resp.text}")
            return 1
        ack_body = resp.json()
        if (
            ack_body.get("status") != "ACCEPTED"
            or ack_body.get("task_id") != envelope["task_id"]
        ):
            print(f"FAIL: bad ACK body: {ack_body}")
            return 1
        print(f"[step 1 OK] 202 ACK in {ack_latency * 1000:.0f}ms: {ack_body}")

        if ack_latency > 2.0:
            print(f"WARN: ACK took {ack_latency:.1f}s, router timeout is 10s. Close.")

        print("[step 2] waiting for callback PATCH...")
        deadline = time.time() + 60.0
        while time.time() < deadline and "body" not in _received:
            time.sleep(0.2)

        if "body" not in _received:
            print("FAIL: callback was never received within 60s")
            return 1

        total = time.time() - t_post
        print(f"[step 2 OK] callback received after {total:.1f}s")
        print(f"[callback] method={_received['method']} path={_received['path']}")
        print(f"[callback] Content-Type={_received['headers'].get('Content-Type')}")
        print(
            f"[callback] X-Correlation-Id={_received['headers'].get('X-Correlation-Id')}"
        )

        body = _received["body"]
        if not isinstance(body, dict):
            print(f"FAIL: callback body is not an object: {body!r}")
            return 1
        if body.get("status") != "COMPLETED":
            print(
                f"FAIL: status={body.get('status')}, error_message={body.get('error_message')}"
            )
            return 1
        enrichment = (body.get("output_data") or {}).get("enrichment") or {}
        if enrichment.get("schema_version") != "2.0":
            print(f"FAIL: schema_version={enrichment.get('schema_version')}")
            return 1
        if not enrichment.get("caption", {}).get("hook"):
            print("FAIL: enrichment.caption.hook is empty")
            return 1

        print(
            f"[step 3 OK] CallbackBody valid — status=COMPLETED, schema 2.0, "
            f"cta.channel={enrichment['cta']['channel']}, "
            f"warnings={[w['code'] for w in body['output_data']['warnings']]}"
        )
        print("\nSMOKE TEST PASSED")
        return 0
    finally:
        marketer.terminate()
        try:
            marketer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            marketer.kill()
        mock.shutdown()


if __name__ == "__main__":
    sys.exit(main())
