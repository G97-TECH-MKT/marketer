#!/usr/bin/env python3
"""Multi-surface quality test — 4 verticals × 4 surfaces (post / story / reel / carousel).

Forces one surface format per run using keyword injection in client_request.description.
Report focuses on the new CF output fields: brand_dna (design-system format),
cf_post_brief (assembled post instruction), and hashtag_strategy.tags.

Usage:
    MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/dev/multi_surface_test.py
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from marketer.config import load_settings  # noqa: E402
from marketer.llm.gemini import GeminiClient  # noqa: E402
from marketer.reasoner import reason  # noqa: E402

REPORT_DIR = ROOT / "reports"
REPORT_PATH = REPORT_DIR / "multi_surface_test_2026-04-21.md"

RED_FLAG_CODES = {
    "cta_caption_channel_mismatch",
    "palette_mismatch",
    "claim_not_in_brief",
}

# (label, fixture_path, description_override)
# The description must contain the surface keyword that normalizer detects.
RUNS: list[tuple[str, Path, str]] = [
    (
        "post | Verdea Studio (moda sostenible)",
        ROOT / "tests" / "fixtures" / "envelopes" / "retail_ecom_post.json",
        (
            "Crea un post simple para el lanzamiento de la nueva colección cápsula de primavera, "
            "hecha con algodón reciclado. Transmite el valor sostenible y dirige tráfico "
            "a la tienda online. Tono estético, honesto y consciente."
        ),
    ),
    (
        "story | Casa Maruja (restaurante)",
        ROOT / "tests" / "fixtures" / "envelopes" / "casa_maruja_post.json",
        (
            "Crea una story de Instagram para anunciar el plato del día de hoy: arròs al forn. "
            "Corta, directa, con ganas de que la gente venga a comer. Tono cercano."
        ),
    ),
    (
        "reel | Clínica Dental Eixample (salud)",
        ROOT / "tests" / "fixtures" / "envelopes" / "dentist_post.json",
        (
            "Crea un reel corto mostrando cómo es una revisión dental en nuestra clínica: "
            "tranquila, sin miedo y con trato cercano. Que la gente pierda el miedo al dentista."
        ),
    ),
    (
        "carousel | Pulsemetrics (SaaS B2B)",
        ROOT / "tests" / "fixtures" / "envelopes" / "saas_b2b_post.json",
        (
            "Crea un carrusel con los 3 beneficios principales de las alertas predictivas "
            "de Pulsemetrics: menos ruido, más señal, y menos tiempo resolviendo incidentes. "
            "Claro, orientado a resultados, dirigido a CTOs y SREs."
        ),
    ),
]


def _patch_envelope(base: dict[str, Any], description: str, label: str) -> dict[str, Any]:
    """Deep-copy the base envelope and inject a new description + fresh task_id."""
    env = copy.deepcopy(base)
    env["task_id"] = str(uuid.uuid4())
    env["correlation_id"] = f"multi-surface-{label[:12].replace(' ', '-').replace('|', '').strip()}"
    payload = env.setdefault("payload", {})
    client_request = payload.setdefault("client_request", {})
    client_request["description"] = description
    return env


def _extract_metrics(callback_dump: dict[str, Any]) -> dict[str, Any]:
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
    hashtag = enrichment.get("hashtag_strategy") or {}
    sd = enrichment.get("strategic_decisions") or {}
    conf = enrichment.get("confidence") or {}
    bi = enrichment.get("brand_intelligence") or {}

    out.update(
        {
            "surface_format": enrichment.get("surface_format"),
            "content_pillar": enrichment.get("content_pillar"),
            "angle_chosen": (sd.get("angle") or {}).get("chosen"),
            "voice_chosen": (sd.get("voice") or {}).get("chosen"),
            "cta_channel": cta.get("channel"),
            "cta_label": cta.get("label"),
            "emotional_beat": bi.get("emotional_beat"),
            "rhetorical_device": bi.get("rhetorical_device"),
            # New CF fields
            "brand_dna": enrichment.get("brand_dna", ""),
            "cf_post_brief": enrichment.get("cf_post_brief", ""),
            "hashtag_tags": hashtag.get("tags") or [],
            # Quality signals
            "caption_hook": caption.get("hook", ""),
            "caption_body": caption.get("body", ""),
            "caption_cta_line": caption.get("cta_line", ""),
            "image_concept": image.get("concept", ""),
            "warning_codes": warning_codes,
            "red_flag": any(c in RED_FLAG_CODES for c in warning_codes),
            "latency_ms": trace.get("latency_ms", 0),
            "degraded": trace.get("degraded", False),
            "repair_attempted": trace.get("repair_attempted", False),
            "confidence": {
                "surface_format": conf.get("surface_format"),
                "angle": conf.get("angle"),
                "palette_match": conf.get("palette_match"),
                "cta_channel": conf.get("cta_channel"),
            },
        }
    )
    return out


def _run(
    label: str,
    envelope: dict[str, Any],
    client: GeminiClient,
    extras_truncation: int,
) -> dict[str, Any]:
    try:
        callback = reason(envelope, gemini=client, extras_truncation=extras_truncation)
        dump = callback.model_dump(mode="json")
        metrics = _extract_metrics(dump)
        metrics["exception"] = None
    except Exception as exc:  # noqa: BLE001
        metrics = {
            "status": "FAILED",
            "exception": f"{type(exc).__name__}: {exc}",
            "error_message": str(exc),
            "red_flag": False,
            "warning_codes": [],
            "latency_ms": 0,
            "brand_dna": "",
            "cf_post_brief": "",
            "hashtag_tags": [],
            "caption_hook": "",
            "caption_body": "",
            "caption_cta_line": "",
            "image_concept": "",
            "surface_format": "—",
            "content_pillar": "—",
            "angle_chosen": "—",
            "voice_chosen": "—",
            "cta_channel": "—",
            "cta_label": "—",
            "emotional_beat": "—",
            "rhetorical_device": "—",
            "degraded": False,
            "repair_attempted": False,
            "confidence": {},
        }
    return metrics


def _fmt_warns(codes: list[str]) -> str:
    if not codes:
        return "—"
    return ", ".join(f"`{c}`" for c in codes)


def _render_report(
    results: list[tuple[str, dict[str, Any]]],
    model_name: str,
    total_seconds: float,
) -> str:
    lines: list[str] = []
    lines.append("# Multi-surface test — 2026-04-21")
    lines.append("")
    lines.append(f"- Model: `{model_name}`")
    lines.append(f"- Runs: {len(results)} (post / story / reel / carousel × 1 each)")
    lines.append(f"- Total wall time: {total_seconds:.1f}s")
    lines.append("")

    # ── Summary table ──────────────────────────────────────────────────────────
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| Label | surface | pillar | cta | beat | rhetorical | hashtags | "
        "latency | warns | red_flag |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for label, r in results:
        tags_n = len(r.get("hashtag_tags") or [])
        warn_codes = r.get("warning_codes") or []
        lines.append(
            "| {lbl} | {sf} | {pillar} | {ch} | {beat} | {rhet} | {tags}× | "
            "{lat}ms | {warns} | {rf} |".format(
                lbl=label,
                sf=r.get("surface_format") or "—",
                pillar=r.get("content_pillar") or "—",
                ch=r.get("cta_channel") or "—",
                beat=r.get("emotional_beat") or "—",
                rhet=r.get("rhetorical_device") or "—",
                tags=tags_n,
                lat=r.get("latency_ms", 0),
                warns=_fmt_warns(warn_codes),
                rf="RED" if r.get("red_flag") else "ok",
            )
        )
    lines.append("")

    # ── Per-run detail ─────────────────────────────────────────────────────────
    for label, r in results:
        lines.append("---")
        lines.append(f"## {label}")
        lines.append("")

        if r.get("exception"):
            lines.append(f"**FAILED**: `{r['exception']}`")
            lines.append("")
            continue

        conf = r.get("confidence") or {}
        lines.append(
            f"**surface**: `{r.get('surface_format')}` · "
            f"**pillar**: `{r.get('content_pillar')}` · "
            f"**cta**: `{r.get('cta_channel')}` ({r.get('cta_label')}) · "
            f"**latency**: {r.get('latency_ms')}ms"
        )
        lines.append(
            f"**angle**: {r.get('angle_chosen')} · "
            f"**voice**: {r.get('voice_chosen')}"
        )
        lines.append(
            f"**confidence**: surface={conf.get('surface_format')} / "
            f"angle={conf.get('angle')} / palette={conf.get('palette_match')} / "
            f"cta={conf.get('cta_channel')}"
        )
        warns = r.get("warning_codes") or []
        if warns:
            lines.append(f"**warnings**: {_fmt_warns(warns)}")
        lines.append("")

        # brand_dna
        lines.append("### brand_dna (→ client_dna en CF)")
        lines.append("")
        lines.append("```")
        lines.append(r.get("brand_dna") or "(vacío)")
        lines.append("```")
        lines.append("")

        # cf_post_brief
        lines.append("### cf_post_brief (→ client_request_posts en CF)")
        lines.append("")
        lines.append("```")
        lines.append(r.get("cf_post_brief") or "(vacío)")
        lines.append("```")
        lines.append("")

        # Hashtags
        tags = r.get("hashtag_tags") or []
        lines.append(f"### hashtag_strategy.tags ({len(tags)} tags)")
        lines.append("")
        if tags:
            lines.append(" ".join(tags))
        else:
            lines.append("_(ninguno)_")
        lines.append("")

        # Caption (for cross-check)
        lines.append("### caption (reference — debe coincidir con cf_post_brief)")
        lines.append("")
        lines.append(f"**hook**: {r.get('caption_hook')}")
        lines.append("")
        body = (r.get("caption_body") or "").replace("\n", "  \n")
        lines.append(f"**body**: {body}")
        lines.append("")
        lines.append(f"**cta_line**: {r.get('caption_cta_line')}")
        lines.append("")

        # image concept
        lines.append(f"**image.concept**: {r.get('image_concept')}")
        lines.append("")

    # ── Aggregate ──────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("## Aggregate")
    lines.append("")
    all_results = [r for _, r in results]
    completed = sum(1 for r in all_results if r.get("status") == "COMPLETED")
    failed = len(all_results) - completed
    red_flags = sum(1 for r in all_results if r.get("red_flag"))
    degraded = sum(1 for r in all_results if r.get("degraded"))
    repaired = sum(1 for r in all_results if r.get("repair_attempted"))
    cf_briefs_filled = sum(1 for r in all_results if r.get("cf_post_brief"))
    brand_dna_filled = sum(1 for r in all_results if r.get("brand_dna"))
    tags_counts = [len(r.get("hashtag_tags") or []) for r in all_results if r.get("status") == "COMPLETED"]

    latencies = [r.get("latency_ms", 0) for r in all_results if r.get("latency_ms")]
    lat_str = f"min={min(latencies)}ms / max={max(latencies)}ms / avg={int(sum(latencies)/len(latencies))}ms" if latencies else "n/a"

    lines.append(f"- Runs completed: {completed}/{len(all_results)} (failed={failed})")
    lines.append(f"- Red flags (palette_mismatch / claim_not_in_brief / cta_caption_channel_mismatch): {red_flags}")
    lines.append(f"- Degraded: {degraded} / Repair attempted: {repaired}")
    lines.append(f"- brand_dna filled: {brand_dna_filled}/{len(all_results)}")
    lines.append(f"- cf_post_brief filled: {cf_briefs_filled}/{len(all_results)}")
    if tags_counts:
        lines.append(f"- hashtag_strategy.tags: avg {sum(tags_counts)/len(tags_counts):.1f} tags/run (min={min(tags_counts)}, max={max(tags_counts)})")
    lines.append(f"- Latency: {lat_str}")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    if not os.environ.get("MARKETER_RUN_LIVE"):
        print(
            "Set MARKETER_RUN_LIVE=1 to run live LLM calls.\n"
            "  MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/dev/multi_surface_test.py",
            file=sys.stderr,
        )
        sys.exit(2)

    settings = load_settings()
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set.", file=sys.stderr)
        sys.exit(2)

    client = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, dict[str, Any]]] = []
    overall_start = time.time()

    for label, fixture_path, description in RUNS:
        base = json.loads(fixture_path.read_text(encoding="utf-8"))
        envelope = _patch_envelope(base, description, label)

        print(f"\n[{label}] running...", flush=True)
        t0 = time.time()
        metrics = _run(label, envelope, client, settings.extras_list_truncation)
        elapsed = time.time() - t0

        sf = metrics.get("surface_format") or "?"
        status = metrics.get("status") or "?"
        tags_n = len(metrics.get("hashtag_tags") or [])
        has_brief = bool(metrics.get("cf_post_brief"))
        warns_n = len(metrics.get("warning_codes") or [])
        rf = "RED" if metrics.get("red_flag") else "ok"

        print(
            f"  >> {status} | surface={sf} | tags={tags_n} | cf_brief={'yes' if has_brief else 'NO'} "
            f"| warns={warns_n} | {rf} | wall={elapsed:.1f}s"
        )

        # Retry once on hard failure
        if metrics.get("exception"):
            print(f"  !! exception: {metrics['exception']} -- retrying once...")
            metrics = _run(label, envelope, client, settings.extras_list_truncation)

        results.append((label, metrics))

    total = time.time() - overall_start
    report = _render_report(results, model_name=client.model_name, total_seconds=total)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\nReport: {REPORT_PATH}")


if __name__ == "__main__":
    main()
