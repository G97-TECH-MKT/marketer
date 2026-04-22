#!/usr/bin/env python3
"""10-scenario power test for Nubiex Men's Massage by Bruno.

Covers: post × 5, story × 2, reel × 2, carousel × 1.
Checks: brand_dna quality, CONCEPT block, image selection, surface format accuracy.

Usage (live):
    MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/demo/nubiex_power_test.py
"""

from __future__ import annotations

import copy
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from marketer.config import load_settings  # noqa: E402
from marketer.llm.gemini import GeminiClient  # noqa: E402
from marketer.reasoner import reason  # noqa: E402

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "envelopes" / "nubiex_post.json"
REPORT_DIR = ROOT / "reports"
REPORT_DATE = "2026-04-21"

RED_FLAG_CODES = {
    "cta_caption_channel_mismatch",
    "palette_mismatch",
    "claim_not_in_brief",
}

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": 1,
        "label": "post_producto_masaje_holístico",
        "description": (
            "Crea un post para Instagram presentando el servicio de masaje holístico "
            "y tántrico de Nubiex, destacando el espacio exclusivo, seguro y discreto "
            "para hombres que buscan reconectar con su cuerpo y bienestar emocional en Barcelona."
        ),
        "expected_surface": "post",
    },
    {
        "id": 2,
        "label": "post_educación_tipos_masaje",
        "description": (
            "Crea un post educativo explicando la diferencia entre masaje holístico, "
            "tántrico, Lomi Lomi hawaiano y quiromasaje, para que los clientes entiendan "
            "el enfoque único de Nubiex Men's Massage by Bruno en Barcelona."
        ),
        "expected_surface": "post",
    },
    {
        "id": 3,
        "label": "story_awareness_bienestar_masculino",
        "description": (
            "Crea una story de Instagram para generar conciencia sobre la propuesta "
            "de bienestar masculino integral de Nubiex, destacando la importancia de "
            "reconectar con el cuerpo y el equilibrio emocional para hombres."
        ),
        "expected_surface": "story",
    },
    {
        "id": 4,
        "label": "reel_ambiente_ritual_espacio",
        "description": (
            "Crea el brief para un reel de Instagram mostrando el ambiente y la "
            "preparación ritual del espacio de masaje de Nubiex, capturando la esencia "
            "del toque consciente y la energía transformadora del lugar."
        ),
        "expected_surface": "reel",
    },
    {
        "id": 5,
        "label": "carrusel_beneficios_4_pilares",
        "description": (
            "Crea un carrusel de Instagram educativo explicando los cuatro pilares "
            "del bienestar de Nubiex: cuerpo, mente, energía y bienestar emocional, "
            "mostrando cómo cada sesión trabaja estos aspectos de forma integral."
        ),
        "expected_surface": "carousel",
    },
    {
        "id": 6,
        "label": "post_comunidad_espacio_seguro",
        "description": (
            "Crea un post de comunidad para Instagram destacando el valor de un espacio "
            "seguro, discreto y respetuoso para el bienestar masculino en Barcelona. "
            "Conectar con hombres que buscan algo más allá de la relajación física."
        ),
        "expected_surface": "post",
    },
    {
        "id": 7,
        "label": "story_promoción_primera_sesión",
        "description": (
            "Crea una story promocional para Instagram orientada a nuevos clientes en "
            "Barcelona, destacando la primera sesión con Bruno como una experiencia "
            "transformadora única de bienestar exclusivo para hombres."
        ),
        "expected_surface": "story",
    },
    {
        "id": 8,
        "label": "reel_transformación_energética",
        "description": (
            "Crea el brief para un reel corto de Instagram mostrando la transformación "
            "emocional y energética que viven los clientes durante una sesión de masaje "
            "consciente de Nubiex, transmitiendo calma, vitalidad y reconexión."
        ),
        "expected_surface": "reel",
    },
    {
        "id": 9,
        "label": "post_behind_the_scenes_bruno",
        "description": (
            "Crea un post de Instagram tipo behind the scenes mostrando la filosofía "
            "y preparación de Bruno para cada sesión de Nubiex, humanizando la marca "
            "y mostrando la dedicación y cuidado personalizado que hay detrás de cada masaje."
        ),
        "expected_surface": "post",
    },
    {
        "id": 10,
        "label": "post_conversión_reserva_sesión",
        "description": (
            "Crea un post de Instagram con llamada a la acción directa para reservar "
            "una sesión con Bruno en Nubiex Men's Massage Barcelona, enfatizando la "
            "exclusividad, discreción y el cuidado personalizado de la experiencia."
        ),
        "expected_surface": "post",
    },
]


