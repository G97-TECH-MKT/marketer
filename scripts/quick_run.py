#!/usr/bin/env python3
"""Single-run fast iteration tool for Nubiex.

Run one scenario against the Nubiex fixture, append to the runs log,
rebuild the dashboard, and open it in the browser.

Usage:
    MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/quick_run.py
    MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/quick_run.py "Crea un post sobre energía tántrica"
    MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/quick_run.py --scenario 3
    MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/quick_run.py --scenario 3 "Override description"

Scenarios (default descriptions):
  1  post      producto    masaje holístico
  2  post      educación   tipos de masaje
  3  story     awareness   bienestar masculino
  4  reel      ambiente    ritual del espacio
  5  carousel  educación   4 pilares
  6  post      comunidad   espacio seguro
  7  story     promoción   primera sesión
  8  reel      energía     transformación
  9  post      bts         Bruno behind the scenes
 10  post      conversión  reserva sesión
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC  = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from marketer.config import load_settings   # noqa: E402
from marketer.llm.gemini import GeminiClient  # noqa: E402
from marketer.reasoner import reason        # noqa: E402

FIXTURE_PATH   = ROOT / "fixtures" / "envelopes" / "nubiex_post.json"
RUNS_LOG       = ROOT / "reports" / "quick_runs.json"
DASHBOARD_PATH = ROOT / "samples" / "nubiex_dashboard.html"

SCENARIO_DESCS: dict[int, str] = {
    1: "Crea un post para Instagram presentando el servicio de masaje holístico y tántrico de Nubiex, destacando el espacio exclusivo, seguro y transformador para hombres en Barcelona.",
    2: "Crea un post educativo explicando la diferencia entre masaje holístico, tántrico, Lomi Lomi hawaiano y quiromasaje, para que los clientes entiendan el enfoque único de Nubiex en Barcelona.",
    3: "Crea una story de Instagram para generar conciencia sobre la propuesta de bienestar masculino integral de Nubiex, destacando la importancia de reconectar con el cuerpo.",
    4: "Crea el brief para un reel de Instagram mostrando el ambiente y la preparación ritual del espacio de masaje de Nubiex.",
    5: "Crea un carrusel de Instagram educativo explicando los cuatro pilares del bienestar de Nubiex: cuerpo, mente, energía y bienestar emocional.",
    6: "Crea un post de comunidad para Instagram destacando el valor de un espacio seguro, discreto y respetuoso para el bienestar masculino en Barcelona.",
    7: "Crea una story promocional para Instagram orientada a nuevos clientes en Barcelona, destacando la primera sesión con Bruno como experiencia transformadora.",
    8: "Crea el brief para un reel corto de Instagram mostrando la transformación emocional y energética que viven los clientes durante una sesión de masaje consciente de Nubiex.",
    9: "Crea un post de Instagram tipo behind the scenes mostrando la filosofía y preparación de Bruno para cada sesión de Nubiex.",
    10: "Crea un post de Instagram con llamada a la acción directa para reservar una sesión con Bruno en Nubiex Men's Massage Barcelona.",
}

GALLERY_LOCAL: dict[str, str] = {
    "https://cdn.nubiex.es/brand/nubiex_valores_1.jpg": "../images/Nubiex Valores 1.jpg",
    "https://cdn.nubiex.es/brand/nubiex_valores_2.jpg": "../images/Nubiex Valores 2.jpg",
    "https://cdn.nubiex.es/brand/nubiex_valores_3.jpg": "../images/Nubiex Valores 3.jpg",
    "https://cdn.nubiex.es/brand/nubiex_valores_4.jpg": "../images/Nubiex Valores 4.jpg",
}
GALLERY_NAMES: dict[str, str] = {
    "https://cdn.nubiex.es/brand/nubiex_valores_1.jpg": "nubiex_valores_1.jpg",
    "https://cdn.nubiex.es/brand/nubiex_valores_2.jpg": "nubiex_valores_2.jpg",
    "https://cdn.nubiex.es/brand/nubiex_valores_3.jpg": "nubiex_valores_3.jpg",
    "https://cdn.nubiex.es/brand/nubiex_valores_4.jpg": "nubiex_valores_4.jpg",
}


# ── run ──────────────────────────────────────────────────────────────────────

def do_run(description: str, scenario_id: int, client: GeminiClient, extras: int, max_tokens: int = 16384) -> dict[str, Any]:
    base = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    envelope = copy.deepcopy(base)
    run_id = int(time.time() * 1000) % 10_000_000
    envelope["task_id"]       = f"qr-{run_id}"
    envelope["correlation_id"] = f"quick-{run_id}"
    envelope["payload"]["client_request"]["description"] = description

    t0 = time.time()
    try:
        cb = reason(envelope, gemini=client, extras_truncation=extras, max_output_tokens=max_tokens)
        dump = cb.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        return {
            "run_id": run_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "scenario_id": scenario_id,
            "description": description,
            "status": "FAILED",
            "error": str(exc),
            "latency_ms": int((time.time() - t0) * 1000),
        }

    od = dump.get("output_data") or {}
    en = od.get("enrichment") or {}
    tr = od.get("trace") or {}
    ws = od.get("warnings") or []
    cf_data = od.get("data") or {}
    cap = en.get("caption") or {}
    vs  = en.get("visual_selection") or {}
    bi  = en.get("brand_intelligence") or {}
    cf  = en.get("cf_post_brief") or ""

    input_tok  = tr.get("input_tokens", 0)
    output_tok = tr.get("output_tokens", 0)
    thought_tok = tr.get("thoughts_tokens", 0)
    # Approximate Flash pricing (est.)
    cost_usd = (input_tok * 0.15 + (output_tok + thought_tok) * 0.60) / 1_000_000

    im_m = re.search(r"Imagen:\s*(.+)", cf)
    tp_m = re.search(r"Tipo:\s*(.+)", cf)

    selected = vs.get("recommended_asset_urls") or []
    selected_names = [GALLERY_NAMES.get(u, u) for u in selected]

    concept_part = cf.split("Caption:")[0].strip() if "Caption:" in cf else cf

    return {
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "scenario_id": scenario_id,
        "description": description,
        "status": dump.get("status"),
        "latency_ms": tr.get("latency_ms", int((time.time() - t0) * 1000)),
        "repair": tr.get("repair_attempted", False),
        "degraded": tr.get("degraded", False),
        "surface": en.get("surface_format"),
        "pillar": en.get("content_pillar"),
        "angle": (en.get("strategic_decisions") or {}).get("angle", {}).get("chosen"),
        "voice": (en.get("strategic_decisions") or {}).get("voice", {}).get("chosen"),
        "emotional_beat": bi.get("emotional_beat"),
        "funnel": bi.get("funnel_stage_target"),
        "hook": cap.get("hook"),
        "body": cap.get("body"),
        "cta_line": cap.get("cta_line"),
        "cta_channel": (en.get("cta") or {}).get("channel"),
        "concept_block": concept_part,
        "imagen_line": im_m.group(1).strip() if im_m else None,
        "tipo_line": tp_m.group(1).strip() if tp_m else None,
        "selected_urls": selected,
        "selected_names": selected_names,
        "brand_dna": en.get("brand_dna"),
        "gen_prompt": (en.get("image") or {}).get("generation_prompt"),
        "hashtags": (en.get("hashtag_strategy") or {}).get("tags") or [],
        "warnings": [w.get("code") for w in ws],
        "cf_post_brief": cf,
        "cf_data": cf_data,
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "thoughts_tokens": thought_tok,
        "cost_usd": round(cost_usd, 6),
    }


# ── dashboard HTML builder ────────────────────────────────────────────────────

def _e(s: Any) -> str:
    import html
    return html.escape(str(s or ""), quote=True)

def _nl(s: str) -> str:
    import html
    return html.escape(str(s or "")).replace("\n", "<br>")

def render_concept_html(block: str) -> str:
    lines = block.split("\n")
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("CONCEPT —"):
            out.append(f'<div class="ct">{_e(s)}</div>')
        elif s.startswith("Imagen:"):
            out.append(f'<div class="cm"><span class="ck">Imagen</span><span class="cv">{_e(s[7:].strip())}</span></div>')
        elif s.startswith("Tipo:"):
            out.append(f'<div class="cm"><span class="ck">Tipo</span><span class="cv tp">{_e(s[5:].strip())}</span></div>')
        else:
            out.append(f'<div class="cb">{_e(s)}</div>')
    return "\n".join(out)

def render_img_strip(selected_urls: list[str]) -> str:
    tiles = []
    for cdn_url, local_path in GALLERY_LOCAL.items():
        name = GALLERY_NAMES[cdn_url]
        if cdn_url in selected_urls:
            cls, badge = "ti use", '<span class="bd bd-use">USE</span>'
        else:
            cls, badge = "ti", '<span class="bd bd-no">—</span>'
        tiles.append(
            f'<figure class="{cls}">{badge}'
            f'<img src="{_e(local_path)}" alt="{_e(name)}">'
            f'<figcaption>{_e(name)}</figcaption></figure>'
        )
    return "\n".join(tiles)

_COST_NOTE = "est. Flash pricing"

def _token_pill(r: dict) -> str:
    it = r.get("input_tokens", 0)
    ot = r.get("output_tokens", 0)
    tt = r.get("thoughts_tokens", 0)
    cost = r.get("cost_usd", 0.0)
    if not it and not ot:
        return ""
    th_part = f" +{tt}th" if tt else ""
    return (f'<span class="tok-pill" title="{_COST_NOTE}">'
            f'{it}in / {ot}out{th_part} &nbsp;~${cost:.4f}</span>')


def render_run_card(r: dict[str, Any], label: str = "") -> str:
    sel      = r.get("selected_urls") or []
    concept  = r.get("concept_block") or ""
    cf_full  = r.get("cf_post_brief") or ""
    hook     = r.get("hook") or ""
    body_txt = r.get("body") or ""
    cta_txt  = r.get("cta_line") or ""
    tags     = r.get("hashtags") or []
    dna      = r.get("brand_dna") or ""
    status   = r.get("status") or "?"
    sf       = r.get("surface") or "—"
    pil      = r.get("pillar") or "—"
    lat      = f"{(r.get('latency_ms') or 0) / 1000:.1f}s"
    beat     = r.get("emotional_beat") or "—"
    angle    = r.get("angle") or "—"
    ts_raw   = r.get("ts") or ""
    ts_disp  = ts_raw[:19].replace("T", " ") if ts_raw else "—"
    repair_badge = '<span class="chip warn">repair</span>' if r.get("repair") else ""
    status_cls = "ok" if status == "COMPLETED" else "bad"
    tags_html  = " ".join(f'<code class="tag">{_e(t)}</code>' for t in tags)

    import json as _json
    cf_data = r.get("cf_data") or {}
    cf_json = _json.dumps(cf_data, ensure_ascii=False, indent=2) if cf_data else _json.dumps({
        "total_items": 1,
        "client_dna": dna,
        "client_request": cf_full,
        "resources": r.get("selected_urls") or [],
    }, ensure_ascii=False, indent=2)
    card_id = f"card-{abs(hash(ts_raw))}"

    # Caption / brief section — surface-specific layout
    is_carousel = sf == "carousel"
    if is_carousel:
        # Show full carousel brief (Carrusel — overview + Slide N + Caption)
        cf_main_html = f'<div class="cap-card carousel-raw"><div class="cap-lbl">CF BRIEF — CAROUSEL</div><div class="cap-txt cf-raw-txt">{_nl(cf_full)}</div></div>'
    else:
        cf_main_html = f"""
      <div class="cap-card hook-cap">
        <div class="cap-lbl">HOOK <span class="char-n">{len(hook)} chars</span></div>
        <div class="cap-txt hook-txt">{_nl(hook)}</div>
      </div>
      <div class="cap-card">
        <div class="cap-lbl">BODY</div>
        <div class="cap-txt">{_nl(body_txt)}</div>
      </div>
      {'<div class="cap-card cta-cap"><div class="cap-lbl">CTA</div><div class="cap-txt cta-txt">' + _nl(cta_txt) + '</div></div>' if cta_txt else ''}"""

    label_badge = f'<span class="run-label">{_e(label)}</span>' if label else ""

    return f"""
