#!/usr/bin/env python3
"""Generate docs/examples/runs/marketer_demo_v2.html — multi-fixture comparison view.

Runs 6 fixtures through reason() in parallel, then renders a single HTML
with tab navigation, a cross-vertical comparison table, and a per-fixture
detail panel that surfaces the new brand_dna + brand_intelligence layers.

Usage:
  MARKETER_RUN_LIVE=1 PYTHONPATH=src python scripts/demo/build_multi_demo_html.py
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from marketer.config import load_settings  # noqa: E402
from marketer.llm.gemini import GeminiClient  # noqa: E402
from marketer.reasoner import reason  # noqa: E402


FIXTURES: list[tuple[str, str, str]] = [
    ("casa_maruja_post", "Casa Maruja", "Restaurante local · Ruzafa"),
    ("saas_b2b_post", "Pulsemetrics", "SaaS B2B analytics"),
    ("retail_ecom_post", "Verdea Studio", "E-commerce moda sostenible"),
    ("dentist_post", "Clínica Dental", "Salud · Barcelona"),
    ("minimal_post", "Minimal SL", "Brief mínimo"),
    ("missing_brief_post", "No Brief Brand", "Sin brief (gate failed)"),
]


def _run_fixture(fixture: str, label: str, subtitle: str) -> dict:
    envelope = json.loads(
        (ROOT / "tests" / "fixtures" / "envelopes" / f"{fixture}.json").read_text(encoding="utf-8")
    )
    settings = load_settings()
    client = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    t0 = time.time()
    try:
        callback = reason(envelope, gemini=client, extras_truncation=settings.extras_list_truncation)
        callback_body = callback.model_dump(mode="json")
        error = None
    except Exception as exc:  # noqa: BLE001
        callback_body = {"status": "FAILED", "output_data": None, "error_message": str(exc)}
        error = str(exc)
    wall_ms = int((time.time() - t0) * 1000)
    print(f"  [{fixture}] status={callback_body.get('status')} wall={wall_ms}ms")
    return {
        "fixture": fixture,
        "label": label,
        "subtitle": subtitle,
        "envelope": envelope,
        "callback_body": callback_body,
        "wall_ms": wall_ms,
        "error": error,
    }


def _esc(text: object) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _conf_class(level: str | None) -> str:
    lvl = (level or "medium").lower()
    return lvl if lvl in {"high", "medium", "low"} else "medium"


def _decision_block(label: str, choice: dict | None) -> str:
    if not choice:
        return ""
    chosen = choice.get("chosen", "")
    rationale = choice.get("rationale", "")
    alts = choice.get("alternatives_considered") or []
    alts_html = "".join(f'<span class="alt">{_esc(a)}</span>' for a in alts)
    alts_block = f'<div class="alts">{alts_html}</div>' if alts_html else ""
    return (
        '<div class="decision">'
        f'  <div class="label">{_esc(label)}</div>'
        f'  <div class="chosen">{_esc(chosen)}</div>'
        f'  {alts_block}'
        f'  <div class="why">{_esc(rationale)}</div>'
        '</div>'
    )


def _cta_card(cta: dict | None) -> str:
    if not cta:
        return '<div class="cta-card"><span class="ch">NONE</span></div>'
    channel = (cta.get("channel") or "none").upper()
    label = cta.get("label") or ""
    url = cta.get("url_or_handle") or ""
    url_html = f'<span class="url">→ {_esc(url)}</span>' if url else ""
    return (
        '<div class="cta-card">'
        f'  <span class="ch">{_esc(channel)}</span>'
        f'  <span class="lbl">{_esc(label)}</span>'
        f'  {url_html}'
        '</div>'
    )


def _brand_intelligence_grid(bi: dict | None) -> str:
    if not bi:
        return '<div class="bi-empty">No brand_intelligence produced.</div>'
    rows = [
        ("Business taxonomy", bi.get("business_taxonomy"), "taxo"),
        ("Funnel stage", bi.get("funnel_stage_target"), "funnel"),
        ("Voice register", bi.get("voice_register"), "voice"),
        ("Emotional beat", bi.get("emotional_beat"), "emo"),
        ("Audience persona", bi.get("audience_persona"), "persona"),
        ("Unfair advantage", bi.get("unfair_advantage"), "edge"),
        ("Rhetorical device", bi.get("rhetorical_device"), "rhet"),
    ]
    risk = bi.get("risk_flags") or []
    risk_pills = "".join(f'<span class="chip warn">{_esc(r)}</span>' for r in risk) or '<span class="chip ghost">none</span>'
    html = ['<div class="bi-grid">']
    for title, value, _kind in rows:
        html.append(
            f'<div class="bi-row"><div class="bi-label">{_esc(title)}</div>'
            f'<div class="bi-value">{_esc(value or "—")}</div></div>'
        )
    html.append(
        '<div class="bi-row"><div class="bi-label">Risk flags</div>'
        f'<div class="bi-value">{risk_pills}</div></div>'
    )
    html.append('</div>')
    return "".join(html)


def _warnings_chips(warnings: list[dict]) -> str:
    if not warnings:
        return '<span class="chip good">no warnings</span>'
    bad = {"claim_not_in_brief", "palette_mismatch", "cta_channel_invalid", "cta_url_invalid",
           "visual_hallucinated", "field_missing", "prior_post_missing",
           "cta_caption_channel_mismatch"}
    warn = {"brief_missing", "gallery_empty", "gallery_all_filtered",
            "price_not_in_brief", "caption_length_exceeded", "do_not_truncated",
            "surface_format_overridden", "tone_unclear", "value_proposition_empty",
            "gallery_partially_filtered", "gallery_truncated",
            "context_missing_id", "brief_request_mismatch", "request_vague",
            "schema_repair_used"}
    pills = []
    for w in warnings:
        code = (w or {}).get("code", "")
        msg = _esc((w or {}).get("message", ""))
        cls = "chip bad" if code in bad else ("chip warn" if code in warn else "chip")
        pills.append(f'<span class="{cls}" title="{msg}">{_esc(code)}</span>')
    return "".join(pills)


def _render_panel(result: dict, idx: int) -> str:
    """Render a single fixture's detail panel."""
    body = result["callback_body"]
    output = body.get("output_data") or {}
    enrich = output.get("enrichment") or {}
    trace = output.get("trace") or {}
    warnings = output.get("warnings") or []

    status = body.get("status", "UNKNOWN")
    error_msg = body.get("error_message")

    caption = enrich.get("caption") or {}
    image = enrich.get("image") or {}
    cta = enrich.get("cta") or {}
    hashtag = enrich.get("hashtag_strategy") or {}
    conf = enrich.get("confidence") or {}
    decisions = enrich.get("strategic_decisions") or {}
    vs = enrich.get("visual_selection") or {}
    bi = enrich.get("brand_intelligence")

    if status != "COMPLETED":
        return (
            f'<div class="tab-panel" id="panel-{idx}">'
            f'  <div class="fail-box">'
            f'    <h2>Run failed · {_esc(result["label"])}</h2>'
            f'    <p class="error-text">{_esc(error_msg or "no error_message")}</p>'
            f'  </div>'
            '</div>'
        )

    use_urls = vs.get("recommended_asset_urls") or []
    avoid_urls = vs.get("avoid_asset_urls") or []
    ref_urls = vs.get("recommended_reference_urls") or []

    decisions_html = (
        _decision_block("Surface format", decisions.get("surface_format"))
        + _decision_block("Angle", decisions.get("angle"))
        + _decision_block("Voice", decisions.get("voice"))
    )

    do_not = enrich.get("do_not") or []
    do_not_html = (
        "".join(f'<span class="chip ghost">{_esc(x)}</span>' for x in do_not)
        or '<span class="chip ghost">—</span>'
    )

    themes = hashtag.get("themes") or []
    themes_html = (
        "".join(f'<span class="chip">#{_esc(t)}</span>' for t in themes)
        or '<span class="chip ghost">no themes</span>'
    )

    visual_selection_html = ""
    if use_urls or avoid_urls or ref_urls:
        rows = []
        for u in use_urls:
            rows.append(f'<div class="thumb use"><span class="role" style="background:rgba(43,208,123,0.18);color:var(--good)">use</span><div class="t">{_esc(u)}</div></div>')
        for u in ref_urls:
            rows.append(f'<div class="thumb reference"><span class="role" style="background:rgba(0,212,255,0.18);color:var(--accent-2)">reference</span><div class="t">{_esc(u)}</div></div>')
        for u in avoid_urls:
            rows.append(f'<div class="thumb avoid"><span class="role" style="background:rgba(255,93,108,0.18);color:var(--bad)">avoid</span><div class="t">{_esc(u)}</div></div>')
        visual_selection_html = f'<div class="img-row">{"".join(rows)}</div>'
    else:
        visual_selection_html = '<div class="bi-empty">No gallery recommendations.</div>'

    gallery_stats = trace.get("gallery_stats") or {}
    latency = trace.get("latency_ms", 0)

    return f"""
<div class="tab-panel" id="panel-{idx}">
  <div class="grid-3">
    <div class="card"><h3>Status</h3><div class="v">{_esc(status)} <small>{_esc(trace.get("surface", ""))} / {_esc(trace.get("mode", ""))}</small></div></div>
    <div class="card"><h3>Latency</h3><div class="v">{latency} ms <small>repair: {"yes" if trace.get("repair_attempted") else "no"}</small></div></div>
    <div class="card"><h3>Gallery</h3><div class="v">{gallery_stats.get("accepted_count", 0)} / {gallery_stats.get("raw_count", 0)} <small>degraded: {"yes" if trace.get("degraded") else "no"}</small></div></div>
  </div>

  <!-- POST CARD -->
  <div class="post-card">
    <div class="post-head">
      <span class="surface-pill">{_esc((enrich.get("surface_format") or "post").upper())}</span>
      <span class="pillar-pill">{_esc((enrich.get("content_pillar") or "—").replace("_", " ").upper())}</span>
      <h3 class="post-title">{_esc(enrich.get("title", "—"))}</h3>
    </div>
    <div class="post-grid">
      <div class="post-col">
        <div class="post-section">
          <h4>🎯 Objective</h4>
          <p>{_esc(enrich.get("objective", ""))}</p>
        </div>
        <div class="post-section">
          <h4>⚔️ Strategic decisions</h4>
          {decisions_html or '<p class="lead">No structured decisions.</p>'}
        </div>
        <div class="post-section">
          <h4>✨ Visual style notes</h4>
          <p>{_esc(enrich.get("visual_style_notes", ""))}</p>
        </div>
        <div class="post-section">
          <h4>🚫 Do not</h4>
          <div class="pill-row">{do_not_html}</div>
        </div>
      </div>
      <div class="post-col">
        <div class="post-section">
          <h4>🖼️ Image brief</h4>
          <div class="img-brief"><div class="lbl">Concept</div><div class="body">{_esc(image.get("concept", ""))}</div></div>
          <div class="img-brief gen"><div class="lbl">Generation prompt</div><div class="body">{_esc(image.get("generation_prompt", ""))}</div></div>
          <div class="img-brief alt"><div class="lbl">Alt text</div><div class="body">{_esc(image.get("alt_text", ""))}</div></div>
        </div>
        <div class="post-section">
          <h4>📝 Caption</h4>
          <div class="cap hook"><div class="lbl"><span>Hook</span><span class="len">{len(caption.get("hook", ""))} ch</span></div><div class="txt">{_esc(caption.get("hook", ""))}</div></div>
          <div class="cap body"><div class="lbl"><span>Body</span><span class="len">{len(caption.get("body", ""))} ch</span></div><div class="txt">{_esc(caption.get("body", ""))}</div></div>
          <div class="cap cta"><div class="lbl"><span>CTA line</span><span class="len">{len(caption.get("cta_line", ""))} ch</span></div><div class="txt">{_esc(caption.get("cta_line", ""))}</div></div>
        </div>
        <div class="post-section">
          <h4>👉 Call to action</h4>
          {_cta_card(cta)}
        </div>
        <div class="post-section">
          <h4>🏷️ Hashtag strategy</h4>
          <div class="pill-row"><span class="chip">intent · {_esc(hashtag.get("intent", "—"))}</span><span class="chip">volume · {hashtag.get("suggested_volume", 0)}</span></div>
          <div class="pill-row">{themes_html}</div>
        </div>
      </div>
    </div>
  </div>

  <!-- BRAND DNA (NEW · public) -->
  <div class="card-block">
    <div class="card-block-head"><h3>🧬 Brand DNA</h3><span class="tag-public">public · viaja a Content Factory</span></div>
    <p class="lead">Narrativa destilada del brief. El copywriter de CONTENT_FACTORY la lee antes de redactar.</p>
    <pre class="dna">{_esc(enrich.get("brand_dna", "—"))}</pre>
  </div>

  <!-- BRAND INTELLIGENCE (NEW · internal) -->
  <div class="card-block">
    <div class="card-block-head"><h3>🧠 Brand Intelligence</h3><span class="tag-internal">internal · solo subagentes</span></div>
    <p class="lead">Razonamiento interno del agente. Nunca aparece en el post; informa a specialists downstream.</p>
    {_brand_intelligence_grid(bi)}
  </div>

  <div class="split-2">
    <div class="card-block">
      <h3>Visual selection</h3>
      <div class="pill-row" style="margin:6px 0 10px;">
        <span class="chip good">use · {len(use_urls)}</span>
        <span class="chip">reference · {len(ref_urls)}</span>
        <span class="chip bad">avoid · {len(avoid_urls)}</span>
      </div>
      {visual_selection_html}
    </div>
    <div class="card-block">
      <h3>Confidence</h3>
      <div class="conf-row">
        <div class="conf"><b>surface_format</b><span class="lvl {_conf_class(conf.get("surface_format"))}">{_esc(conf.get("surface_format", "medium"))}</span></div>
        <div class="conf"><b>angle</b><span class="lvl {_conf_class(conf.get("angle"))}">{_esc(conf.get("angle", "medium"))}</span></div>
        <div class="conf"><b>palette_match</b><span class="lvl {_conf_class(conf.get("palette_match"))}">{_esc(conf.get("palette_match", "medium"))}</span></div>
        <div class="conf"><b>cta_channel</b><span class="lvl {_conf_class(conf.get("cta_channel"))}">{_esc(conf.get("cta_channel", "medium"))}</span></div>
      </div>
    </div>
  </div>

  <div class="card-block">
    <h3>Warnings</h3>
    <div class="pill-row">{_warnings_chips(warnings)}</div>
  </div>

  <details class="acc">
    <summary>Raw input envelope (ROUTER → MARKETER)</summary>
    <pre class="json">{_esc(json.dumps(result["envelope"], ensure_ascii=False, indent=2))}</pre>
  </details>
  <details class="acc">
    <summary>Raw output callback (MARKETER → ROUTER)</summary>
    <pre class="json">{_esc(json.dumps(body, ensure_ascii=False, indent=2))}</pre>
  </details>
</div>
"""