def _extract_concept_metrics(cf_post_brief: str) -> dict[str, Any]:
    """Parse the CONCEPT block quality from cf_post_brief."""
    concept_prefix_ok = cf_post_brief.strip().startswith("CONCEPT —")
    imagen_match = re.search(r"Imagen:\s*(.+)", cf_post_brief)
    tipo_match = re.search(r"Tipo:\s*(.+)", cf_post_brief)
    return {
        "concept_prefix_ok": concept_prefix_ok,
        "imagen_line": imagen_match.group(1).strip() if imagen_match else None,
        "tipo_line": tipo_match.group(1).strip() if tipo_match else None,
        "imagen_present": imagen_match is not None,
        "tipo_present": tipo_match is not None,
        "concept_section_len": len(cf_post_brief.split("Caption:")[0].strip()) if "Caption:" in cf_post_brief else 0,
    }


def _extract_run_metrics(
    callback_dump: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "scenario_id": scenario["id"],
        "scenario_label": scenario["label"],
        "expected_surface": scenario["expected_surface"],
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
    vs = enrichment.get("visual_selection") or {}
    bi = enrichment.get("brand_intelligence") or {}

    surface = enrichment.get("surface_format")
    recommended_assets = vs.get("recommended_asset_urls") or []
    cf_brief = enrichment.get("cf_post_brief") or ""
    brand_dna = enrichment.get("brand_dna") or ""

    concept_metrics = _extract_concept_metrics(cf_brief)

    out.update(
        {
            "surface_format": surface,
            "surface_correct": surface == scenario["expected_surface"],
            "content_pillar": enrichment.get("content_pillar"),
            "cta_channel": cta.get("channel"),
            "cta_label": cta.get("label"),
            "angle_chosen": (sd.get("angle") or {}).get("chosen"),
            "voice_chosen": (sd.get("voice") or {}).get("chosen"),
            "emotional_beat": bi.get("emotional_beat"),
            "funnel_stage": bi.get("funnel_stage_target"),
            "confidence": {
                "surface_format": conf.get("surface_format"),
                "angle": conf.get("angle"),
                "palette_match": conf.get("palette_match"),
                "cta_channel": conf.get("cta_channel"),
            },
            # Image selection
            "gallery_assets_selected": len(recommended_assets),
            "selected_asset_urls": recommended_assets,
            # CONCEPT quality
            "concept_prefix_ok": concept_metrics["concept_prefix_ok"],
            "concept_imagen_present": concept_metrics["imagen_present"],
            "concept_imagen_line": concept_metrics["imagen_line"],
            "concept_tipo_present": concept_metrics["tipo_present"],
            "concept_tipo_line": concept_metrics["tipo_line"],
            "concept_section_len": concept_metrics["concept_section_len"],
            # Caption lengths
            "hook_len": len(caption.get("hook") or ""),
            "body_len": len(caption.get("body") or ""),
            "cta_line_len": len(caption.get("cta_line") or ""),
            "gen_prompt_len": len(image.get("generation_prompt") or ""),
            # brand_dna quality
            "brand_dna_word_count": len((brand_dna or "").split()),
            "brand_dna_has_colors": "#5e204d" in brand_dna.lower() or "#9c7945" in brand_dna.lower(),
            "brand_dna_has_json": '"style_reference_analysis"' in brand_dna,
            # Meta
            "warning_codes": warning_codes,
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
    scenario: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (metrics, full_callback_dump)."""
    started = time.time()
    try:
        callback = reason(envelope, gemini=client, extras_truncation=extras_truncation)
        dump = callback.model_dump(mode="json")
        metrics = _extract_run_metrics(dump, scenario)
        metrics["exception"] = None
        return metrics, dump
    except Exception as exc:  # noqa: BLE001
        empty: dict[str, Any] = {}
        return {
            "scenario_id": scenario["id"],
            "scenario_label": scenario["label"],
            "expected_surface": scenario["expected_surface"],
            "status": "FAILED",
            "error_message": f"{type(exc).__name__}: {exc}",
            "exception": f"{type(exc).__name__}: {exc}",
            "latency_ms": int((time.time() - started) * 1000),
            "red_flag": False,
            "warning_codes": [],
        }, empty


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "Y" if v else "N"
    return str(v).replace("|", "/")


def _render_report(
    results: list[dict[str, Any]],
    model_name: str,
    total_seconds: float,
) -> str:
    lines: list[str] = []
    lines.append(f"# Nubiex Power Test — {REPORT_DATE}")
    lines.append("")
    lines.append(f"- Model: `{model_name}`")
    lines.append(f"- Scenarios: {len(results)} (10 total)")
    lines.append(f"- Total wall time: {total_seconds:.1f}s")
    lines.append("")

    # ---- Run table ----
    lines.append("## Resultados por escenario")
    lines.append("")
    lines.append(
        "| # | label | status | surface | ✓surf | pillar | CTA | "
        "imgs | CONCEPT | Imagen | Tipo | brand_dna_wc | latency | red |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    for r in results:
        lines.append(
            "| {id} | {lbl} | {st} | {sf} | {sc} | {pl} | {ch} | "
            "{imgs} | {cp} | {im} | {tp} | {wc} | {lat} | {rf} |".format(
                id=r.get("scenario_id", "?"),
                lbl=r.get("scenario_label", "?")[:30],
                st=r.get("status", "?"),
                sf=_fmt(r.get("surface_format")),
                sc=_fmt(r.get("surface_correct")),
                pl=_fmt(r.get("content_pillar")),
                ch=_fmt(r.get("cta_channel")),
                imgs=_fmt(r.get("gallery_assets_selected", 0)),
                cp=_fmt(r.get("concept_prefix_ok")),
                im=_fmt(r.get("concept_imagen_present")),
                tp=_fmt(r.get("concept_tipo_present")),
                wc=_fmt(r.get("brand_dna_word_count", 0)),
                lat=r.get("latency_ms", 0),
                rf=_fmt(r.get("red_flag")),
            )
        )

    lines.append("")

    # ---- Aggregate stats ----
    lines.append("## Estadísticas agregadas")
    lines.append("")
    n = len(results)
    completed = sum(1 for r in results if r.get("status") == "COMPLETED")
    failed = n - completed
    surface_correct = sum(1 for r in results if r.get("surface_correct"))
    imgs_selected = sum(1 for r in results if (r.get("gallery_assets_selected") or 0) > 0)
    concept_ok = sum(1 for r in results if r.get("concept_prefix_ok"))
    imagen_ok = sum(1 for r in results if r.get("concept_imagen_present"))
    tipo_ok = sum(1 for r in results if r.get("concept_tipo_present"))
    brand_dna_ok = sum(1 for r in results if r.get("brand_dna_has_colors") and r.get("brand_dna_has_json"))
    red_flags = sum(1 for r in results if r.get("red_flag"))
    degraded = sum(1 for r in results if r.get("degraded"))
    repairs = sum(1 for r in results if r.get("repair_attempted"))
    latencies = [r["latency_ms"] for r in results if r.get("latency_ms")]

    lines.append(f"- Runs: {n} (completed={completed}, failed={failed})")
    lines.append(f"- Surface format correcto: {surface_correct}/{n}")
    lines.append(f"- Imágenes de galería seleccionadas: {imgs_selected}/{n}")
    lines.append(f"- CONCEPT prefix correcto ('CONCEPT —'): {concept_ok}/{n}")
    lines.append(f"- Línea Imagen presente en CONCEPT: {imagen_ok}/{n}")
    lines.append(f"- Línea Tipo presente en CONCEPT: {tipo_ok}/{n}")
    lines.append(f"- Brand DNA completo (colores + JSON): {brand_dna_ok}/{n}")
    lines.append(f"- Red flags: {red_flags}")
    lines.append(f"- Degraded: {degraded}")
    lines.append(f"- Repair attempted: {repairs}")
    if latencies:
        lines.append(
            f"- Latency ms: p50={int(statistics.median(latencies))}, "
            f"min={min(latencies)}, max={max(latencies)}, avg={int(statistics.mean(latencies))}"
        )
    lines.append("")

    # ---- CONCEPT samples ----
    lines.append("## CONCEPT samples (primeras 3 corridas completadas)")
    lines.append("")
    shown = 0
    for r in results:
        if r.get("status") != "COMPLETED" or shown >= 3:
            continue
        lines.append(f"### Escenario {r['scenario_id']}: {r['scenario_label']}")
        lines.append(f"- Surface: `{r.get('surface_format')}` | Pillar: `{r.get('content_pillar')}`")
        lines.append(f"- Imagen seleccionada: {r.get('concept_imagen_line') or '—'}")
        lines.append(f"- Tipo: {r.get('concept_tipo_line') or '—'}")
        lines.append(f"- Emotional beat: `{r.get('emotional_beat')}`")
        lines.append("")
        shown += 1

    # ---- Warning frequency ----
    all_warnings: list[str] = []
    for r in results:
        all_warnings.extend(r.get("warning_codes") or [])
    if all_warnings:
        from collections import Counter
        counts = Counter(all_warnings).most_common()
        lines.append("## Warning codes")
        lines.append("")
        for code, cnt in counts:
            lines.append(f"- `{code}` × {cnt}")
        lines.append("")

    # ---- Raw JSON appendix ----
    lines.append("## Raw JSON (todos los runs)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if not os.environ.get("MARKETER_RUN_LIVE"):
        print("ERROR: set MARKETER_RUN_LIVE=1 to run live Gemini calls.", file=sys.stderr)
        sys.exit(2)

    settings = load_settings()
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set. Aborting.", file=sys.stderr)
        sys.exit(2)

    base_envelope = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    client = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    full_outputs: list[dict[str, Any]] = []
    overall_started = time.time()

    for scenario in SCENARIOS:
        envelope = copy.deepcopy(base_envelope)
        envelope["task_id"] = f"nubiex-{scenario['id']:04d}-power-test"
        envelope["correlation_id"] = f"nubiex-power-{scenario['id']}"
        envelope["payload"]["client_request"]["description"] = scenario["description"]

        t0 = time.time()
        metrics, full_dump = _run_once(envelope, client, settings.extras_list_truncation, scenario)

        if metrics.get("exception"):
            print(
                f"[{scenario['id']:2d}/{len(SCENARIOS)}] {scenario['label']} FAILED "
                f"({metrics['exception']}); retrying once..."
            )
            metrics, full_dump = _run_once(envelope, client, settings.extras_list_truncation, scenario)

        elapsed = time.time() - t0
        status = metrics.get("status", "?")
        sf = metrics.get("surface_format") or "-"
        sf_ok = "OK" if metrics.get("surface_correct") else "NO"
        imgs = metrics.get("gallery_assets_selected", 0)
        concept = "OK" if metrics.get("concept_prefix_ok") else "NO"
        lat = metrics.get("latency_ms", 0)
        warns = len(metrics.get("warning_codes") or [])
        print(
            f"[{scenario['id']:2d}/{len(SCENARIOS)}] {scenario['label'][:35]:<35} "
            f"{status} surface={sf}({sf_ok}) imgs={imgs} CONCEPT={concept} "
            f"lat={lat}ms warns={warns} wall={elapsed:.1f}s"
        )
        results.append(metrics)
        full_outputs.append({"scenario": scenario, "callback": full_dump})

    total = time.time() - overall_started
    report_md = _render_report(results, model_name=client.model_name, total_seconds=total)

    md_path = REPORT_DIR / f"nubiex_power_test_{REPORT_DATE}.md"
    json_path = REPORT_DIR / f"nubiex_power_test_{REPORT_DATE}.json"
    full_path = REPORT_DIR / f"nubiex_power_test_{REPORT_DATE}_full.json"
    md_path.write_text(report_md, encoding="utf-8")
    json_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    full_path.write_text(
        json.dumps(full_outputs, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nReport: {md_path}")
    print(f"JSON:   {json_path}")
    print(f"Full:   {full_path}")


if __name__ == "__main__":
    main()
