#!/usr/bin/env python3
"""Generate docs/examples/runs/vision_poc_demo.html — visual viewer for vision POC runs.

For each run renders:
  - KPI cards (status, latency, warnings, degraded, gallery counts)
  - EMBEDDED thumbnails of every gallery image the LLM saw, badged as
    use / avoid / reference / neutral based on visual_selection
  - Full PostEnrichment breakdown (strategic_decisions, image brief, caption,
    cta, hashtag_strategy, do_not, confidence, brand_intelligence)
  - cf_post_brief highlighted (the compact CF payload)
  - brand_dna pre-formatted
  - Collapsibles with input envelope + full callback_body raw JSON

Usage:
  python scripts/demo/build_vision_poc_html.py

Prerequisites:
  reports/vision_poc_runs.json must exist — run scripts/demo/vision_poc.py first.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
IMAGES_DIR = ROOT / "images"
RUNS_PATH = ROOT / "reports" / "vision_poc_runs.json"
CACHE_DIR = ROOT / "reports" / "_images_cache"
OUT_PATH = ROOT / "docs" / "examples" / "runs" / "vision_poc_demo.html"

THUMB_MAX_EDGE = 720
THUMB_JPEG_QUALITY = 78


def _mock_url_to_local_path(url: str) -> Path | None:
    """Map placeholder mock-s3 URL → local file in images/."""
    prefix = "https://mock-s3.plinng.local/nubiex/"
    if not url.startswith(prefix):
        return None
    # mock URL has underscores: Nubiex_Valores_1.jpg
    # local file uses spaces: Nubiex Valores 1.jpg
    fname = url[len(prefix):].replace("_", " ")
    path = IMAGES_DIR / fname
    return path if path.exists() else None


def _fetch_or_cache(url: str) -> bytes | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    ext = ".jpg"
    cache_file = CACHE_DIR / f"{key}{ext}"
    if cache_file.exists():
        return cache_file.read_bytes()
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
        cache_file.write_bytes(data)
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"  [fetch FAIL] {url[:60]}: {exc}")
        return None


def _thumb_data_uri(raw: bytes) -> str | None:
    try:
        img = Image.open(io.BytesIO(raw))
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, THUMB_MAX_EDGE / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=THUMB_JPEG_QUALITY, optimize=True)
        return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception as exc:  # noqa: BLE001
        print(f"  [thumb FAIL] {exc}")
        return None


def get_image_data_uri(url: str) -> str | None:
    """Resolve URL → base64 data URI (from local folder or HTTP fetch)."""
    local_path = _mock_url_to_local_path(url)
    if local_path:
        return _thumb_data_uri(local_path.read_bytes())
    raw = _fetch_or_cache(url)
    if raw is None:
        return None
    return _thumb_data_uri(raw)


# ─── HTML helpers ────────────────────────────────────────────────────────────


def esc(text: object) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def conf_class(level: str | None) -> str:
    lvl = (level or "medium").lower()
    return lvl if lvl in {"high", "medium", "low"} else "medium"


def decision_block(label: str, choice: dict | None) -> str:
    if not choice:
        return ""
    alts = "".join(f'<span class="alt">{esc(a)}</span>' for a in (choice.get("alternatives_considered") or []))
    alts_block = f'<div class="alts">{alts}</div>' if alts else ""
    return (
        '<div class="decision">'
        f'  <div class="label">{esc(label)}</div>'
        f'  <div class="chosen">{esc(choice.get("chosen", ""))}</div>'
        f'  {alts_block}'
        f'  <div class="why">{esc(choice.get("rationale", ""))}</div>'
        '</div>'
    )


def cta_card(cta: dict | None) -> str:
    if not cta:
        return '<div class="cta-card"><span class="ch">NONE</span></div>'
    ch = (cta.get("channel") or "none").upper()
    lbl = cta.get("label") or ""
    url = cta.get("url_or_handle") or ""
    url_html = f'<span class="url">→ {esc(url)}</span>' if url else ""
    return (
        '<div class="cta-card">'
        f'  <span class="ch">{esc(ch)}</span>'
        f'  <span class="lbl">{esc(lbl)}</span>'
        f'  {url_html}'
        '</div>'
    )


def brand_intelligence_grid(bi: dict | None) -> str:
    if not bi:
        return '<div class="bi-empty">No brand_intelligence produced.</div>'
    rows = [
        ("Business taxonomy", bi.get("business_taxonomy")),
        ("Funnel stage", bi.get("funnel_stage_target")),
        ("Voice register", bi.get("voice_register")),
        ("Emotional beat", bi.get("emotional_beat")),
        ("Audience persona", bi.get("audience_persona")),
        ("Unfair advantage", bi.get("unfair_advantage")),
        ("Rhetorical device", bi.get("rhetorical_device")),
    ]
    html = ['<div class="bi-grid">']
    for title, value in rows:
        html.append(
            f'<div class="bi-row"><div class="bi-label">{esc(title)}</div>'
            f'<div class="bi-value">{esc(value or "—")}</div></div>'
        )
    risks = bi.get("risk_flags") or []
    risks_pills = (
        "".join(f'<span class="chip warn">{esc(r)}</span>' for r in risks)
        or '<span class="chip ghost">none</span>'
    )
    html.append(
        '<div class="bi-row"><div class="bi-label">Risk flags</div>'
        f'<div class="bi-value">{risks_pills}</div></div>'
    )
    html.append('</div>')
    return "".join(html)


def warnings_chips(warnings: list[dict]) -> str:
    if not warnings:
        return '<span class="chip good">no warnings</span>'
    pills = []
    for w in warnings:
        code = (w or {}).get("code", "")
        msg = esc((w or {}).get("message", ""))
        pills.append(f'<span class="chip warn" title="{msg}">{esc(code)}</span>')
    return "".join(pills)


def gallery_tiles(gallery_items: list[dict], recommended: set[str], avoid: set[str], references: set[str], resolver) -> str:
    """Render gallery as a grid of image tiles with badges."""
    if not gallery_items:
        return '<div class="bi-empty">Gallery empty.</div>'
    tiles = []
    for item in gallery_items:
        url = item.get("url", "")
        name = (item.get("name") or url.rsplit("/", 1)[-1]).replace("_", " ")
        if url in recommended:
            cls, badge = "use", "USE"
        elif url in avoid:
            cls, badge = "avoid", "AVOID"
        elif url in references:
            cls, badge = "reference", "REF"
        else:
            cls, badge = "", "—"
        data_uri = resolver(url) or ""
        img_html = (
            f'<img src="{data_uri}" alt="{esc(name)}" loading="lazy"/>'
            if data_uri else '<div class="img-missing">image not resolvable</div>'
        )
        tiles.append(
            f'<figure class="tile {cls}">'
            f'  <div class="badge badge-{cls or "neutral"}">{badge}</div>'
            f'  {img_html}'
            f'  <figcaption><b>{esc(name)}</b><br><span class="u">{esc(url)}</span></figcaption>'
            f'</figure>'
        )
    return f'<div class="tiles-grid">{"".join(tiles)}</div>'


def render_panel(run: dict, idx: int, resolver) -> str:
    cb = run.get("callback_body") or {}
    status = cb.get("status", "UNKNOWN")
    envelope = run.get("envelope") or {}
    gallery_items = (
        ((envelope.get("payload") or {}).get("action_execution_gates") or {})
        .get("image_catalog", {})
        .get("response", {})
        .get("data", [])
    )

    if status != "COMPLETED":
        return (
            f'<div class="tab-panel" id="panel-{idx}">'
            f'  <div class="fail-box">'
            f'    <h2>{esc(run["client"])} run {run["run_idx"]} — {esc(status)}</h2>'
            f'    <p class="error-text">{esc(cb.get("error_message", "no error_message"))}</p>'
            f'  </div>'
            '</div>'
        )

    output = cb["output_data"]
    e = output["enrichment"]
    warnings = output.get("warnings") or []
    trace = output.get("trace") or {}

    vs = e.get("visual_selection") or {}
    rec = set(vs.get("recommended_asset_urls") or [])
    avoid = set(vs.get("avoid_asset_urls") or [])
    refs = set(vs.get("recommended_reference_urls") or [])

    caption = e.get("caption") or {}
    image = e.get("image") or {}
    cta = e.get("cta") or {}
    hashtag = e.get("hashtag_strategy") or {}
    conf = e.get("confidence") or {}
    decisions = e.get("strategic_decisions") or {}
    bi = e.get("brand_intelligence")
    brand_dna = e.get("brand_dna", "")
    cf_brief = e.get("cf_post_brief", "")

    decisions_html = (
        decision_block("Surface format", decisions.get("surface_format"))
        + decision_block("Angle", decisions.get("angle"))
        + decision_block("Voice", decisions.get("voice"))
    )

    do_not = e.get("do_not") or []
    do_not_html = (
        "".join(f'<span class="chip ghost">{esc(x)}</span>' for x in do_not)
        or '<span class="chip ghost">—</span>'
    )

    themes = hashtag.get("themes") or []
    tags = hashtag.get("tags") or []
    themes_html = (
        "".join(f'<span class="chip">#{esc(t)}</span>' for t in themes)
        or '<span class="chip ghost">no themes</span>'
    )
    tags_html = (
        " ".join(f'<code class="tag">{esc(t)}</code>' for t in tags)
        or '<span class="chip ghost">no tags</span>'
    )

    gs = trace.get("gallery_stats") or {}
    latency = trace.get("latency_ms", run.get("latency_ms", 0))
    vision_count = len(gallery_items)

    return f"""