def _comparison_table(results: list[dict]) -> str:
    """Cross-fixture quick-glance table."""
    rows = []
    for r in results:
        body = r["callback_body"]
        enrich = (body.get("output_data") or {}).get("enrichment") or {}
        bi = enrich.get("brand_intelligence") or {}
        warnings = ((body.get("output_data") or {}).get("warnings") or [])
        warn_count = len(warnings)
        rows.append({
            "label": r["label"],
            "subtitle": r["subtitle"],
            "status": body.get("status", "?"),
            "latency": r["wall_ms"],
            "pillar": enrich.get("content_pillar", "—"),
            "channel": (enrich.get("cta") or {}).get("channel", "—"),
            "funnel": bi.get("funnel_stage_target", "—"),
            "taxo": bi.get("business_taxonomy", "—"),
            "register": bi.get("voice_register", "—"),
            "beat": bi.get("emotional_beat", "—"),
            "warn": warn_count,
        })
    trs = []
    for r in rows:
        status_cls = "status-ok" if r["status"] == "COMPLETED" else "status-bad"
        trs.append(
            f'<tr><td><b>{_esc(r["label"])}</b><br><small>{_esc(r["subtitle"])}</small></td>'
            f'<td class="{status_cls}">{_esc(r["status"])}</td>'
            f'<td>{r["latency"]} ms</td>'
            f'<td>{_esc(r["pillar"])}</td>'
            f'<td>{_esc(r["channel"])}</td>'
            f'<td>{_esc(r["funnel"])}</td>'
            f'<td><code>{_esc(r["taxo"])}</code></td>'
            f'<td>{_esc(r["register"])}</td>'
            f'<td>{_esc(r["beat"])}</td>'
            f'<td>{r["warn"]}</td></tr>'
        )
    return f"""
<table class="cmp">
<thead><tr>
  <th>Vertical</th><th>Status</th><th>Latency</th>
  <th>Pillar</th><th>CTA channel</th><th>Funnel</th>
  <th>Taxonomy</th><th>Voice register</th><th>Emotional beat</th><th>Warnings</th>
</tr></thead>
<tbody>{"".join(trs)}</tbody>
</table>
"""