<div class="run-card" id="{card_id}">
  <div class="lc-head">
    <div class="lc-left">
      {label_badge}
      <span class="status-{status_cls}">{_e(status)}</span>
      <span class="sf-pill">{_e(sf)}</span>
      <span class="pil-pill">{_e(pil)}</span>
      <span class="muted-pill">{_e(beat)}</span>
      <span class="lat-pill">{_e(lat)}</span>
      {_token_pill(r)}
      {repair_badge}
    </div>
    <span class="ts-lbl">{_e(ts_disp)}</span>
  </div>
  <p class="angle-line">{_e(angle)}</p>

  <div class="lc-grid">
    <div class="lc-left-col">
      <div class="section-lbl cf-lbl">Para Content Factory</div>
      {'<div class="concept-card">' + render_concept_html(concept) + '</div>' if not is_carousel else ''}
      {cf_main_html}
      {'<div class="tags-row">' + tags_html + '</div>' if tags_html and not is_carousel else ''}
    </div>
    <div class="lc-right-col">
      <div class="section-lbl">Imagenes</div>
      <div class="img-strip">{render_img_strip(sel)}</div>
      {'<div class="section-lbl" style="margin-top:14px">Gen prompt</div><div class="genprompt">' + _e(r.get("gen_prompt") or "") + '</div>' if r.get("gen_prompt") else ''}
    </div>
  </div>

  {'<details class="dna-details"><summary class="dna-summary">Brand DNA</summary><div class="dna-body"><pre class="dna-pre">' + _e(dna) + '</pre></div></details>' if dna else ''}

  <details class="cf-details">
    <summary class="cf-summary">CF JSON — copiar para compartir
      <button class="copy-btn" onclick="copyJson('{card_id}')">Copiar</button>
    </summary>
    <div class="cf-json-wrap">
      <textarea id="json-{card_id}" class="cf-json-ta" readonly>{_e(cf_json)}</textarea>
    </div>
  </details>
