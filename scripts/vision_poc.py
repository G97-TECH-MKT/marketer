#!/usr/bin/env python3
"""Vision POC — marketer ve las imágenes antes de proponer visual_selection.

Script aislado (no toca src/marketer/). Construye un envelope simulando lo que
ROUTER mandaría, carga imágenes reales del folder `images/` (o URLs públicas
para clientes que ya tienen gallery accesible), resize a <2MB/imagen, las
inyecta como `Part` multimodal en la llamada a Gemini, y guarda el output
para análisis cross-run y cross-client.

Usage:
  MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/vision_poc.py

Requiere Pillow (pip install Pillow).
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from marketer.config import load_settings  # noqa: E402
from marketer.llm.gemini import serialize_for_prompt  # noqa: E402
from marketer.llm.prompts.create_post import CREATE_POST_OVERLAY  # noqa: E402
from marketer.llm.prompts.system import SYSTEM_PROMPT  # noqa: E402
from marketer.llm.prompts.vision_dna import VISION_DNA_PROMPT  # noqa: E402
from marketer.normalizer import normalize  # noqa: E402
from marketer.schemas.enrichment import (  # noqa: E402
    CallbackBody,
    CallbackOutputData,
    GalleryStats,
    PostEnrichment,
    TraceInfo,
)
from marketer.validator import validate_and_correct  # noqa: E402

IMAGES_DIR = ROOT / "images"
REPORTS_DIR = ROOT / "reports"
MAX_EDGE_PX = 1568  # Gemini multimodal sweet spot (tiles of 768px; 2 tiles fit)
JPEG_QUALITY = 82
RUNS_PER_CLIENT = 2  # para ver consistencia/varianza


# ─── Clientes a probar ───────────────────────────────────────────────────────

NUBIEX_IMAGE_FILES = [
    "Nubiex Valores 1.jpg",
    "Nubiex Valores 2.jpg",
    "Nubiex Valores 3.jpg",
    "Nubiex Valores 4.jpg",
]

NUBIEX_CONFIG = {
    "client_slug": "nubiex",
    "client_name": "Nubiex",
    "task_id_base": "poc-nubiex-",
    "account_uuid": "aaaa-nubiex-uuid-00000000000000000001",
    "request": (
        "Crea un post que presente nuestros 4 valores corporativos apoyándonos "
        "en la mejor imagen del set. Debe inspirar cultura de innovación y "
        "colaboración, tono profesional pero cercano."
    ),
    "brief": {
        "FIELD_COMPANY_NAME": "Nubiex",
        "FIELD_COMPANY_CATEGORY": "Tecnología",
        "FIELD_COUNTRY": "España",
        "FIELD_LARGE_ANSWER": (
            "Nubiex es una consultora tecnológica centrada en transformar "
            "equipos mediante cultura de datos, colaboración radical y "
            "experimentación continua."
        ),
        "FIELD_PRODUCTS_SERVICES_ANSWER": (
            "Consultoría en transformación digital, programas de liderazgo "
            "basados en datos, arquitectura de plataformas colaborativas."
        ),
        "FIELD_TARGET_CUSTOMER_ANSWER": (
            "Directivos de medianas y grandes empresas que quieren mejorar "
            "la velocidad y calidad de decisión de sus equipos."
        ),
        "FIELD_VALUE_PROPOSITION": (
            "Cultura de datos y colaboración aplicada, no teoría; "
            "resultados medibles en 90 días."
        ),
        "FIELD_COMMUNICATION_LANGUAGE": "spanish",
        "FIELD_COMMUNICATION_STYLE": "professional",
        "FIELD_COLOR_LIST_PICKER": ["#0B5FFF", "#14142B", "#F2F4F8"],
        "FIELD_FONT_STYLE": "sans",
        "FIELD_POST_CONTENT_STYLE": "image_text",
        "FIELD_KEYWORDS_TAGS_INPUT": [
            "cultura de datos",
            "colaboración",
            "liderazgo",
            "transformación digital",
        ],
        "FIELD_HAS_BRAND_MATERIAL": True,
        "FIELD_BRAND_MATERIAL": [],
        "FIELD_WEBSITE_URL": "https://www.nubiex.example",
    },
    "image_source": "local",  # usar files del folder `images/`
    "image_files": NUBIEX_IMAGE_FILES,
    # URLs placeholder estilo S3 para que el LLM pueda referenciarlas en visual_selection.
    "mock_urls": [
        f"https://mock-s3.plinng.local/nubiex/{f.replace(' ', '_')}"
        for f in NUBIEX_IMAGE_FILES
    ],
}

CASA_MARUJA_IMAGE_URLS = [
    "https://i.pinimg.com/736x/00/2f/ef/002fefd0c200e93fd65f823cac70ed05.jpg",
]

CASA_MARUJA_CONFIG = {
    "client_slug": "casa_maruja",
    "client_name": "Casa Maruja",
    "task_id_base": "poc-casamaruja-",
    "account_uuid": "9b1c0f12-0d8b-4a46-aea5-2a2cc4b47f21",
    "request": (
        "Crea un post destacando el plato estrella de la semana, producto de "
        "temporada, tono cercano y sin florituras."
    ),
    "brief": {
        "FIELD_COMPANY_NAME": "Casa Maruja",
        "FIELD_COMPANY_CATEGORY": "Restauración",
        "FIELD_COUNTRY": "España",
        "FIELD_LARGE_ANSWER": (
            "Cocina de mercado en Ruzafa, Valencia. Recetario de la abuela. "
            "Menú del día a 12 €. Especialidades: arròs al forn los lunes, "
            "croquetas de puchero."
        ),
        "FIELD_PRODUCTS_SERVICES_ANSWER": "Restaurante de cocina de mercado.",
        "FIELD_TARGET_CUSTOMER_ANSWER": "Vecinos de Ruzafa que buscan comida honesta.",
        "FIELD_VALUE_PROPOSITION": "Cocina casera sin florituras.",
        "FIELD_COMMUNICATION_LANGUAGE": "spanish",
        "FIELD_COMMUNICATION_STYLE": "friendly",
        "FIELD_COLOR_LIST_PICKER": ["#8B5A2B", "#D4A017", "#556B2F"],
        "FIELD_FONT_STYLE": "sans",
        "FIELD_POST_CONTENT_STYLE": "image_text",
        "FIELD_KEYWORDS_TAGS_INPUT": [
            "cocina de mercado",
            "producto de temporada",
            "Ruzafa",
        ],
        "FIELD_HAS_BRAND_MATERIAL": True,
        "FIELD_BRAND_MATERIAL": [],
        "FIELD_WEBSITE_URL": "https://www.casamaruja.example",
    },
    "image_source": "remote",
    "image_urls": CASA_MARUJA_IMAGE_URLS,
    "mock_urls": CASA_MARUJA_IMAGE_URLS,
}


CLIENTS = [NUBIEX_CONFIG, CASA_MARUJA_CONFIG]


# ─── Image helpers ───────────────────────────────────────────────────────────


def _resize_encode_jpeg(img_bytes: bytes, source_label: str) -> tuple[bytes, tuple[int, int]]:
    """Return JPEG bytes resized to MAX_EDGE_PX on longest side + (w, h)."""
    img = Image.open(io.BytesIO(img_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, MAX_EDGE_PX / max(w, h))
    if scale < 1.0:
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    data = buf.getvalue()
    print(f"  [{source_label}] {len(img_bytes)/1024:.0f}KB -> {len(data)/1024:.0f}KB ({img.size[0]}x{img.size[1]})")
    return data, img.size


def load_local_image(filename: str) -> tuple[bytes, tuple[int, int]] | None:
    path = IMAGES_DIR / filename
    if not path.exists():
        print(f"  [MISS] {filename}")
        return None
    return _resize_encode_jpeg(path.read_bytes(), filename)


def load_remote_image(url: str) -> tuple[bytes, tuple[int, int]] | None:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"  [FETCH FAIL] {url}: {exc}")
        return None
    return _resize_encode_jpeg(raw, url.split("/")[-1][:40])


# ─── Envelope builder ────────────────────────────────────────────────────────


def build_envelope(cfg: dict, run_idx: int, image_urls: list[str], image_sizes: list[tuple[int, int]]) -> dict:
    task_id = f"{cfg['task_id_base']}{run_idx}-{int(time.time())}"
    items = [
        {
            "url": url,
            "name": url.rsplit("/", 1)[-1],
            "extension": "jpg",
            "mime_type": "image/jpeg",
            "width": w,
            "height": h,
            "role": "content",
            "tags": [],
            "description": "",
        }
        for url, (w, h) in zip(image_urls, image_sizes)
    ]
    return {
        "task_id": task_id,
        "job_id": f"{cfg['task_id_base']}job-{run_idx}",
        "action_code": "create_post",
        "correlation_id": f"poc-{cfg['client_slug']}-run{run_idx}",
        "callback_url": f"https://mock-router/api/v1/tasks/{task_id}/callback",
        "payload": {
            "client_request": {"description": cfg["request"], "attachments": []},
            "context": {
                "account_uuid": cfg["account_uuid"],
                "client_name": cfg["client_name"],
                "platform": "instagram",
            },
            "action_execution_gates": {
                "brief": {
                    "passed": True,
                    "reason": "ok",
                    "status_code": 200,
                    "response": {
                        "status": "success",
                        "data": {
                            "uuid": cfg["account_uuid"],
                            "profile": {
                                "business_name": cfg["brief"]["FIELD_COMPANY_NAME"],
                                "website_url": cfg["brief"].get("FIELD_WEBSITE_URL"),
                                "tone": cfg["brief"].get("FIELD_COMMUNICATION_STYLE"),
                            },
                            "brief": {"form_values": cfg["brief"]},
                        },
                    },
                },
                "image_catalog": {
                    "passed": True,
                    "reason": "ok",
                    "status_code": 200,
                    "response": {"status": "success", "data": items},
                },
            },
            "agent_sequence": {
                "current": {"step_code": "marketing_enrichment", "step_order": 1},
                "previous": {},
            },
        },
    }


# ─── Prompt + Gemini call (with vision) ──────────────────────────────────────


VISION_NOTE = f"""

