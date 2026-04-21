#!/usr/bin/env python3
"""Batch stress-test the MARKETER prompt across 3 verticals × 3 runs.

Reads 3 fixtures from fixtures/envelopes/, runs reason() three times each,
collects qualitative signals, and writes a markdown report under reports/.

Usage (live):
    MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/batch_test.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from marketer.config import load_settings  # noqa: E402
from marketer.llm.gemini import GeminiClient  # noqa: E402
from marketer.reasoner import reason  # noqa: E402

FIXTURES = [
    ("saas_b2b", ROOT / "fixtures" / "envelopes" / "saas_b2b_post.json"),
    ("retail_ecom", ROOT / "fixtures" / "envelopes" / "retail_ecom_post.json"),
    ("dentist", ROOT / "fixtures" / "envelopes" / "dentist_post.json"),
]

RED_FLAG_CODES = {
    "cta_caption_channel_mismatch",
    "palette_mismatch",
    "claim_not_in_brief",
}

RUNS_PER_FIXTURE = 3

REPORT_DIR = ROOT / "reports"
# Use the run date matching the task context; fall back to today if unavailable.
REPORT_PATH = REPORT_DIR / "batch_test_2026-04-21.md"


def _extract_run_metrics(callback_dump: dict[str, Any]) -> dict[str, Any]:
    """Flatten a CallbackBody dump into the qualitative signals we care about."""
    out: dict[str, Any] = {
        "status": callback_dump.get("status"),
        "error_message": callback_dump.get("error_message"),
    }
    data = callback_dump.get("output_data") or {}
    enrichment = data.get("enrichment") or {}
    trace = data.get("trace") or {}
    warnings = data.get("warnings") or []
    warning_codes = [w.get("code") for w in warnings]

    cta = enrichment.get("cta") or {}
    caption = enrichment.get("caption") or {}
    image = enrichment.get("image") or {}
    sd = enrichment.get("strategic_decisions") or {}
    conf = enrichment.get("confidence") or {}

    out.update(
        {
            "cta_channel": cta.get("channel"),
            "cta_label": cta.get("label"),
            "content_pillar": enrichment.get("content_pillar"),
            "surface_format": enrichment.get("surface_format"),
            "angle_chosen": (sd.get("angle") or {}).get("chosen"),
            "voice_chosen": (sd.get("voice") or {}).get("chosen"),
            "confidence": {
                "surface_format": conf.get("surface_format"),
                "angle": conf.get("angle"),
                "palette_match": conf.get("palette_match"),
                "cta_channel": conf.get("cta_channel"),
            },
            "warning_codes": warning_codes,
            "hook_len": len(caption.get("hook") or ""),
            "body_len": len(caption.get("body") or ""),
            "gen_prompt_len": len(image.get("generation_prompt") or ""),
            "latency_ms": trace.get("latency_ms", 0),
            "degraded": trace.get("degraded", False),
            "repair_attempted": trace.get("repair_attempted", False),
            "red_flag": any(c in RED_FLAG_CODES for c in warning_codes),
        }
    )
    return out


def _run_once(
    envelope: dict[str, Any],
    client: GeminiClient,
    extras_truncation: int,
) -> dict[str, Any]:
    started = time.time()
    try:
        callback = reason(envelope, gemini=client, extras_truncation=extras_truncation)
        dump = callback.model_dump(mode="json")
        metrics = _extract_run_metrics(dump)
        metrics["exception"] = None
        return metrics
    except Exception as exc:  # noqa: BLE001 - we want to log transient failures
        return {
            "status": "FAILED",
            "error_message": f"exception: {type(exc).__name__}: {exc}",
            "exception": f"{type(exc).__name__}: {exc}",
            "latency_ms": int((time.time() - started) * 1000),
            "red_flag": False,
            "warning_codes": [],
        }


def _fmt_list(items: list[Any]) -> str:
    if not items:
        return "—"
    return ", ".join(str(x) for x in items)


def _agreement(values: list[Any]) -> str:
    """Return 'all agree' if all equal, else show distribution."""
    filtered = [v for v in values if v is not None]
    if not filtered:
        return "n/a"
    uniq = sorted(set(filtered), key=str)
    if len(uniq) == 1:
        return f"all agree ({uniq[0]})"
    return " / ".join(str(v) for v in values)


def _voice_channel_alignment(voice: str | None, channel: str | None) -> str:
    if not voice or not channel:
        return "?"
    v = (voice or "").lower()
    friendly_like = any(kw in v for kw in ("friendly", "cerc", "cálid", "calid", "warm", "casual", "amistos"))
    professional_like = any(kw in v for kw in ("profesion", "formal", "authoritative", "expert", "clar"))
    friendly_channels = {"dm", "link_sticker"}
    professional_channels = {"website", "phone", "email"}
    if friendly_like and channel in friendly_channels:
        return "OK (friendly→dm/link)"
    if professional_like and channel in professional_channels:
        return "OK (professional→web/phone)"
    if friendly_like and channel in professional_channels:
        return "COUNTER (friendly→web/phone)"
    if professional_like and channel in friendly_channels:
        return "COUNTER (professional→dm/link)"
    return "neutral"


def _render_report(
    all_results: dict[str, list[dict[str, Any]]],
    model_name: str,
    total_seconds: float,
) -> str:
    lines: list[str] = []
    lines.append("# Batch stress-test — 2026-04-21")
    lines.append("")
    lines.append(f"- Model: `{model_name}`")
    lines.append(f"- Fixtures × runs: 3 × {RUNS_PER_FIXTURE} = {3 * RUNS_PER_FIXTURE}")
    lines.append(f"- Total wall time: {total_seconds:.1f}s")
    lines.append("")

    # -------- Per-vertical sections --------
    all_latencies: list[int] = []
    for vertical, runs in all_results.items():
        lines.append(f"## Vertical: `{vertical}`")
        lines.append("")
        lines.append(
            "| Run | status | pillar | cta.channel | cta.label | voice.chosen | angle.chosen | "
            "surface | hook/body/img | latency_ms | degraded | repair | warnings | red_flag |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
        )
        for i, r in enumerate(runs, 1):
            if r.get("latency_ms"):
                all_latencies.append(r["latency_ms"])
            lens = f"{r.get('hook_len', 0)}/{r.get('body_len', 0)}/{r.get('gen_prompt_len', 0)}"
            warns = _fmt_list(r.get("warning_codes") or [])
            row_status = r.get("status", "?")
            if r.get("exception"):
                row_status = f"ERR ({r['exception']})"
            lines.append(
                "| {run} | {status} | {pillar} | {ch} | {lbl} | {voice} | {angle} | {surface} | {lens} | "
                "{lat} | {deg} | {rep} | {warns} | {rf} |".format(
                    run=i,
                    status=row_status,
                    pillar=r.get("content_pillar") or "—",
                    ch=r.get("cta_channel") or "—",
                    lbl=(r.get("cta_label") or "—").replace("|", "/"),
                    voice=(r.get("voice_chosen") or "—").replace("|", "/"),
                    angle=(r.get("angle_chosen") or "—").replace("|", "/"),
                    surface=r.get("surface_format") or "—",
                    lens=lens,
                    lat=r.get("latency_ms", 0),
                    deg=r.get("degraded"),
                    rep=r.get("repair_attempted"),
                    warns=warns,
                    rf=r.get("red_flag"),
                )
            )
        # Consistency summary
        pillars = [r.get("content_pillar") for r in runs]
        channels = [r.get("cta_channel") for r in runs]
        voices = [r.get("voice_chosen") for r in runs]
        angles = [r.get("angle_chosen") for r in runs]
        lines.append("")
        lines.append("**Consistency across runs:**")
        lines.append(f"- content_pillar: {_agreement(pillars)}")
        lines.append(f"- cta.channel: {_agreement(channels)}")
        lines.append(f"- voice.chosen: {_agreement(voices)}")
        lines.append(f"- angle.chosen: {_agreement(angles)}")
        lines.append("")
        # Voice→channel rule
        lines.append("**Voice→channel alignment per run:**")
        for i, r in enumerate(runs, 1):
            al = _voice_channel_alignment(r.get("voice_chosen"), r.get("cta_channel"))
            lines.append(f"- Run {i}: voice=`{r.get('voice_chosen')}` → channel=`{r.get('cta_channel')}` → {al}")
        lines.append("")

    # -------- Aggregate stats --------
    lines.append("## Aggregate stats")
    lines.append("")
    all_runs = [r for runs in all_results.values() for r in runs]
    n = len(all_runs)
    completed = sum(1 for r in all_runs if r.get("status") == "COMPLETED")
    failed = n - completed
    red_flags = sum(1 for r in all_runs if r.get("red_flag"))
    degraded = sum(1 for r in all_runs if r.get("degraded"))
    repaired = sum(1 for r in all_runs if r.get("repair_attempted"))
    all_warnings: list[str] = []
    for r in all_runs:
        all_warnings.extend(r.get("warning_codes") or [])

    lines.append(f"- Runs: {n} (completed={completed}, failed={failed})")
    lines.append(f"- Red flags (palette_mismatch / claim_not_in_brief / cta_caption_channel_mismatch): {red_flags}")
    lines.append(f"- Degraded: {degraded}")
    lines.append(f"- Repair attempted: {repaired}")
    if all_latencies:
        p50 = int(statistics.median(all_latencies))
        mn = min(all_latencies)
        mx = max(all_latencies)
        avg = int(statistics.mean(all_latencies))
        lines.append(f"- Latency ms: p50={p50}, min={mn}, max={mx}, avg={avg}")
    if all_warnings:
        from collections import Counter
        counts = Counter(all_warnings).most_common()
        lines.append("- Warning code frequency: " + ", ".join(f"`{c}`×{n}" for c, n in counts))
    else:
        lines.append("- Warning code frequency: (none)")
    lines.append("")

    # -------- Raw appendix --------
    lines.append("## Raw JSON (per-run)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(all_results, ensure_ascii=False, indent=2, default=str))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    settings = load_settings()
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set (env or .env). Aborting.", file=sys.stderr)
        sys.exit(2)

    client = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    all_results: dict[str, list[dict[str, Any]]] = {}
    overall_started = time.time()

    for vertical, path in FIXTURES:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        runs: list[dict[str, Any]] = []
        for i in range(1, RUNS_PER_FIXTURE + 1):
            t0 = time.time()
            metrics = _run_once(envelope, client, settings.extras_list_truncation)
            if metrics.get("exception"):
                # retry once on transient failure
                print(
                    f"[{vertical}] run {i}/{RUNS_PER_FIXTURE} FAILED ({metrics['exception']}); retrying once..."
                )
                metrics = _run_once(envelope, client, settings.extras_list_truncation)
            elapsed = time.time() - t0
            status = metrics.get("status")
            ch = metrics.get("cta_channel") or "-"
            pillar = metrics.get("content_pillar") or "-"
            lat = metrics.get("latency_ms", 0)
            warn_n = len(metrics.get("warning_codes") or [])
            print(
                f"[{vertical}] run {i}/{RUNS_PER_FIXTURE} {status} pillar={pillar} "
                f"channel={ch} latency={lat}ms warnings={warn_n} wall={elapsed:.1f}s"
            )
            runs.append(metrics)
        all_results[vertical] = runs

    total = time.time() - overall_started
    report = _render_report(all_results, model_name=client.model_name, total_seconds=total)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\nReport written: {REPORT_PATH}")


if __name__ == "__main__":
    main()