<div class="tab-panel" id="panel-{idx}">
  <div class="grid-4">
    <div class="card"><h3>Status</h3><div class="v">{esc(status)} <small>{esc(trace.get("surface", ""))} / {esc(trace.get("mode", ""))}</small></div></div>
    <div class="card"><h3>Latency</h3><div class="v">{latency} ms <small>repair: {"yes" if trace.get("repair_attempted") else "no"}</small></div></div>
    <div class="card"><h3>Gallery</h3><div class="v">{gs.get("accepted_count", 0)} / {gs.get("raw_count", 0)} <small>degraded: {"yes" if trace.get("degraded") else "no"}</small></div></div>
    <div class="card"><h3>Vision fed</h3><div class="v">{vision_count} img <small>inline bytes</small></div></div>
  </div>

  <!-- VISION GALLERY (the money shot) -->
  <div class="card-block">
    <div class="card-block-head"><h3>👁️ Vision gallery — what the LLM saw + its verdict</h3>
      <span class="tag-public">visual_selection</span>
    </div>
    <p class="lead">Imágenes enviadas como Part multimodal. Border verde = recommended, dashed roja = avoid.</p>
    <div class="pill-row" style="margin:6px 0 12px;">
      <span class="chip good">use · {len(rec)}</span>
      <span class="chip">reference · {len(refs)}</span>
      <span class="chip bad">avoid · {len(avoid)}</span>
    </div>
    {gallery_tiles(gallery_items, rec, avoid, refs, resolver)}
  </div>

  <!-- POST PROPOSAL -->
  <div class="post-card">
    <div class="post-head">
      <span class="surface-pill">{esc((e.get("surface_format") or "post").upper())}</span>
      <span class="pillar-pill">{esc((e.get("content_pillar") or "—").replace("_", " ").upper())}</span>
      <h3 class="post-title">{esc(e.get("title", "—"))}</h3>
    </div>
    <div class="post-grid">
      <div class="post-col">
        <div class="post-section">
          <h4>🎯 Objective</h4>
          <p>{esc(e.get("objective", ""))}</p>
        </div>
        <div class="post-section">
          <h4>⚔️ Strategic decisions</h4>
          {decisions_html}
        </div>
        <div class="post-section">
          <h4>✨ Visual style notes</h4>
          <p>{esc(e.get("visual_style_notes", ""))}</p>
        </div>
        <div class="post-section">
          <h4>🚫 Do not</h4>
          <div class="pill-row">{do_not_html}</div>
        </div>
      </div>
      <div class="post-col">
        <div class="post-section">
          <h4>🖼️ Image brief</h4>
          <div class="img-brief"><div class="lbl">Concept</div><div class="body">{esc(image.get("concept", ""))}</div></div>
          <div class="img-brief gen"><div class="lbl">Generation prompt</div><div class="body">{esc(image.get("generation_prompt", ""))}</div></div>
          <div class="img-brief alt"><div class="lbl">Alt text</div><div class="body">{esc(image.get("alt_text", ""))}</div></div>
        </div>
        <div class="post-section">
          <h4>📝 Caption</h4>
          <div class="cap hook"><div class="lbl"><span>Hook</span><span class="len">{len(caption.get("hook", ""))} ch</span></div><div class="txt">{esc(caption.get("hook", ""))}</div></div>
          <div class="cap body"><div class="lbl"><span>Body</span><span class="len">{len(caption.get("body", ""))} ch</span></div><div class="txt">{esc(caption.get("body", ""))}</div></div>
          <div class="cap cta"><div class="lbl"><span>CTA line</span><span class="len">{len(caption.get("cta_line", ""))} ch</span></div><div class="txt">{esc(caption.get("cta_line", ""))}</div></div>
        </div>
        <div class="post-section">
          <h4>👉 Call to action</h4>
          {cta_card(cta)}
        </div>
        <div class="post-section">
          <h4>🏷️ Hashtag strategy</h4>
          <div class="pill-row"><span class="chip">intent · {esc(hashtag.get("intent", "—"))}</span><span class="chip">volume · {hashtag.get("suggested_volume", 0)}</span></div>
          <div class="pill-row">{themes_html}</div>
          <div class="tags-line">{tags_html}</div>
        </div>
      </div>
    </div>
  </div>

  <!-- CF POST BRIEF (the ready-to-execute CF payload, new in v2) -->
  <div class="card-block highlight-cf">
    <div class="card-block-head"><h3>📦 cf_post_brief — ready for CONTENT_FACTORY</h3>
      <span class="tag-public">public · CF lee ESTO primero</span>
    </div>
    <p class="lead">Bloque compacto con editorial image note + Caption completa + Hashtags. El diseñador/copywriter de CF lo consume tal cual.</p>
    <pre class="cf-brief">{esc(cf_brief or "—")}</pre>
  </div>

  <!-- BRAND DNA -->
  <div class="card-block">
    <div class="card-block-head"><h3>🧬 brand_dna — design-system reference</h3>
      <span class="tag-public">public · client_dna para CF</span>
    </div>
    <pre class="dna">{esc(brand_dna or "—")}</pre>
  </div>

  <!-- BRAND INTELLIGENCE -->
  <div class="card-block">
    <div class="card-block-head"><h3>🧠 brand_intelligence — internal reasoning</h3>
      <span class="tag-internal">internal · para subagentes</span>
    </div>
    {brand_intelligence_grid(bi)}
  </div>

  <div class="split-2">
    <div class="card-block">
      <h3>Confidence</h3>
      <div class="conf-row">
        <div class="conf"><b>surface_format</b><span class="lvl {conf_class(conf.get("surface_format"))}">{esc(conf.get("surface_format", "medium"))}</span></div>
        <div class="conf"><b>angle</b><span class="lvl {conf_class(conf.get("angle"))}">{esc(conf.get("angle", "medium"))}</span></div>
        <div class="conf"><b>palette_match</b><span class="lvl {conf_class(conf.get("palette_match"))}">{esc(conf.get("palette_match", "medium"))}</span></div>
        <div class="conf"><b>cta_channel</b><span class="lvl {conf_class(conf.get("cta_channel"))}">{esc(conf.get("cta_channel", "medium"))}</span></div>
      </div>
    </div>
    <div class="card-block">
      <h3>Warnings</h3>
      <div class="pill-row">{warnings_chips(warnings)}</div>
    </div>
  </div>

  <details class="acc">
    <summary>Raw input envelope (router → marketer)</summary>
    <pre class="json">{esc(json.dumps(envelope, ensure_ascii=False, indent=2))}</pre>
  </details>
  <details class="acc">
    <summary>Raw callback_body (marketer → router) — EXACT router contract shape</summary>
    <pre class="json">{esc(json.dumps(cb, ensure_ascii=False, indent=2))}</pre>
  </details>