def _build_html(results: list[dict]) -> str:
    tabs_nav = "".join(
        f'<button class="tab-btn{" active" if i == 0 else ""}" data-tab="{i}">'
        f'<b>{_esc(r["label"])}</b><small>{_esc(r["subtitle"])}</small></button>'
        for i, r in enumerate(results)
    )
    panels = "\n".join(_render_panel(r, i) for i, r in enumerate(results))
    cmp_table = _comparison_table(results)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>MARKETER — Multi-vertical demo v2</title>
<style>
  :root {{
    --bg: #0b0f17;
    --panel: #121826;
    --panel-2: #0f1422;
    --text: #e6ecf5;
    --muted: #8b97ad;
    --line: #1f2738;
    --accent: #7c5cff;
    --accent-2: #00d4ff;
    --good: #2bd07b;
    --warn: #f5a524;
    --bad: #ff5d6c;
    --code-bg: #0a0e17;
    --key: #7ee787;
    --string: #a5d6ff;
  }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --bg: #f6f7fb; --panel: #ffffff; --panel-2: #fafbff;
      --text: #1a2030; --muted: #5b6577; --line: #e6e8ef;
      --code-bg: #f1f3f9; --key: #166534; --string: #0f6cbf;
    }}
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text);
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Inter, Roboto, sans-serif; }}
  .wrap {{ max-width: 1280px; margin: 0 auto; padding: 28px 22px 80px; }}

  header.hero {{
    display: grid; grid-template-columns: 1fr auto; gap: 20px; align-items: end;
    padding-bottom: 22px; border-bottom: 1px solid var(--line); margin-bottom: 22px;
  }}
  .hero h1 {{ margin: 0; font-size: 26px; letter-spacing: -0.01em; }}
  .hero h1 .grad {{
    background: linear-gradient(90deg, var(--accent), var(--accent-2));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  .hero p {{ margin: 6px 0 0; color: var(--muted); max-width: 820px; line-height: 1.5; }}
  .meta {{ color: var(--muted); font-size: 12.5px; text-align: right; line-height: 1.6; }}
  .meta b {{ color: var(--text); font-weight: 600; }}

  /* Comparison table */
  table.cmp {{ width: 100%; border-collapse: collapse; margin: 14px 0 24px;
    font-size: 13px; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }}
  table.cmp th, table.cmp td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }}
  table.cmp thead th {{ background: var(--panel-2); color: var(--muted); font-weight: 600;
    font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; }}
  table.cmp tr:last-child td {{ border-bottom: none; }}
  table.cmp small {{ color: var(--muted); display:block; margin-top: 2px; }}
  table.cmp code {{ font-size: 12px; color: var(--accent-2); }}
  .status-ok {{ color: var(--good); font-weight: 600; }}
  .status-bad {{ color: var(--bad); font-weight: 600; }}

  /* Tabs */
  .tabs-nav {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 16px 0 20px;
    padding-bottom: 10px; border-bottom: 1px solid var(--line); }}
  .tab-btn {{ font: inherit; cursor: pointer; background: transparent; color: var(--muted);
    border: 1px solid var(--line); padding: 8px 14px; border-radius: 10px;
    display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
    transition: all 0.15s; min-width: 140px; }}
  .tab-btn:hover {{ color: var(--text); border-color: var(--accent); }}
  .tab-btn b {{ font-size: 13.5px; font-weight: 600; }}
  .tab-btn small {{ font-size: 11px; color: var(--muted); }}
  .tab-btn.active {{ background: linear-gradient(135deg, rgba(124,92,255,0.15), rgba(0,212,255,0.12));
    color: var(--text); border-color: var(--accent); }}
  .tab-btn.active small {{ color: var(--text); opacity: 0.75; }}

  .tab-panel {{ display: none; }}
  .tab-panel.active {{ display: block; }}

  /* Cards */
  .grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 4px 0 20px; }}
  @media (max-width: 900px) {{ .grid-3 {{ grid-template-columns: 1fr; }} }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }}
  .card h3 {{ margin: 0 0 6px; font-size: 13px; letter-spacing: 0.03em; text-transform: uppercase; color: var(--muted); }}
  .card .v {{ font-size: 17px; font-weight: 600; }}
  .card .v small {{ color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 6px; }}

  .card-block {{ background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
    padding: 16px 18px; margin: 14px 0; }}
  .card-block h3 {{ margin: 0 0 4px; font-size: 16px; }}
  .card-block-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
  .tag-public {{ font-size: 11px; background: rgba(43,208,123,0.15); color: var(--good);
    padding: 3px 10px; border-radius: 999px; border: 1px solid rgba(43,208,123,0.35); }}
  .tag-internal {{ font-size: 11px; background: rgba(124,92,255,0.12); color: var(--accent);
    padding: 3px 10px; border-radius: 999px; border: 1px solid rgba(124,92,255,0.35); }}
  .lead {{ color: var(--muted); margin: 0 0 12px; line-height: 1.5; font-size: 13.5px; }}

  .split-2 {{ display: grid; grid-template-columns: 1.5fr 1fr; gap: 14px; }}
  @media (max-width: 900px) {{ .split-2 {{ grid-template-columns: 1fr; }} }}

  /* Post card */
  .post-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; margin-bottom: 14px; }}
  .post-head {{ padding: 18px 22px 12px; border-bottom: 1px solid var(--line); display:flex; flex-wrap:wrap; align-items:center; gap:10px; }}
  .surface-pill, .pillar-pill {{ display: inline-block; font-size: 11px; letter-spacing: 0.10em;
    text-transform: uppercase; padding: 4px 10px; border-radius: 999px; }}
  .surface-pill {{ color: var(--accent); background: rgba(124,92,255,0.12); border: 1px solid rgba(124,92,255,0.35); }}
  .pillar-pill {{ color: var(--accent-2); background: rgba(0,212,255,0.10); border: 1px solid rgba(0,212,255,0.30); }}
  .post-title {{ width:100%; margin: 8px 0 0; font-size: 22px; line-height: 1.25; font-weight: 700; }}
  .post-grid {{ display: grid; grid-template-columns: 1.2fr 1fr; gap: 0; }}
  @media (max-width: 980px) {{ .post-grid {{ grid-template-columns: 1fr; }} }}
  .post-col {{ padding: 18px 22px; }}
  .post-col + .post-col {{ border-left: 1px solid var(--line); }}
  @media (max-width: 980px) {{ .post-col + .post-col {{ border-left: none; border-top: 1px solid var(--line); }} }}
  .post-section {{ margin: 0 0 16px; }}
  .post-section:last-child {{ margin-bottom: 0; }}
  .post-section h4 {{ margin: 0 0 6px; font-size: 12px; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--muted); }}
  .post-section p {{ margin: 0; line-height: 1.55; font-size: 14px; }}

  /* Decisions */
  .decision {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px;
    padding: 10px 12px; margin-bottom: 8px; }}
  .decision .label {{ font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }}
  .decision .chosen {{ margin-top: 3px; font-weight: 600; font-size: 14px; }}
  .decision .alts {{ margin-top: 5px; display: flex; flex-wrap: wrap; gap: 5px; }}
  .decision .alt {{ font-size: 11px; padding: 2px 7px; border-radius: 999px; border: 1px dashed var(--line); color: var(--muted); text-decoration: line-through; }}
  .decision .why {{ margin-top: 6px; font-size: 12.5px; color: var(--text); line-height: 1.5; opacity: 0.9; }}
  .decision .why::before {{ content: "↳ "; color: var(--muted); }}

  /* Caption */
  .cap {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; }}
  .cap .lbl {{ font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); display:flex; justify-content:space-between; }}
  .cap .txt {{ margin-top: 5px; white-space: pre-wrap; line-height: 1.5; font-size: 13.5px; }}
  .cap.hook .txt {{ font-weight: 600; }}

  /* Image brief */
  .img-brief {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; }}
  .img-brief .lbl {{ font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 4px; }}
  .img-brief .body {{ line-height: 1.5; font-size: 13px; }}
  .img-brief.gen .body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--accent-2); }}
  .img-brief.alt .body {{ color: var(--muted); font-style: italic; font-size: 12.5px; }}

  /* CTA */
  .cta-card {{ display:flex; align-items:center; gap:10px; padding: 10px 12px;
    background: linear-gradient(90deg, rgba(124,92,255,0.12), rgba(0,212,255,0.10));
    border: 1px solid rgba(124,92,255,0.30); border-radius: 10px; }}
  .cta-card .ch {{ background: linear-gradient(90deg, var(--accent), var(--accent-2));
    color: white; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 3px 10px; border-radius: 999px; font-weight: 700; }}
  .cta-card .lbl {{ font-weight: 600; }}
  .cta-card .url {{ color: var(--muted); font-size: 12px; word-break: break-all; }}

  /* Brand DNA */
  pre.dna {{ background: var(--code-bg); color: var(--text); padding: 14px 16px;
    border-radius: 10px; border: 1px solid var(--line); white-space: pre-wrap;
    font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13.5px;
    line-height: 1.55; max-height: 600px; overflow: auto; margin: 8px 0 0; }}

  /* Brand intelligence grid */
  .bi-grid {{ display: grid; grid-template-columns: 180px 1fr; gap: 0; background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; margin-top: 8px; }}
  .bi-row {{ display: contents; }}
  .bi-row > div {{ padding: 9px 14px; border-bottom: 1px solid var(--line); }}
  .bi-row:last-child > div {{ border-bottom: none; }}
  .bi-label {{ color: var(--muted); font-size: 12px; letter-spacing: 0.05em; text-transform: uppercase;
    background: rgba(0,0,0,0.10); }}
  .bi-value {{ font-size: 13.5px; line-height: 1.45; }}
  .bi-empty {{ color: var(--muted); font-style: italic; padding: 12px; }}

  /* Chips */
  .pill-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }}
  .chip {{ display: inline-flex; align-items: center; gap: 5px; padding: 5px 9px; border-radius: 999px;
    border: 1px solid var(--line); background: var(--panel-2); font-size: 12px; color: var(--text); }}
  .chip.good {{ color: var(--good); border-color: rgba(43,208,123,0.4); }}
  .chip.bad  {{ color: var(--bad);  border-color: rgba(255,93,108,0.4); }}
  .chip.warn {{ color: var(--warn); border-color: rgba(245,165,36,0.4); }}
  .chip.ghost {{ color: var(--muted); border-style: dashed; }}

  /* Confidence */
  .conf-row {{ display:grid; grid-template-columns: 1fr; gap: 6px; }}
  .conf {{ display:flex; align-items:center; justify-content:space-between;
    background: var(--panel-2); border:1px solid var(--line); border-radius:8px; padding:6px 10px; font-size:12.5px; }}
  .conf .lvl {{ font-size:11px; padding:2px 8px; border-radius:999px; text-transform:uppercase; letter-spacing:.06em; }}
  .lvl.high   {{ background: rgba(43,208,123,0.15); color: var(--good); }}
  .lvl.medium {{ background: rgba(245,165,36,0.15); color: var(--warn); }}
  .lvl.low    {{ background: rgba(255,93,108,0.15); color: var(--bad); }}

  /* Visual selection */
  .img-row {{ display: grid; grid-template-columns: 1fr; gap: 6px; margin-top: 8px; }}
  .thumb {{ background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; display: flex; align-items: center; gap: 8px; }}
  .thumb.use {{ border-left: 3px solid var(--good); }}
  .thumb.avoid {{ opacity: 0.6; border-left: 3px dashed var(--bad); }}
  .thumb.reference {{ border-left: 3px solid var(--accent-2); }}
  .thumb .role {{ font-size: 10px; padding: 2px 6px; border-radius: 999px; background: rgba(124,92,255,0.15); color: var(--accent); }}
  .thumb .t {{ font-size: 11.5px; color: var(--muted); word-break: break-all; line-height: 1.35; flex: 1; }}

  details.acc {{ margin-top: 10px; }}
  details.acc > summary {{ cursor: pointer; color: var(--muted); font-size: 13px; padding: 8px 12px;
    border: 1px dashed var(--line); border-radius: 8px; }}
  details.acc[open] > summary {{ color: var(--text); border-style: solid; }}
  pre.json {{ margin: 0; padding: 14px 16px; background: var(--code-bg); color: var(--text);
    overflow: auto; max-height: 480px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px; line-height: 1.5; border: 1px solid var(--line); border-radius: 0 0 8px 8px; border-top: none; }}

  .fail-box {{ background: rgba(255,93,108,0.08); border: 1px solid rgba(255,93,108,0.35);
    border-radius: 12px; padding: 22px 28px; margin: 14px 0; }}
  .fail-box h2 {{ margin: 0 0 8px; color: var(--bad); }}
  .error-text {{ font-family: ui-monospace, monospace; font-size: 13px; color: var(--muted); white-space: pre-wrap; }}

  footer {{ color: var(--muted); font-size: 12px; margin-top: 26px; text-align: center; }}