# Visual evidence

The N images attached above this prompt, in order, match gallery[0..N-1] in
the Context block. You SEE the pixels — judge visual quality, palette fit,
composition and subject relevance by what you actually observe, not only by
tags/description metadata.

When populating `visual_selection`:
- recommended_asset_urls[]: pick the one or two images whose VISUAL
  characteristics (light, palette, composition, subject) most reinforce the
  chosen angle AND the brand_tokens.palette.
- avoid_asset_urls[]: list images that you see are visually off — blurry,
  low quality, off-brand (palette clash with brand), or off-topic for the
  specific angle. Cite the reason briefly in do_not[] when useful.
- If an image looks like a placeholder or broken, avoid it.

Cross-reference the text tags and descriptions when present, but trust the
pixels when they disagree.

# Design DNA analysis (for brand_dna.style_reference_analysis)

When you observe the images, apply the following blueprint to generate the
style_reference_analysis block inside brand_dna. Use what you SEE — lighting,
composition, depth, subject treatment — not the brief text.

{VISION_DNA_PROMPT}
"""


def build_prompt(envelope: dict, extras_truncation: int = 10) -> tuple[dict, str]:
    ctx, _ = normalize(envelope)
    payload = {
        "action_code": ctx.action_code,
        "surface": ctx.surface,
        "mode": ctx.mode,
        "user_request": ctx.user_request,
        "requested_surface_format": ctx.requested_surface_format,
        "context": {
            "account_uuid": ctx.account_uuid,
            "client_name": ctx.client_name,
            "platform": ctx.platform,
            "post_id": ctx.post_id,
            "website_id": ctx.website_id,
            "section_id": ctx.section_id,
        },
        "brief": ctx.brief.model_dump() if ctx.brief else None,
        "brand_tokens": ctx.brand_tokens.model_dump(),
        "available_channels": [c.model_dump() for c in ctx.available_channels],
        "brief_facts": ctx.brief_facts.model_dump(),
        "prior_post": ctx.prior_post.model_dump() if ctx.prior_post else None,
        "gallery": [item.model_dump() for item in ctx.gallery],
        "prior_step_outputs": ctx.prior_step_outputs or None,
    }
    rendered = serialize_for_prompt(payload, truncate_lists=extras_truncation)
    user_prompt = (
        f"{CREATE_POST_OVERLAY}\n\nContext:\n{rendered}\n\nReturn the PostEnrichment JSON now."
    )
    return payload, user_prompt


def run_one(client: genai.Client, model: str, cfg: dict, run_idx: int, image_parts: list, image_urls: list[str], image_sizes: list[tuple[int, int]]) -> dict:
    """Run one POC request. Returns dict with router-contract-compatible callback.

    The `callback_body` field contains EXACTLY what marketer would PATCH to
    router (`CallbackBody` shape). Never diverges from the contract, even in
    failure paths.
    """
    envelope = build_envelope(cfg, run_idx, image_urls, image_sizes)
    ctx, normalize_warnings = normalize(envelope)
    _, user_prompt = build_prompt(envelope)
    system_prompt = SYSTEM_PROMPT + VISION_NOTE

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=PostEnrichment,
        temperature=0.4,
        max_output_tokens=8192,
    )

    contents = [*image_parts, user_prompt]

    t0 = time.time()
    gemini_error: str | None = None
    try:
        resp = client.models.generate_content(model=model, contents=contents, config=config)
    except Exception as exc:  # noqa: BLE001
        gemini_error = f"{type(exc).__name__}: {exc}"
        resp = None
    wall = int((time.time() - t0) * 1000)

    def _fail(msg: str) -> dict:
        callback = CallbackBody(status="FAILED", output_data=None, error_message=msg)
        return {
            "run_idx": run_idx,
            "client": cfg["client_name"],
            "status": "FAILED",
            "latency_ms": wall,
            "envelope": envelope,
            "callback_body": callback.model_dump(mode="json"),
        }

    if resp is None:
        return _fail(f"llm_error: {gemini_error}")

    parsed = getattr(resp, "parsed", None)
    if not isinstance(parsed, PostEnrichment):
        try:
            parsed = PostEnrichment.model_validate_json(resp.text or "")
        except Exception as exc:  # noqa: BLE001
            return _fail(f"schema_validation_failed: {type(exc).__name__}: {exc}")

    # Run validator to match full reasoner behavior (hallucination scrub, cta coherence, etc.)
    parsed, validator_warnings, blocking = validate_and_correct(parsed, ctx)
    if blocking:
        return _fail(f"schema_validation_failed: {blocking}")

    all_warnings = [*normalize_warnings, *validator_warnings]
    degraded = any(
        w.code in ("brief_missing", "gallery_empty", "gallery_all_filtered")
        for w in all_warnings
    )

    trace = TraceInfo(
        task_id=ctx.task_id,
        action_code=ctx.action_code,
        surface=ctx.surface,
        mode=ctx.mode,
        latency_ms=wall,
        gemini_model=model,
        repair_attempted=False,
        degraded=degraded,
        gallery_stats=GalleryStats(
            raw_count=ctx.gallery_raw_count,
            accepted_count=len(ctx.gallery),
            rejected_count=ctx.gallery_rejected_count,
            truncated=ctx.gallery_truncated,
        ),
    )

    callback = CallbackBody(
        status="COMPLETED",
        output_data=CallbackOutputData(
            enrichment=parsed,
            warnings=all_warnings,
            trace=trace,
        ),
        error_message=None,
    )

    return {
        "run_idx": run_idx,
        "client": cfg["client_name"],
        "status": "OK",
        "latency_ms": wall,
        "envelope": envelope,
        # ← EXACTLY the shape router expects (see docs/ROUTER CONTRACT.md §4
        #   and src/marketer/schemas/enrichment.py::CallbackBody).
        "callback_body": callback.model_dump(mode="json"),
    }


# ─── Main driver ─────────────────────────────────────────────────────────────


def load_images_for_client(cfg: dict) -> tuple[list[bytes], list[tuple[int, int]], list[str]]:
    """Load + resize all images for this client. Returns (bytes[], sizes[], urls[])."""
    print(f"\n[{cfg['client_name']}] loading images...")
    if cfg["image_source"] == "local":
        files = cfg["image_files"]
        urls = cfg["mock_urls"]
        out_bytes, out_sizes, out_urls = [], [], []
        for fname, url in zip(files, urls):
            result = load_local_image(fname)
            if result is None:
                continue
            data, size = result
            out_bytes.append(data)
            out_sizes.append(size)
            out_urls.append(url)
        return out_bytes, out_sizes, out_urls
    else:  # remote
        urls = cfg["image_urls"]
        out_bytes, out_sizes, out_urls = [], [], []
        for url in urls:
            result = load_remote_image(url)
            if result is None:
                continue
            data, size = result
            out_bytes.append(data)
            out_sizes.append(size)
            out_urls.append(url)
        return out_bytes, out_sizes, out_urls


def summarize_results(all_runs: list[dict]) -> str:
    lines = []
    by_client: dict[str, list[dict]] = {}
    for r in all_runs:
        by_client.setdefault(r["client"], []).append(r)

    lines.append(f"# Vision POC — {len(all_runs)} runs over {len(by_client)} clients\n")
    lines.append(
        "_Each run's `callback_body` in `vision_poc_runs.json` is the EXACT shape "
        "marketer would PATCH to router (CallbackBody: `status`, "
        "`output_data.{enrichment, warnings, trace}`, `error_message`)._\n"
    )

    def _enrichment_of(r: dict) -> dict | None:
        cb = r.get("callback_body") or {}
        if cb.get("status") != "COMPLETED":
            return None
        return (cb.get("output_data") or {}).get("enrichment")

    for client, runs in by_client.items():
        lines.append(f"\n## {client}\n")
        for r in runs:
            lines.append(f"### Run {r['run_idx']} — {r['status']} — {r['latency_ms']}ms\n")
            cb = r.get("callback_body") or {}
            if cb.get("status") != "COMPLETED":
                lines.append(f"- status: `{cb.get('status', r['status'])}`")
                lines.append(f"- error_message: `{cb.get('error_message', '—')}`\n")
                continue
            output = cb["output_data"]
            e = output["enrichment"]
            trace = output["trace"]
            warnings = output["warnings"]
            vs = e["visual_selection"]
            cta = e["cta"]
            caption = e["caption"]
            conf = e["confidence"]
            bi = e["brand_intelligence"]
            lines.append(f"- **callback_body.status**: {cb['status']}")
            lines.append(f"- **trace.latency_ms**: {trace['latency_ms']} · **degraded**: {trace['degraded']} · **repair_attempted**: {trace['repair_attempted']}")
            lines.append(f"- **trace.gallery_stats**: {trace['gallery_stats']}")
            lines.append(f"- **warnings** ({len(warnings)}): {[w['code'] for w in warnings]}")
            lines.append(f"- **surface_format**: {e['surface_format']} · **pillar**: {e['content_pillar']}")
            lines.append(f"- **title**: {e['title']}")
            lines.append(f"- **angle**: {e['strategic_decisions']['angle']['chosen']}")
            lines.append(f"- **voice**: {e['strategic_decisions']['voice']['chosen']}")
            lines.append(f"- **cta**: channel={cta['channel']} label={cta['label']!r}")
            lines.append("- **visual_selection:**")
            lines.append(f"  - recommended: {vs['recommended_asset_urls']}")
            lines.append(f"  - avoid: {vs['avoid_asset_urls']}")
            lines.append(f"- **confidence**: {conf}")
            lines.append(f"- **brand_intelligence.emotional_beat**: {bi['emotional_beat']}")
            lines.append(f"- **brand_intelligence.rhetorical_device**: {bi['rhetorical_device']}")
            lines.append(f"- **caption.hook** ({len(caption['hook'])} ch): {caption['hook']!r}")
            lines.append(f"- **cf_post_brief** ({len(e['cf_post_brief'])} ch, first 220): {e['cf_post_brief'][:220]!r}")
            lines.append("")

        # Consistency analysis
        ok = [r for r in runs if _enrichment_of(r) is not None]
        if len(ok) >= 2:
            lines.append(f"\n**Consistency across {len(ok)} OK runs:**")
            pillars = {_enrichment_of(r)["content_pillar"] for r in ok}
            channels = {_enrichment_of(r)["cta"]["channel"] for r in ok}
            surfaces = {_enrichment_of(r)["surface_format"] for r in ok}
            recs = [set(_enrichment_of(r)["visual_selection"]["recommended_asset_urls"]) for r in ok]
            common_rec = set.intersection(*recs) if recs else set()
            union_rec = set.union(*recs) if recs else set()
            avoids = [set(_enrichment_of(r)["visual_selection"]["avoid_asset_urls"]) for r in ok]
            common_avoid = set.intersection(*avoids) if avoids else set()
            lines.append(f"- surface_format agreement: {surfaces}")
            lines.append(f"- content_pillar agreement: {pillars}")
            lines.append(f"- cta.channel agreement: {channels}")
            lines.append(f"- recommended URL intersection: {list(common_rec)}")
            lines.append(f"- recommended URL union: {list(union_rec)}")
            lines.append(f"- avoid URL intersection: {list(common_avoid)}")

    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        return 1

    REPORTS_DIR.mkdir(exist_ok=True)
    client = genai.Client(api_key=settings.gemini_api_key)

    all_runs: list[dict] = []

    for cfg in CLIENTS:
        image_bytes, image_sizes, image_urls = load_images_for_client(cfg)
        if not image_bytes:
            print(f"[{cfg['client_name']}] SKIP — no images loaded")
            continue

        image_parts = [
            types.Part.from_bytes(data=data, mime_type="image/jpeg")
            for data in image_bytes
        ]
        total_mb = sum(len(d) for d in image_bytes) / (1024 * 1024)
        print(f"[{cfg['client_name']}] {len(image_parts)} images, {total_mb:.1f} MB total, {RUNS_PER_CLIENT} runs")

        for run_idx in range(1, RUNS_PER_CLIENT + 1):
            print(f"  run {run_idx}/{RUNS_PER_CLIENT}...", end=" ", flush=True)
            result = run_one(client, settings.gemini_model, cfg, run_idx, image_parts, image_urls, image_sizes)
            all_runs.append(result)
            cb = result.get("callback_body", {})
            if cb.get("status") == "COMPLETED":
                e = cb["output_data"]["enrichment"]
                rec = e["visual_selection"]["recommended_asset_urls"]
                avoid = e["visual_selection"]["avoid_asset_urls"]
                warns = [w["code"] for w in cb["output_data"]["warnings"]]
                print(f"COMPLETED {result['latency_ms']}ms rec={len(rec)} avoid={len(avoid)} warnings={warns}")
            else:
                print(f"{cb.get('status', result['status'])}: {cb.get('error_message', '')[:120]}")

    # Persist everything
    runs_path = REPORTS_DIR / "vision_poc_runs.json"
    runs_path.write_text(json.dumps(all_runs, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = REPORTS_DIR / "vision_poc_summary.md"
    report_path.write_text(summarize_results(all_runs), encoding="utf-8")

    print(f"\n[artifacts]")
    print(f"  {runs_path}")
    print(f"  {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