</div>
"""


def comparison_table(runs: list[dict]) -> str:
    rows = []
    for r in runs:
        cb = r.get("callback_body") or {}
        status = cb.get("status", "?")
        if status != "COMPLETED":
            rows.append(
                f'<tr><td><b>{esc(r["client"])}</b> #{r["run_idx"]}</td>'
                f'<td class="status-bad">{esc(status)}</td>'
                f'<td>{r.get("latency_ms", 0)} ms</td>'
                f'<td colspan="6">—</td></tr>'
            )
            continue
        output = cb["output_data"]
        e = output["enrichment"]
        vs = e["visual_selection"]
        bi = e.get("brand_intelligence") or {}
        warns = [w["code"] for w in output.get("warnings", [])]
        rec_names = [u.rsplit("/", 1)[-1].replace("_", " ")[:24] for u in vs.get("recommended_asset_urls", [])]
        avoid_names = [u.rsplit("/", 1)[-1].replace("_", " ")[:24] for u in vs.get("avoid_asset_urls", [])]
        rows.append(
            f'<tr><td><b>{esc(r["client"])}</b> #{r["run_idx"]}</td>'
            f'<td class="status-ok">{esc(status)}</td>'
            f'<td>{r.get("latency_ms", 0)} ms</td>'
            f'<td>{esc(e["content_pillar"])}</td>'
            f'<td>{esc(e["cta"]["channel"])}</td>'
            f'<td>{esc(bi.get("funnel_stage_target", "—"))}</td>'
            f'<td>{esc(bi.get("emotional_beat", "—"))}</td>'
            f'<td>{"<br>".join(esc(n) for n in rec_names) or "—"}</td>'
            f'<td>{"<br>".join(esc(n) for n in avoid_names) or "—"}</td>'
            f'<td>{len(warns)}</td></tr>'
        )
    return f"""