</div>"""


def build_dashboard(runs: list[dict[str, Any]]) -> str:
    if not runs:
        recent_html  = "<p style='color:var(--muted);padding:40px'>No runs yet. Run quick_run.py.</p>"
        history_html = ""
    else:
        # Show last 2 runs in full cards
        recent = runs[-2:] if len(runs) >= 2 else runs[-1:]
        recent_cards = [render_run_card(r, label=f"Run #{i+1}" if len(recent) > 1 else "")
                        for i, r in enumerate(recent)]
        compare_cls  = "compare-grid" if len(recent) == 2 else "solo-grid"
        recent_html  = f'<div class="{compare_cls}">{"".join(recent_cards)}</div>'

        # History table — everything
        history = list(reversed(runs))
        rows = []
        for r in history:
            hook_short = (r.get("hook") or "")[:75] + ("…" if len(r.get("hook") or "") > 75 else "")
            img_names  = ", ".join(r.get("selected_names") or []) or "AI-gen"
            tipo       = r.get("tipo_line") or "—"
            warns      = ", ".join(r.get("warnings") or []) or "—"
            st  = r.get("status") or "?"
            sc  = "ok" if st == "COMPLETED" else "bad"
            ts_short = (r.get("ts") or "")[:19].replace("T", " ")
            lat_s = f"{(r.get('latency_ms') or 0) / 1000:.0f}s"
            rows.append(f"""<tr>
              <td class="mono">{_e(ts_short)}</td>
              <td class="status-{sc}">{_e(st[:4])}</td>
              <td>{_e(r.get('surface') or '—')}</td>
              <td>{_e(r.get('pillar') or '—')}</td>
              <td class="hook-cell">{_e(hook_short)}</td>
              <td class="mono img-cell">{_e(img_names)}</td>
              <td class="mono">{_e(tipo)}</td>
              <td class="mono">{_e(lat_s)}</td>
              <td class="warn-cell">{_e(warns)}</td>
            </tr>""")
        history_html = f"""