</style>
</head>
<body>
<div class="wrap">

  <header class="hero">
    <div>
      <h1>MARKETER <span class="grad">multi-vertical demo v2</span></h1>
      <p>6 verticales reales corridos contra Gemini 3 Flash. Cada tab muestra un post enrichment completo con
        las dos nuevas capas: <b>brand_dna</b> (narrativa pública que viaja a CONTENT_FACTORY) y
        <b>brand_intelligence</b> (razonamiento interno, solo para subagentes).</p>
    </div>
    <div class="meta">
      <div><b>fixtures</b> {len(results)}</div>
      <div><b>model</b> gemini-3-flash-preview</div>
      <div><b>generated</b> {_esc(generated)}</div>
    </div>
  </header>

  <h2 style="margin:0 0 8px; font-size:16px; letter-spacing:.02em;">Comparación cross-vertical</h2>
  {cmp_table}

  <div class="tabs-nav">{tabs_nav}</div>

  {panels}

  <footer>Generated with scripts/demo/build_multi_demo_html.py · one call per fixture through <code>reason()</code></footer>
</div>

<script>
  const btns = document.querySelectorAll('.tab-btn');
  const panels = document.querySelectorAll('.tab-panel');
  // Initialize: show first panel
  if (panels[0]) panels[0].classList.add('active');
  btns.forEach((b) => {{
    b.addEventListener('click', () => {{
      const idx = b.getAttribute('data-tab');
      btns.forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      panels.forEach((p, i) => {{
        p.classList.toggle('active', String(i) === idx);
      }});
      window.scrollTo({{ top: 0, behavior: 'smooth' }});
    }});
  }});
</script>
</body>
</html>
"""


def main() -> None:
    print(f"Running {len(FIXTURES)} fixtures in parallel (max_workers=3)...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_run_fixture, *args) for args in FIXTURES]
        results = [f.result() for f in futures]
    print(f"\nBuilding HTML with {len(results)} panels...")
    html = _build_html(results)
    out = ROOT / "docs" / "examples" / "runs" / "marketer_demo_v2.html"
    out.write_text(html, encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    print(f"Wrote {out} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