<table class="cmp">
<thead><tr>
  <th>Client · Run</th><th>Status</th><th>Latency</th>
  <th>Pillar</th><th>CTA</th><th>Funnel</th><th>Emotion</th>
  <th>Recommended</th><th>Avoid</th><th>Warn</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
"""


# ─── CSS (shared with marketer_demo_v2) ──────────────────────────────────────


CSS = """
:root {
  --bg: #0b0f17; --panel: #121826; --panel-2: #0f1422;
  --text: #e6ecf5; --muted: #8b97ad; --line: #1f2738;
  --accent: #7c5cff; --accent-2: #00d4ff;
  --good: #2bd07b; --warn: #f5a524; --bad: #ff5d6c;
  --code-bg: #0a0e17;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg:#f6f7fb; --panel:#ffffff; --panel-2:#fafbff;
    --text:#1a2030; --muted:#5b6577; --line:#e6e8ef;
    --code-bg:#f1f3f9;
  }
}
* { box-sizing: border-box; }
html, body { margin:0; padding:0; background:var(--bg); color:var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Inter, Roboto, sans-serif; }
.wrap { max-width:1320px; margin:0 auto; padding:28px 22px 80px; }

header.hero { display:grid; grid-template-columns:1fr auto; gap:20px; align-items:end;
  padding-bottom:22px; border-bottom:1px solid var(--line); margin-bottom:22px; }