<h2 class="section-h">Historial <span class="run-count">({len(runs)} runs)</span></h2>
<div class="table-wrap">
<table class="hist">
  <thead><tr>
    <th>Timestamp</th><th>Estado</th><th>Surface</th><th>Pillar</th>
    <th>Hook</th><th>Imagen</th><th>Tipo</th><th>Lat</th><th>Warnings</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
</div>"""

    recent_label = "Ultimos 2 runs" if runs and len(runs) >= 2 else "Ultimo run"

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Nubiex Dashboard</title>
<style>
:root {{
  --bg:#0b0f17; --panel:#121826; --panel-2:#0f1422;
  --text:#e6ecf5; --muted:#8b97ad; --line:#1f2738;
  --accent:#7c5cff; --a2:#00d4ff;
  --good:#2bd07b; --warn:#f5a524; --bad:#ff5d6c;
  --np:#5e204d; --ns:#9c7945; --na:#edd494; --nv:#8d3db4;
}}
* {{ box-sizing:border-box; margin:0; padding:0; }}
html,body {{ background:var(--bg); color:var(--text);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; font-size:14px; }}
.wrap {{ max-width:1600px; margin:0 auto; padding:20px 18px 60px; }}

header {{ display:flex; align-items:center; justify-content:space-between;
  padding-bottom:14px; border-bottom:2px solid var(--np); margin-bottom:18px; }}
header h1 {{ font-size:20px; }}
header h1 .brand {{ background:linear-gradient(90deg,var(--nv),var(--na));
  -webkit-background-clip:text; background-clip:text; color:transparent; }}
.hdr-meta {{ font-size:12px; color:var(--muted); text-align:right; line-height:1.6; }}
.hdr-meta b {{ color:var(--text); }}

.section-h {{ font-size:13px; letter-spacing:.04em; text-transform:uppercase;
  color:var(--muted); margin:18px 0 10px; font-weight:600; }}
.run-count {{ font-weight:400; }}
.section-lbl {{ font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--muted); margin-bottom:7px; font-weight:600; }}
.cf-lbl {{ color:var(--good); }}

/* Compare grid — 2 run cards side by side */
.compare-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:20px; }}
.solo-grid {{ margin-bottom:20px; }}
@media(max-width:1100px){{ .compare-grid {{ grid-template-columns:1fr; }} }}

/* Run card */
.run-card {{ background:var(--panel); border:1px solid var(--line);
  border-radius:14px; padding:16px 18px; }}
.run-label {{ display:inline-block; font-size:10px; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; padding:2px 8px; border-radius:999px;
  background:rgba(124,92,255,.18); color:var(--accent); border:1px solid rgba(124,92,255,.4); }}
.lc-head {{ display:flex; align-items:center; justify-content:space-between;
  flex-wrap:wrap; gap:8px; margin-bottom:5px; }}
.lc-left {{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; }}
.ts-lbl {{ font-size:11px; color:var(--muted); font-family:ui-monospace,monospace; }}
.angle-line {{ font-size:12px; color:var(--muted); margin-bottom:12px; }}

.status-ok  {{ color:var(--good); font-weight:700; }}
.status-bad {{ color:var(--bad);  font-weight:700; }}

.sf-pill,.pil-pill,.muted-pill,.lat-pill {{
  display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px;
  letter-spacing:.06em; text-transform:uppercase; }}
.sf-pill  {{ color:var(--nv); background:rgba(141,61,180,.14); border:1px solid rgba(141,61,180,.35); font-weight:700; }}
.pil-pill {{ color:var(--a2); background:rgba(0,212,255,.10); border:1px solid rgba(0,212,255,.25); }}
.muted-pill,.lat-pill {{ color:var(--muted); background:var(--panel-2); border:1px solid var(--line); }}
.lat-pill {{ font-family:ui-monospace,monospace; text-transform:none; }}
.tok-pill {{ display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px;
  color:#6ee7b7; background:rgba(110,231,183,.10); border:1px solid rgba(110,231,183,.3);
  font-family:ui-monospace,monospace; cursor:default; }}
.chip {{ display:inline-flex; padding:2px 7px; border-radius:999px;
  border:1px solid var(--line); font-size:11px; }}
.chip.warn {{ color:var(--warn); border-color:rgba(245,165,36,.4); }}

.lc-grid {{ display:grid; grid-template-columns:1.15fr 1fr; gap:12px; }}
@media(max-width:800px){{ .lc-grid {{ grid-template-columns:1fr; }} }}
.lc-left-col,.lc-right-col {{ display:flex; flex-direction:column; gap:8px; }}

.concept-card {{ background:linear-gradient(135deg,rgba(43,208,123,.06),rgba(0,212,255,.03));
  border:1.5px solid rgba(43,208,123,.35); border-radius:10px; padding:11px 13px;
  display:flex; flex-direction:column; gap:4px; }}
.ct {{ font-size:13.5px; font-weight:700;
  background:linear-gradient(90deg,var(--nv),var(--na));
  -webkit-background-clip:text; background-clip:text; color:transparent; }}
.cb {{ font-size:12.5px; line-height:1.5; color:var(--text); }}
.cm {{ display:flex; align-items:center; gap:7px; margin-top:2px; }}
.ck {{ font-size:10px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); background:var(--panel-2); padding:2px 6px;
  border-radius:6px; border:1px solid var(--line); white-space:nowrap; }}
.cv {{ font-size:12px; font-family:ui-monospace,monospace; color:var(--a2); font-weight:600; }}
.cv.tp {{ color:var(--na) !important; }}

.cap-card {{ background:var(--panel-2); border:1px solid var(--line);
  border-radius:9px; padding:8px 11px; }}
.cap-lbl {{ font-size:10px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--muted); margin-bottom:4px; display:flex; justify-content:space-between; }}
.char-n {{ font-weight:400; }}
.cap-txt {{ white-space:pre-wrap; line-height:1.5; font-size:13px; }}
.cf-raw-txt {{ font-size:12px; }}
.hook-cap {{ border-color:rgba(237,212,148,.25); }}
.hook-txt {{ font-weight:700; font-size:13.5px; }}
.cta-cap .cta-txt {{ color:var(--na); font-weight:600; }}
.carousel-raw {{ border-color:rgba(0,212,255,.2); }}
.tags-row {{ line-height:2.2; }}
code.tag {{ background:var(--panel-2); border:1px solid rgba(141,61,180,.3);
  padding:2px 7px; border-radius:999px; font-size:11.5px; color:var(--na);
  margin-right:3px; font-family:inherit; }}

.img-strip {{ display:grid; grid-template-columns:repeat(2,1fr); gap:7px; }}
.ti {{ margin:0; background:var(--panel-2); border:2px solid var(--line);
  border-radius:9px; overflow:hidden; position:relative; }}
.ti.use {{ border-color:var(--good); box-shadow:0 0 0 3px rgba(43,208,123,.15); }}
.ti img {{ width:100%; height:90px; object-fit:cover; display:block; }}
.ti figcaption {{ padding:3px 5px; font-size:10px; color:var(--muted); }}
.bd {{ position:absolute; top:4px; left:4px; font-size:10px; font-weight:700;
  padding:1px 6px; border-radius:999px; letter-spacing:.06em; text-transform:uppercase; }}
.bd-use {{ background:var(--good); color:#0b3b22; }}
.bd-no  {{ background:rgba(139,151,173,.5); color:var(--text); }}

.genprompt {{ background:var(--panel-2); border:1px solid var(--line); border-radius:9px;
  padding:9px 11px; font-size:11px; font-family:ui-monospace,monospace;
  color:var(--a2); line-height:1.5; white-space:pre-wrap; }}

/* Brand DNA collapsible */
.dna-details {{ margin-top:10px; }}
.dna-summary {{ font-size:11px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); cursor:pointer; padding:6px 0; user-select:none; }}
.dna-summary:hover {{ color:var(--text); }}
.dna-body {{ margin-top:6px; }}
.dna-pre {{ background:var(--panel-2); border:1px solid var(--line); border-radius:9px;
  padding:10px 13px; font-size:11.5px; font-family:ui-monospace,monospace;
  color:var(--muted); line-height:1.6; white-space:pre-wrap; overflow-x:auto; }}

/* CF JSON copy panel */
.cf-details {{ margin-top:8px; }}
.cf-summary {{ font-size:11px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); cursor:pointer; padding:6px 0; user-select:none;
  display:flex; align-items:center; gap:10px; }}
.cf-summary:hover {{ color:var(--text); }}
.copy-btn {{ font-size:11px; padding:3px 10px; border-radius:6px; cursor:pointer;
  background:rgba(43,208,123,.15); border:1px solid rgba(43,208,123,.4);
  color:var(--good); font-family:inherit; transition:background .15s; }}
.copy-btn:hover {{ background:rgba(43,208,123,.28); }}
.cf-json-wrap {{ margin-top:6px; }}
.cf-json-ta {{ width:100%; height:160px; background:var(--panel-2);
  border:1px solid var(--line); border-radius:9px; padding:10px 13px;
  font-size:11.5px; font-family:ui-monospace,monospace; color:var(--a2);
  line-height:1.5; resize:vertical; }}

.table-wrap {{ overflow-x:auto; }}
table.hist {{ width:100%; border-collapse:collapse; font-size:12px;
  background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
table.hist th, table.hist td {{ padding:7px 10px; text-align:left;
  border-bottom:1px solid var(--line); vertical-align:top; }}
table.hist thead th {{ background:var(--panel-2); color:var(--muted);
  font-size:11px; letter-spacing:.05em; text-transform:uppercase; font-weight:600; }}
table.hist tr:last-child td {{ border-bottom:none; }}
.hook-cell {{ max-width:260px; color:var(--text); }}
.img-cell  {{ max-width:150px; color:var(--a2); font-size:11px; word-break:break-all; }}
.warn-cell {{ max-width:130px; color:var(--warn); font-size:11px; }}
.mono {{ font-family:ui-monospace,monospace; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1><span class="brand">Nubiex</span> Quick Runs</h1>
  <div class="hdr-meta">
    <b>{len(runs)} runs</b> · nubiex_post.json<br>
    Reload to refresh
  </div>
</header>

<h2 class="section-h">{recent_label}</h2>
{recent_html}
{history_html}

</div>
<script>
function copyJson(cardId) {{
  var ta = document.getElementById('json-' + cardId);
  if (!ta) return;
  var text = ta.value;
  var btn = document.querySelector('#' + cardId + ' .copy-btn');
  function markDone() {{
    if (btn) {{ btn.textContent = 'Copiado!'; setTimeout(function(){{ btn.textContent = 'Copiar'; }}, 1500); }}
  }}
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(markDone).catch(function() {{ fallbackCopy(text, markDone); }});
  }} else {{
    fallbackCopy(text, markDone);
  }}
}}
function fallbackCopy(text, cb) {{
  var tmp = document.createElement('textarea');
  tmp.value = text;
  tmp.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
  document.body.appendChild(tmp);
  tmp.focus();
  tmp.select();
  try {{ document.execCommand('copy'); cb(); }} catch(e) {{}}
  document.body.removeChild(tmp);
}}
</script>
</body>
</html>"""


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Quick single-run Nubiex tester")
    parser.add_argument("description", nargs="?", default=None,
                        help="Custom request description (overrides scenario default)")
    parser.add_argument("--scenario", "-s", type=int, default=1,
                        help="Scenario ID 1-10 for default description (default: 1)")
    args = parser.parse_args()

    if not os.environ.get("MARKETER_RUN_LIVE"):
        print("ERROR: set MARKETER_RUN_LIVE=1", file=sys.stderr)
        sys.exit(2)

    settings = load_settings()
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    description = args.description or SCENARIO_DESCS.get(args.scenario, SCENARIO_DESCS[1])
    scenario_id = args.scenario

    client = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    print(f"Running scenario {scenario_id}...")
    print(f"  > {description[:80]}...")
    t0 = time.time()

    result = do_run(description, scenario_id, client, settings.extras_list_truncation, settings.llm_max_output_tokens)
    elapsed = time.time() - t0

    status = result.get("status") or "FAILED"
    sf     = result.get("surface") or "—"
    img    = ", ".join(result.get("selected_names") or []) or "AI-gen"
    warns  = result.get("warnings") or []
    print(f"  {status} | surface={sf} | imgs={img} | lat={result.get('latency_ms',0)}ms | warns={warns} | {elapsed:.1f}s total")

    # Load existing runs
    existing: list[dict] = []
    if RUNS_LOG.exists():
        try:
            existing = json.loads(RUNS_LOG.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    existing.append(result)
    RUNS_LOG.write_text(json.dumps(existing, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Rebuild dashboard
    html_content = build_dashboard(existing)
    DASHBOARD_PATH.write_text(html_content, encoding="utf-8")
    print(f"  Dashboard: {DASHBOARD_PATH}")

    # Open in browser
    try:
        if sys.platform == "win32":
            os.startfile(str(DASHBOARD_PATH))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(DASHBOARD_PATH)])
        else:
            subprocess.run(["xdg-open", str(DASHBOARD_PATH)])
    except Exception:
        pass


if __name__ == "__main__":
    main()