.hero h1 { margin:0; font-size:26px; }
.hero h1 .grad { background:linear-gradient(90deg, var(--accent), var(--accent-2));
  -webkit-background-clip:text; background-clip:text; color:transparent; }
.hero p { margin:6px 0 0; color:var(--muted); max-width:860px; line-height:1.5; }
.meta { color:var(--muted); font-size:12.5px; text-align:right; line-height:1.6; }
.meta b { color:var(--text); font-weight:600; }

table.cmp { width:100%; border-collapse:collapse; margin:14px 0 24px; font-size:12.5px;
  background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
table.cmp th, table.cmp td { padding:10px 12px; text-align:left;
  border-bottom:1px solid var(--line); vertical-align:top; }
table.cmp thead th { background:var(--panel-2); color:var(--muted); font-weight:600;
  font-size:11px; letter-spacing:0.06em; text-transform:uppercase; }
table.cmp tr:last-child td { border-bottom:none; }
.status-ok { color:var(--good); font-weight:600; }
.status-bad { color:var(--bad); font-weight:600; }

.tabs-nav { display:flex; gap:6px; flex-wrap:wrap; margin:16px 0 20px;
  padding-bottom:10px; border-bottom:1px solid var(--line); }
.tab-btn { font:inherit; cursor:pointer; background:transparent; color:var(--muted);
  border:1px solid var(--line); padding:8px 14px; border-radius:10px;
  display:flex; flex-direction:column; align-items:flex-start; gap:2px; min-width:140px; transition:all 0.15s; }
.tab-btn:hover { color:var(--text); border-color:var(--accent); }
.tab-btn b { font-size:13.5px; font-weight:600; }
.tab-btn small { font-size:11px; color:var(--muted); }
.tab-btn.active { background:linear-gradient(135deg, rgba(124,92,255,0.15), rgba(0,212,255,0.12));
  color:var(--text); border-color:var(--accent); }

.tab-panel { display:none; } .tab-panel.active { display:block; }

.grid-4 { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:4px 0 20px; }
@media (max-width:900px) { .grid-4 { grid-template-columns:1fr 1fr; } }
.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
.card h3 { margin:0 0 6px; font-size:12px; letter-spacing:0.03em; text-transform:uppercase; color:var(--muted); }
.card .v { font-size:17px; font-weight:600; }
.card .v small { color:var(--muted); font-weight:400; font-size:11px; margin-left:6px; }

.card-block { background:var(--panel); border:1px solid var(--line); border-radius:14px;
  padding:16px 18px; margin:14px 0; }
.card-block h3 { margin:0 0 4px; font-size:16px; }
.card-block-head { display:flex; align-items:center; gap:10px; margin-bottom:6px; flex-wrap:wrap; }
.tag-public { font-size:11px; background:rgba(43,208,123,0.15); color:var(--good);
  padding:3px 10px; border-radius:999px; border:1px solid rgba(43,208,123,0.35); }
.tag-internal { font-size:11px; background:rgba(124,92,255,0.12); color:var(--accent);
  padding:3px 10px; border-radius:999px; border:1px solid rgba(124,92,255,0.35); }
.lead { color:var(--muted); margin:0 0 12px; line-height:1.5; font-size:13.5px; }

.highlight-cf { border:2px solid rgba(43,208,123,0.4); background:linear-gradient(135deg,
  rgba(43,208,123,0.06), rgba(0,212,255,0.04)); }

.split-2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
@media (max-width:900px) { .split-2 { grid-template-columns:1fr; } }

.post-card { background:var(--panel); border:1px solid var(--line); border-radius:14px; overflow:hidden; margin-bottom:14px; }
.post-head { padding:18px 22px 12px; border-bottom:1px solid var(--line);
  display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
.surface-pill, .pillar-pill { display:inline-block; font-size:11px; letter-spacing:0.10em;
  text-transform:uppercase; padding:4px 10px; border-radius:999px; }
.surface-pill { color:var(--accent); background:rgba(124,92,255,0.12); border:1px solid rgba(124,92,255,0.35); }
.pillar-pill { color:var(--accent-2); background:rgba(0,212,255,0.10); border:1px solid rgba(0,212,255,0.30); }
.post-title { width:100%; margin:8px 0 0; font-size:22px; font-weight:700; }
.post-grid { display:grid; grid-template-columns:1.2fr 1fr; gap:0; }
@media (max-width:980px) { .post-grid { grid-template-columns:1fr; } }
.post-col { padding:18px 22px; }
.post-col + .post-col { border-left:1px solid var(--line); }
@media (max-width:980px) { .post-col + .post-col { border-left:none; border-top:1px solid var(--line); } }
.post-section { margin:0 0 16px; }
.post-section:last-child { margin-bottom:0; }
.post-section h4 { margin:0 0 6px; font-size:12px; letter-spacing:0.06em;
  text-transform:uppercase; color:var(--muted); }
.post-section p { margin:0; line-height:1.55; font-size:14px; }

.decision { background:var(--panel-2); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:8px; }
.decision .label { font-size:11px; letter-spacing:0.08em; text-transform:uppercase; color:var(--muted); }
.decision .chosen { margin-top:3px; font-weight:600; font-size:14px; }
.decision .alts { margin-top:5px; display:flex; flex-wrap:wrap; gap:5px; }
.decision .alt { font-size:11px; padding:2px 7px; border-radius:999px;
  border:1px dashed var(--line); color:var(--muted); text-decoration:line-through; }
.decision .why { margin-top:6px; font-size:12.5px; line-height:1.5; opacity:0.9; }
.decision .why::before { content:"↳ "; color:var(--muted); }

.cap { background:var(--panel-2); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:8px; }
.cap .lbl { font-size:11px; letter-spacing:0.08em; text-transform:uppercase;
  color:var(--muted); display:flex; justify-content:space-between; }
.cap .txt { margin-top:5px; white-space:pre-wrap; line-height:1.5; font-size:13.5px; }
.cap.hook .txt { font-weight:600; }

.img-brief { background:var(--panel-2); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:8px; }
.img-brief .lbl { font-size:11px; letter-spacing:0.08em; text-transform:uppercase;
  color:var(--muted); margin-bottom:4px; }
.img-brief .body { line-height:1.5; font-size:13px; }
.img-brief.gen .body { font-family:ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size:12px; color:var(--accent-2); }
.img-brief.alt .body { color:var(--muted); font-style:italic; font-size:12.5px; }

.cta-card { display:flex; align-items:center; gap:10px; padding:10px 12px;
  background:linear-gradient(90deg, rgba(124,92,255,0.12), rgba(0,212,255,0.10));
  border:1px solid rgba(124,92,255,0.30); border-radius:10px; }
.cta-card .ch { background:linear-gradient(90deg, var(--accent), var(--accent-2));
  color:white; font-size:11px; letter-spacing:0.08em; text-transform:uppercase;
  padding:3px 10px; border-radius:999px; font-weight:700; }
.cta-card .lbl { font-weight:600; }
.cta-card .url { color:var(--muted); font-size:12px; word-break:break-all; }

.tags-line { margin-top:8px; line-height:2; font-size:13px; }
code.tag { background:var(--panel-2); border:1px solid var(--line); padding:3px 8px;
  border-radius:999px; font-size:12px; color:var(--accent-2); margin-right:3px; }

pre.dna { background:var(--code-bg); color:var(--text); padding:14px 16px;
  border-radius:10px; border:1px solid var(--line); white-space:pre-wrap;
  font-family:ui-sans-serif, system-ui, sans-serif; font-size:13px; line-height:1.55;
  max-height:520px; overflow:auto; margin:8px 0 0; }

pre.cf-brief { background:rgba(43,208,123,0.06); color:var(--text); padding:14px 16px;
  border-radius:10px; border:1px solid rgba(43,208,123,0.25); white-space:pre-wrap;
  font-family:ui-sans-serif, system-ui, sans-serif; font-size:13.5px; line-height:1.6;
  margin:8px 0 0; }

.bi-grid { display:grid; grid-template-columns:180px 1fr; gap:0;
  background:var(--panel-2); border:1px solid var(--line); border-radius:10px; overflow:hidden; margin-top:8px; }
.bi-row { display:contents; }
.bi-row > div { padding:9px 14px; border-bottom:1px solid var(--line); }
.bi-row:last-child > div { border-bottom:none; }
.bi-label { color:var(--muted); font-size:12px; letter-spacing:0.05em; text-transform:uppercase;
  background:rgba(0,0,0,0.10); }
.bi-value { font-size:13.5px; line-height:1.45; }
.bi-empty { color:var(--muted); font-style:italic; padding:12px; }

.pill-row { display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }
.chip { display:inline-flex; align-items:center; gap:5px; padding:5px 9px; border-radius:999px;
  border:1px solid var(--line); background:var(--panel-2); font-size:12px; color:var(--text); }
.chip.good { color:var(--good); border-color:rgba(43,208,123,0.4); }
.chip.bad  { color:var(--bad);  border-color:rgba(255,93,108,0.4); }
.chip.warn { color:var(--warn); border-color:rgba(245,165,36,0.4); }
.chip.ghost { color:var(--muted); border-style:dashed; }

.conf-row { display:grid; grid-template-columns:1fr; gap:6px; }
.conf { display:flex; align-items:center; justify-content:space-between;
  background:var(--panel-2); border:1px solid var(--line); border-radius:8px;
  padding:6px 10px; font-size:12.5px; }
.conf .lvl { font-size:11px; padding:2px 8px; border-radius:999px; text-transform:uppercase; letter-spacing:.06em; }
.lvl.high { background:rgba(43,208,123,0.15); color:var(--good); }
.lvl.medium { background:rgba(245,165,36,0.15); color:var(--warn); }
.lvl.low { background:rgba(255,93,108,0.15); color:var(--bad); }

/* Gallery tiles — the centerpiece */
.tiles-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr));
  gap:14px; margin-top:8px; }
.tile { margin:0; background:var(--panel-2); border:2px solid var(--line); border-radius:12px;
  overflow:hidden; position:relative; transition:transform 0.15s; }
.tile:hover { transform:translateY(-2px); }
.tile img { width:100%; height:260px; object-fit:cover; display:block; background:#000; }
.tile.use { border-color:var(--good); box-shadow:0 0 0 3px rgba(43,208,123,0.15); }
.tile.avoid { border-color:var(--bad); border-style:dashed; opacity:0.65; }
.tile.reference { border-color:var(--accent-2); }
.tile figcaption { padding:10px 12px; font-size:11.5px; color:var(--muted); line-height:1.45; }
.tile figcaption b { color:var(--text); font-size:12px; }
.tile figcaption .u { font-family:ui-monospace, monospace; font-size:10.5px; word-break:break-all; }
.badge { position:absolute; top:10px; left:10px; z-index:2; padding:4px 10px; border-radius:999px;
  font-size:11px; font-weight:700; letter-spacing:0.08em; text-transform:uppercase;
  background:rgba(0,0,0,0.6); color:white; backdrop-filter:blur(4px); }
.badge-use { background:var(--good); color:#0b3b22; }
.badge-avoid { background:var(--bad); color:#3d0a14; }
.badge-reference { background:var(--accent-2); color:#0b2b3b; }
.badge-neutral { background:rgba(139,151,173,0.6); color:var(--text); }
.img-missing { height:260px; display:flex; align-items:center; justify-content:center;
  color:var(--muted); font-style:italic; font-size:12px; background:repeating-linear-gradient(
    45deg, var(--panel), var(--panel) 10px, var(--panel-2) 10px, var(--panel-2) 20px); }

details.acc { margin-top:10px; }
details.acc > summary { cursor:pointer; color:var(--muted); font-size:13px; padding:8px 12px;
  border:1px dashed var(--line); border-radius:8px; }
details.acc[open] > summary { color:var(--text); border-style:solid; }
pre.json { margin:0; padding:14px 16px; background:var(--code-bg); color:var(--text);
  overflow:auto; max-height:480px; font-family:ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size:12px; line-height:1.5; border:1px solid var(--line); border-top:none; border-radius:0 0 8px 8px; }

.fail-box { background:rgba(255,93,108,0.08); border:1px solid rgba(255,93,108,0.35);
  border-radius:12px; padding:22px 28px; margin:14px 0; }
.fail-box h2 { margin:0 0 8px; color:var(--bad); }
.error-text { font-family:ui-monospace, monospace; font-size:13px; color:var(--muted); white-space:pre-wrap; }

footer { color:var(--muted); font-size:12px; margin-top:26px; text-align:center; }
"""


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    if not RUNS_PATH.exists():
        print(f"ERROR: {RUNS_PATH} not found. Run scripts/demo/vision_poc.py first.")
        return 1

    runs = json.loads(RUNS_PATH.read_text(encoding="utf-8"))
    if not runs:
        print("ERROR: runs file is empty.")
        return 1

    # Resolve unique image URLs once, cache data URIs.
    resolver_cache: dict[str, str | None] = {}
    unique_urls: set[str] = set()
    for r in runs:
        env = r.get("envelope") or {}
        gallery = (
            ((env.get("payload") or {}).get("action_execution_gates") or {})
            .get("image_catalog", {})
            .get("response", {})
            .get("data", [])
        )
        for item in gallery:
            if item.get("url"):
                unique_urls.add(item["url"])

    print(f"Resolving {len(unique_urls)} unique images...")
    for url in sorted(unique_urls):
        print(f"  {url[:70]}")
        resolver_cache[url] = get_image_data_uri(url)

    def resolver(url: str) -> str | None:
        return resolver_cache.get(url)

    tabs_nav = "".join(
        f'<button class="tab-btn{" active" if i == 0 else ""}" data-tab="{i}">'
        f'<b>{esc(r["client"])}</b><small>run {r["run_idx"]}</small></button>'
        for i, r in enumerate(runs)
    )
    panels = "\n".join(render_panel(r, i, resolver) for i, r in enumerate(runs))
    cmp = comparison_table(runs)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_images = sum(1 for v in resolver_cache.values() if v)

    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Vision POC — marketer con imágenes reales</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

  <header class="hero">
    <div>
      <h1>Vision POC — <span class="grad">marketer viendo imágenes reales</span></h1>
      <p>4 runs (2 clientes × 2 iteraciones) donde marketer recibe las imágenes
      del gallery como <b>Part multimodal</b> en la llamada a Gemini, y decide
      recommended/avoid basado en los pixeles reales — no solo en tags.
      Cada <code>callback_body</code> es el shape exacto que viajaría al router
      (<code>status + output_data.{{enrichment, warnings, trace}} + error_message</code>).</p>
    </div>
    <div class="meta">
      <div><b>runs</b> {len(runs)}</div>
      <div><b>images resolved</b> {total_images}/{len(unique_urls)}</div>
      <div><b>model</b> gemini-3-flash-preview</div>
      <div><b>generated</b> {esc(generated)}</div>
    </div>
  </header>

  <h2 style="margin:0 0 8px; font-size:16px;">Comparación cross-run</h2>
  {cmp}

  <div class="tabs-nav">{tabs_nav}</div>

  {panels}

  <footer>Generated with scripts/demo/build_vision_poc_html.py · images embedded as base64 data URIs</footer>
</div>

<script>
  const btns = document.querySelectorAll('.tab-btn');
  const panels = document.querySelectorAll('.tab-panel');
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

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html, encoding="utf-8")
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"\nWrote {OUT_PATH} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
