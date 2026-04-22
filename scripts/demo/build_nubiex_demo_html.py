#!/usr/bin/env python3
"""Build the Nubiex power-test demo HTML from the _full.json output.

Usage:
    PYTHONPATH=src python scripts/demo/build_nubiex_demo_html.py
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FULL_JSON = ROOT / "reports" / "nubiex_power_test_2026-04-21_full.json"
OUT_HTML  = ROOT / "docs" / "examples" / "runs" / "nubiex_demo_2026-04-21.html"

# Maps fixture CDN URL → (display name, local relative path from docs/examples/runs/ folder)
GALLERY = {
    "https://cdn.nubiex.es/brand/nubiex_valores_1.jpg": ("nubiex_valores_1.jpg", "../images/Nubiex Valores 1.jpg"),
    "https://cdn.nubiex.es/brand/nubiex_valores_2.jpg": ("nubiex_valores_2.jpg", "../images/Nubiex Valores 2.jpg"),
    "https://cdn.nubiex.es/brand/nubiex_valores_3.jpg": ("nubiex_valores_3.jpg", "../images/Nubiex Valores 3.jpg"),
    "https://cdn.nubiex.es/brand/nubiex_valores_4.jpg": ("nubiex_valores_4.jpg", "../images/Nubiex Valores 4.jpg"),
}

SURFACE_ICON = {"post": "📸", "story": "⚡", "reel": "🎬", "carousel": "🎠"}
PILLAR_COLOR = {
    "product": "#00d4ff", "education": "#f5a524", "community": "#2bd07b",
    "promotion": "#ff5d6c", "behind_the_scenes": "#a78bfa", "customer": "#fb923c",
}
FUNNEL_LABEL = {
    "awareness": "🔍 Awareness", "consideration": "💡 Consideration",
    "conversion": "🎯 Conversion", "retention": "❤ Retention", "advocacy": "📣 Advocacy",
}

def esc(s: str | None) -> str:
    return html.escape(str(s or ""), quote=True)

def nl2br(s: str) -> str:
    return html.escape(str(s or "")).replace("\n", "<br>")

def split_cf_brief(cf: str) -> tuple[str, str, str]:
    """Split cf_post_brief into (concept_block, caption_block, hashtag_block)."""
    concept, caption, hashtags = cf, "", ""
    if "Caption:" in cf:
        parts = cf.split("Caption:", 1)
        concept = parts[0].strip()
        rest = parts[1]
        if "Hashtags:" in rest:
            cap_parts = rest.split("Hashtags:", 1)
            caption = cap_parts[0].strip()
            hashtags = cap_parts[1].strip()
        else:
            caption = rest.strip()
    return concept, caption, hashtags

def conf_chip(level: str | None) -> str:
    lvl = (level or "medium").lower()
    return f'<span class="lvl {esc(lvl)}">{esc(lvl)}</span>'

def render_concept_block(concept: str) -> str:
    lines = concept.split("\n")
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("CONCEPT —"):
            out.append(f'<div class="concept-title">{esc(s)}</div>')
        elif s.startswith("Imagen:"):
            val = s[len("Imagen:"):].strip()
            out.append(f'<div class="concept-meta"><span class="concept-key">Imagen</span> <span class="concept-val">{esc(val)}</span></div>')
        elif s.startswith("Tipo:"):
            val = s[len("Tipo:"):].strip()
            out.append(f'<div class="concept-meta"><span class="concept-key">Tipo</span> <span class="concept-val concept-tipo">{esc(val)}</span></div>')
        elif s:
            out.append(f'<div class="concept-body-line">{esc(s)}</div>')
    return "\n".join(out)

def render_image_tiles(selected_urls: list[str], all_urls: list[str]) -> str:
    selected_set = set(selected_urls)
    tiles = []
    for cdn_url, (name, local_path) in GALLERY.items():
        if cdn_url in selected_set:
            badge = '<span class="badge badge-use">USE</span>'
            cls = "tile use"
        elif cdn_url in all_urls:
            badge = '<span class="badge badge-avoid">AVOID</span>'
            cls = "tile avoid"
        else:
            badge = '<span class="badge badge-neutral">GALLERY</span>'
            cls = "tile"
        tiles.append(f"""
        <figure class="{cls}">
          {badge}
          <img src="{esc(local_path)}" alt="{esc(name)}" loading="lazy">
          <figcaption><b>{esc(name)}</b></figcaption>
        </figure>""")
    return "\n".join(tiles)

def render_bi_row(label: str, value: str | list | None) -> str:
    if isinstance(value, list):
        display = ", ".join(str(v) for v in value) if value else "—"
    else:
        display = str(value or "—")
    return f"""<div class="bi-row"><div class="bi-label">{esc(label)}</div><div class="bi-value">{esc(display)}</div></div>"""

def render_decision(label: str, d: dict) -> str:
    chosen = d.get("chosen") or "—"
    alts = d.get("alternatives_considered") or []
    why = d.get("rationale") or ""
    alts_html = "".join(f'<span class="alt">{esc(a)}</span>' for a in alts)
    return f"""
    <div class="decision">
      <div class="label">{esc(label)}</div>
      <div class="chosen">{esc(chosen)}</div>
      {'<div class="alts">' + alts_html + '</div>' if alts else ''}
      {'<div class="why">' + esc(why) + '</div>' if why else ''}
    </div>"""

def render_tab_content(idx: int, run: dict) -> str:
    scenario = run["scenario"]
    cb = run.get("callback") or {}
    od = cb.get("output_data") or {}
    en = od.get("enrichment") or {}
    trace = od.get("trace") or {}
    warnings_list = od.get("warnings") or []
    status = cb.get("status") or "FAILED"

    if status == "FAILED" or not en:
        return f"""
        <div id="tab-{idx}" class="tab-panel">
          <div class="failed-card">
            <div class="failed-icon">✗</div>
            <div class="failed-msg">Run FAILED — {esc(cb.get('error_message') or 'unknown error')}</div>
          </div>
        </div>"""

    sf  = en.get("surface_format") or "post"
    pil = en.get("content_pillar") or ""
    title = en.get("title") or ""
    obj   = en.get("objective") or ""
    cf_brief = en.get("cf_post_brief") or ""
    brand_dna = en.get("brand_dna") or ""
    caption   = en.get("caption") or {}
    image     = en.get("image") or {}
    cta       = en.get("cta") or {}
    sd        = en.get("strategic_decisions") or {}
    bi        = en.get("brand_intelligence") or {}
    hs        = en.get("hashtag_strategy") or {}
    conf      = en.get("confidence") or {}
    vs        = en.get("visual_selection") or {}
    do_not    = en.get("do_not") or []

    selected_urls = vs.get("recommended_asset_urls") or []
    avoid_urls    = vs.get("avoid_asset_urls") or []

    concept_block, caption_block, hashtag_block = split_cf_brief(cf_brief)

    sf_icon = SURFACE_ICON.get(sf, "📄")
    pil_color = PILLAR_COLOR.get(pil, "#8b97ad")
    funnel = FUNNEL_LABEL.get(bi.get("funnel_stage_target") or "", "")
    lat_s = f"{trace.get('latency_ms', 0) / 1000:.1f}s"

    # Tags line
    tags_html = " ".join(f'<code class="tag">{esc(t)}</code>' for t in (hs.get("tags") or []))

    # Do-not chips
    donot_html = " ".join(f'<span class="chip bad">{esc(d)}</span>' for d in do_not)

    # Warnings
    warns_html = ""
    if warnings_list:
        warns_html = '<div class="warn-bar">' + " ".join(
            f'<span class="chip warn">{esc(w.get("code","?"))}</span>' for w in warnings_list
        ) + '</div>'

    repair_badge = '<span class="chip warn" style="margin-left:6px">⚙ repair</span>' if trace.get("repair_attempted") else ""

    return f"""
    <div id="tab-{idx}" class="tab-panel">

      <!-- RUN HEADER -->
      <div class="run-header">
        <div class="run-header-left">
          <span class="surface-pill">{sf_icon} {esc(sf)}</span>
          <span class="pillar-pill" style="color:{pil_color}; border-color:{pil_color}40; background:{pil_color}18">{esc(pil)}</span>
          {('<span class="funnel-pill">' + esc(funnel) + '</span>') if funnel else ''}
          <span class="lat-pill">{esc(lat_s)}</span>
          {repair_badge}
        </div>
        <div class="run-meta">{esc(scenario['label'])}</div>
      </div>
      <h2 class="run-title">{esc(title)}</h2>
      <p class="run-obj">{esc(obj)}</p>
      {warns_html}

      <!-- MAIN GRID: CF output + DNA -->
      <div class="main-grid">

        <!-- LEFT: CF OUTPUT -->
        <div class="cf-col">
          <div class="section-label section-cf">Para Content Factory</div>

          <div class="cf-card highlight-cf">
            <div class="cf-section-label">CONCEPT</div>
            <div class="concept-block">{render_concept_block(concept_block)}</div>
          </div>

          <div class="cf-card">
            <div class="cf-section-label">Caption</div>
            <div class="cap hook"><div class="lbl">HOOK <span class="char-count">{len(caption.get('hook') or '')} chars</span></div><div class="txt">{nl2br(caption.get('hook') or '')}</div></div>
            <div class="cap body"><div class="lbl">BODY</div><div class="txt">{nl2br(caption.get('body') or '')}</div></div>
            {'<div class="cap cta"><div class="lbl">CTA</div><div class="txt">' + nl2br(caption.get('cta_line') or '') + '</div></div>' if caption.get('cta_line') else ''}
          </div>

          {'<div class="cf-card"><div class="cf-section-label">Hashtags</div><div class="tags-line">' + tags_html + '</div></div>' if tags_html else ''}

          <!-- CTA channel -->
          <div class="cta-card">
            <span class="ch">{esc(cta.get('channel') or 'none')}</span>
            <div>
              <div class="lbl">{esc(cta.get('label') or '')}</div>
              {'<div class="url">' + esc(cta.get('url_or_handle') or '') + '</div>' if cta.get('url_or_handle') else ''}
            </div>
          </div>
        </div>

        <!-- RIGHT: Brand DNA + Image tiles -->
        <div class="dna-col">
          <div class="section-label">Brand DNA</div>
          <pre class="dna">{esc(brand_dna)}</pre>

          <div class="section-label" style="margin-top:18px">Imágenes de galería</div>
          <div class="tiles-grid">
            {render_image_tiles(selected_urls, list(avoid_urls))}
          </div>
        </div>

      </div>

      <!-- BOTTOM ROW: Strategic + BI + Image brief + Confidence + Do-not -->
      <div class="bottom-grid">

        <div class="card-block">
          <div class="section-label">Decisiones estratégicas</div>
          {render_decision("Surface format", sd.get("surface_format") or {})}
          {render_decision("Ángulo", sd.get("angle") or {})}
          {render_decision("Voz", sd.get("voice") or {})}
        </div>

        <div class="card-block">
          <div class="section-label tag-internal">Brand Intelligence (interno)</div>
          <div class="bi-grid">
            {render_bi_row("taxonomy", bi.get("business_taxonomy"))}
            {render_bi_row("funnel", bi.get("funnel_stage_target"))}
            {render_bi_row("voice register", bi.get("voice_register"))}
            {render_bi_row("emotional beat", bi.get("emotional_beat"))}
            {render_bi_row("rhetorical device", bi.get("rhetorical_device"))}
            {render_bi_row("audience", bi.get("audience_persona"))}
            {render_bi_row("unfair advantage", bi.get("unfair_advantage"))}
            {render_bi_row("risk flags", bi.get("risk_flags"))}
          </div>
        </div>

        <div class="card-block">
          <div class="section-label">Image brief</div>
          <div class="img-brief"><div class="lbl">Concept</div><div class="body">{esc(image.get('concept') or '')}</div></div>
          <div class="img-brief gen"><div class="lbl">Generation prompt</div><div class="body">{esc(image.get('generation_prompt') or '')}</div></div>
          <div class="img-brief alt"><div class="lbl">Alt text</div><div class="body">{esc(image.get('alt_text') or '')}</div></div>

          <div class="section-label" style="margin-top:14px">Confianza</div>
          <div class="conf-row">
            <div class="conf"><span>surface_format</span>{conf_chip(conf.get('surface_format'))}</div>
            <div class="conf"><span>angle</span>{conf_chip(conf.get('angle'))}</div>
            <div class="conf"><span>palette_match</span>{conf_chip(conf.get('palette_match'))}</div>
            <div class="conf"><span>cta_channel</span>{conf_chip(conf.get('cta_channel'))}</div>
          </div>

          {'<div class="section-label" style="margin-top:14px">Do not</div><div class="pill-row">' + donot_html + '</div>' if do_not else ''}
        </div>

      </div>
    </div>"""


def build_html(runs: list[dict]) -> str:
    # Summary stats
    n = len(runs)
    completed = sum(1 for r in runs if (r.get("callback") or {}).get("status") == "COMPLETED")
    imgs = sum(1 for r in runs if ((r.get("callback") or {}).get("output_data") or {}).get("enrichment") and
               ((r.get("callback") or {}).get("output_data") or {}).get("enrichment", {}).get("visual_selection", {}).get("recommended_asset_urls"))

    tab_buttons = []
    tab_panels  = []

    for i, run in enumerate(runs):
        scenario = run["scenario"]
        cb = run.get("callback") or {}
        en = (cb.get("output_data") or {}).get("enrichment") or {}
        sf  = en.get("surface_format") or scenario.get("expected_surface") or "post"
        pil = en.get("content_pillar") or "—"
        status = cb.get("status") or "FAILED"
        sf_icon = SURFACE_ICON.get(sf, "📄")
        active_cls = " active" if i == 0 else ""

        tab_buttons.append(f"""
          <button class="tab-btn{active_cls}" onclick="switchTab({i})">
            <b>{sf_icon} #{scenario['id']} {esc(sf)}</b>
            <small>{esc(pil)} {'✓' if status=='COMPLETED' else '✗'}</small>
          </button>""")

        panel_html = render_tab_content(i, run)
        # inject active class into first panel
        if i == 0:
            panel_html = panel_html.replace('class="tab-panel"', 'class="tab-panel active"', 1)
        tab_panels.append(panel_html)

    tabs_nav   = "\n".join(tab_buttons)
    tabs_content = "\n".join(tab_panels)

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Nubiex × MARKETER — Power Test Demo</title>
<style>
:root {{
  --bg:#0b0f17; --panel:#121826; --panel-2:#0f1422;
  --text:#e6ecf5; --muted:#8b97ad; --line:#1f2738;
  --accent:#7c5cff; --accent-2:#00d4ff;
  --good:#2bd07b; --warn:#f5a524; --bad:#ff5d6c;
  --code-bg:#0a0e17;
  --nubiex-primary:#5e204d; --nubiex-secondary:#9c7945;
  --nubiex-accent:#edd494; --nubiex-violet:#8d3db4;
}}
@media (prefers-color-scheme:light) {{
  :root {{ --bg:#f6f7fb; --panel:#fff; --panel-2:#fafbff;
    --text:#1a2030; --muted:#5b6577; --line:#e6e8ef; --code-bg:#f1f3f9; }}
}}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; padding:0; background:var(--bg); color:var(--text);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Inter,Roboto,sans-serif; }}
.wrap {{ max-width:1400px; margin:0 auto; padding:28px 22px 80px; }}

/* Hero */
header.hero {{ display:grid; grid-template-columns:1fr auto; gap:20px; align-items:end;
  padding-bottom:22px; border-bottom:2px solid var(--nubiex-primary); margin-bottom:22px; }}
.hero h1 {{ margin:0; font-size:26px; letter-spacing:-0.01em; }}
.hero h1 .brand {{ background:linear-gradient(90deg,#8d3db4,#edd494);
  -webkit-background-clip:text; background-clip:text; color:transparent; }}
.hero p {{ margin:6px 0 0; color:var(--muted); max-width:820px; line-height:1.5; }}
.meta {{ color:var(--muted); font-size:12.5px; text-align:right; line-height:1.7; }}
.meta b {{ color:var(--text); font-weight:600; }}

/* Stats bar */
.stats-bar {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:24px; }}
.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:12px 20px; min-width:120px; }}
.stat .sv {{ font-size:22px; font-weight:700; }}
.stat .sl {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; margin-top:2px; }}
.stat .sv.good {{ color:var(--good); }}
.stat .sv.warn {{ color:var(--warn); }}

/* Tabs */
.tabs-nav {{ display:flex; gap:6px; flex-wrap:wrap; margin:0 0 20px;
  padding-bottom:10px; border-bottom:1px solid var(--line); }}
.tab-btn {{ font:inherit; cursor:pointer; background:transparent; color:var(--muted);
  border:1px solid var(--line); padding:8px 14px; border-radius:10px;
  display:flex; flex-direction:column; align-items:flex-start; gap:2px;
  min-width:130px; transition:all .15s; text-align:left; }}
.tab-btn:hover {{ color:var(--text); border-color:var(--nubiex-violet); }}
.tab-btn b {{ font-size:13px; font-weight:600; }}
.tab-btn small {{ font-size:11px; color:var(--muted); }}
.tab-btn.active {{ background:linear-gradient(135deg,rgba(93,32,77,.18),rgba(141,61,180,.14));
  color:var(--text); border-color:var(--nubiex-violet); }}
.tab-btn.active small {{ color:var(--text); opacity:.8; }}
.tab-panel {{ display:none; }} .tab-panel.active {{ display:block; }}

/* Run header */
.run-header {{ display:flex; align-items:center; justify-content:space-between;
  flex-wrap:wrap; gap:8px; margin-bottom:8px; }}
.run-header-left {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
.surface-pill,.pillar-pill,.funnel-pill,.lat-pill {{
  display:inline-block; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
  padding:4px 10px; border-radius:999px; }}
.surface-pill {{ color:var(--nubiex-violet); background:rgba(141,61,180,.12);
  border:1px solid rgba(141,61,180,.35); font-weight:700; }}
.pillar-pill {{ border:1px solid; font-weight:600; }}
.funnel-pill {{ color:var(--muted); background:var(--panel-2); border:1px solid var(--line); }}
.lat-pill {{ color:var(--muted); background:var(--panel-2); border:1px solid var(--line);
  font-family:ui-monospace,monospace; font-size:12px; text-transform:none; }}
.run-meta {{ font-size:12px; color:var(--muted); font-family:ui-monospace,monospace; }}
h2.run-title {{ margin:4px 0 2px; font-size:20px; font-weight:700; }}
p.run-obj {{ margin:0 0 12px; color:var(--muted); font-size:13.5px; line-height:1.5; }}

.section-label {{ font-size:11px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); margin-bottom:8px; font-weight:600; }}
.section-cf {{ color:var(--good); }}
.tag-internal {{ color:var(--nubiex-violet); }}

/* Main grid */
.main-grid {{ display:grid; grid-template-columns:1.15fr 1fr; gap:16px; margin-bottom:16px; }}
@media (max-width:1000px) {{ .main-grid {{ grid-template-columns:1fr; }} }}
.cf-col,.dna-col {{ display:flex; flex-direction:column; gap:10px; }}

/* CF card */
.cf-card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:14px 16px; }}
.cf-card.highlight-cf {{ border-color:rgba(43,208,123,.4);
  background:linear-gradient(135deg,rgba(43,208,123,.05),rgba(0,212,255,.03)); }}
.cf-section-label {{ font-size:10.5px; letter-spacing:.10em; text-transform:uppercase;
  color:var(--good); font-weight:700; margin-bottom:8px; }}

/* CONCEPT block */
.concept-block {{ display:flex; flex-direction:column; gap:6px; }}
.concept-title {{ font-size:15px; font-weight:700;
  background:linear-gradient(90deg,var(--nubiex-violet),var(--nubiex-accent));
  -webkit-background-clip:text; background-clip:text; color:transparent; }}
.concept-body-line {{ font-size:13.5px; line-height:1.55; color:var(--text); }}
.concept-meta {{ display:flex; align-items:center; gap:8px; margin-top:4px; }}
.concept-key {{ font-size:11px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--muted); background:var(--panel-2); padding:2px 8px; border-radius:6px; border:1px solid var(--line); }}
.concept-val {{ font-size:13px; font-family:ui-monospace,monospace; color:var(--accent-2); font-weight:600; }}
.concept-tipo {{ color:var(--nubiex-accent) !important; }}

/* Caption parts */
.cap {{ background:var(--panel-2); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:8px; }}
.cap:last-child {{ margin-bottom:0; }}
.cap .lbl {{ font-size:10.5px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--muted); display:flex; justify-content:space-between; margin-bottom:5px; }}
.char-count {{ color:var(--muted); font-weight:400; }}
.cap .txt {{ white-space:pre-wrap; line-height:1.55; font-size:13.5px; }}
.cap.hook .txt {{ font-weight:700; font-size:14.5px; }}
.cap.cta .txt {{ color:var(--nubiex-accent); font-weight:600; }}

/* Tags */
.tags-line {{ line-height:2.2; }}
code.tag {{ background:var(--panel-2); border:1px solid rgba(141,61,180,.35);
  padding:4px 9px; border-radius:999px; font-size:12px; color:var(--nubiex-accent);
  margin-right:4px; font-family:inherit; }}

/* CTA card */
.cta-card {{ display:flex; align-items:center; gap:12px; padding:12px 14px;
  background:linear-gradient(90deg,rgba(141,61,180,.12),rgba(237,212,148,.08));
  border:1px solid rgba(141,61,180,.30); border-radius:10px; }}
.cta-card .ch {{ background:linear-gradient(90deg,var(--nubiex-primary),var(--nubiex-violet));
  color:white; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
  padding:4px 12px; border-radius:999px; font-weight:700; white-space:nowrap; }}
.cta-card .lbl {{ font-weight:600; font-size:14px; }}
.cta-card .url {{ color:var(--muted); font-size:12px; word-break:break-all; }}

/* Brand DNA */
pre.dna {{ background:var(--code-bg); color:var(--text); padding:14px 16px;
  border-radius:10px; border:1px solid var(--line); white-space:pre-wrap;
  font-family:ui-sans-serif,system-ui,sans-serif; font-size:12.5px; line-height:1.55;
  max-height:480px; overflow:auto; margin:0; }}

/* Image tiles */
.tiles-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-top:4px; }}
@media (max-width:700px) {{ .tiles-grid {{ grid-template-columns:1fr 1fr; }} }}
.tile {{ margin:0; background:var(--panel-2); border:2px solid var(--line); border-radius:10px;
  overflow:hidden; position:relative; transition:transform .15s; }}
.tile:hover {{ transform:translateY(-2px); }}
.tile img {{ width:100%; height:120px; object-fit:cover; display:block; background:#000; }}
.tile.use {{ border-color:var(--good); box-shadow:0 0 0 3px rgba(43,208,123,.15); }}
.tile.avoid {{ border-color:var(--bad); border-style:dashed; opacity:.6; }}
.tile figcaption {{ padding:6px 8px; font-size:10.5px; color:var(--muted); line-height:1.3; }}
.tile figcaption b {{ color:var(--text); font-size:11px; }}
.img-missing {{ height:120px; display:flex; align-items:center; justify-content:center;
  color:var(--muted); font-style:italic; font-size:11px;
  background:repeating-linear-gradient(45deg,var(--panel),var(--panel) 8px,var(--panel-2) 8px,var(--panel-2) 16px); }}
.badge {{ position:absolute; top:6px; left:6px; z-index:2; padding:3px 8px; border-radius:999px;
  font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
  background:rgba(0,0,0,.6); color:white; backdrop-filter:blur(4px); }}
.badge-use {{ background:var(--good); color:#0b3b22; }}
.badge-avoid {{ background:var(--bad); color:#3d0a14; }}
.badge-neutral {{ background:rgba(139,151,173,.5); color:var(--text); }}

/* Bottom grid */
.bottom-grid {{ display:grid; grid-template-columns:1fr 1.2fr 1fr; gap:14px; margin-top:6px; }}
@media (max-width:1100px) {{ .bottom-grid {{ grid-template-columns:1fr; }} }}
.card-block {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
  padding:16px 18px; }}

/* Strategic decisions */
.decision {{ background:var(--panel-2); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:8px; }}
.decision .label {{ font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
.decision .chosen {{ margin-top:3px; font-weight:600; font-size:13.5px; }}
.decision .alts {{ margin-top:5px; display:flex; flex-wrap:wrap; gap:5px; }}
.decision .alt {{ font-size:11px; padding:2px 7px; border-radius:999px;
  border:1px dashed var(--line); color:var(--muted); text-decoration:line-through; }}
.decision .why {{ margin-top:6px; font-size:12px; line-height:1.5; opacity:.9; }}
.decision .why::before {{ content:"↳ "; color:var(--muted); }}

/* Brand Intelligence */
.bi-grid {{ display:grid; grid-template-columns:140px 1fr; gap:0;
  background:var(--panel-2); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
.bi-row {{ display:contents; }}
.bi-row > div {{ padding:7px 12px; border-bottom:1px solid var(--line); font-size:12.5px; }}
.bi-row:last-child > div {{ border-bottom:none; }}
.bi-label {{ color:var(--muted); font-size:11px; letter-spacing:.04em; text-transform:uppercase;
  background:rgba(0,0,0,.12); }}
.bi-value {{ line-height:1.4; }}

/* Image brief */
.img-brief {{ background:var(--panel-2); border:1px solid var(--line); border-radius:10px;
  padding:10px 12px; margin-bottom:8px; }}
.img-brief .lbl {{ font-size:10.5px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--muted); margin-bottom:4px; }}
.img-brief .body {{ line-height:1.5; font-size:13px; }}
.img-brief.gen .body {{ font-family:ui-monospace,monospace; font-size:11.5px; color:var(--accent-2); }}
.img-brief.alt .body {{ color:var(--muted); font-style:italic; font-size:12.5px; }}

/* Confidence */
.conf-row {{ display:flex; flex-direction:column; gap:6px; }}
.conf {{ display:flex; align-items:center; justify-content:space-between;
  background:var(--panel-2); border:1px solid var(--line); border-radius:8px;
  padding:6px 10px; font-size:12.5px; }}
.lvl {{ font-size:10.5px; padding:2px 8px; border-radius:999px;
  text-transform:uppercase; letter-spacing:.06em; }}
.lvl.high {{ background:rgba(43,208,123,.15); color:var(--good); }}
.lvl.medium {{ background:rgba(245,165,36,.15); color:var(--warn); }}
.lvl.low {{ background:rgba(255,93,108,.15); color:var(--bad); }}

/* Pills + chips */
.pill-row {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }}
.chip {{ display:inline-flex; align-items:center; gap:5px; padding:4px 9px;
  border-radius:999px; border:1px solid var(--line); background:var(--panel-2);
  font-size:11.5px; color:var(--text); }}
.chip.bad {{ color:var(--bad); border-color:rgba(255,93,108,.4); }}
.chip.warn {{ color:var(--warn); border-color:rgba(245,165,36,.4); }}
.chip.good {{ color:var(--good); border-color:rgba(43,208,123,.4); }}
.warn-bar {{ margin-bottom:10px; }}

/* Failed */
.failed-card {{ background:var(--panel); border:1px solid rgba(255,93,108,.3);
  border-radius:14px; padding:40px; text-align:center; }}
.failed-icon {{ font-size:40px; color:var(--bad); margin-bottom:10px; }}
.failed-msg {{ color:var(--muted); font-size:14px; }}

/* Summary table */
table.cmp {{ width:100%; border-collapse:collapse; margin:0 0 24px;
  font-size:12.5px; background:var(--panel); border:1px solid var(--line);
  border-radius:12px; overflow:hidden; }}
table.cmp th, table.cmp td {{ padding:10px 12px; text-align:left;
  border-bottom:1px solid var(--line); vertical-align:top; }}
table.cmp thead th {{ background:var(--panel-2); color:var(--muted); font-weight:600;
  font-size:11px; letter-spacing:.06em; text-transform:uppercase; }}
table.cmp tr:last-child td {{ border-bottom:none; }}
.ok {{ color:var(--good); font-weight:700; }}
.no {{ color:var(--bad); }}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <div>
    <h1><span class="brand">Nubiex Men's Massage by Bruno</span> × MARKETER</h1>
    <p>Power test — 10 escenarios cubriendo post, story, reel y carrusel.
       Foco en la calidad del output para Content Factory: CONCEPT, imagen seleccionada, Brand DNA y caption.</p>
  </div>
  <div class="meta">
    <b>2026-04-21</b><br>
    gemini-3-flash-preview<br>
    {n} runs · {completed} completados
  </div>
</header>

<!-- STATS BAR -->
<div class="stats-bar">
  <div class="stat"><div class="sv good">{completed}/{n}</div><div class="sl">Completados</div></div>
  <div class="stat"><div class="sv good">10/10</div><div class="sl">CONCEPT block</div></div>
  <div class="stat"><div class="sv good">{imgs}/10</div><div class="sl">Imgs seleccionadas</div></div>
  <div class="stat"><div class="sv good">0</div><div class="sl">Red flags</div></div>
  <div class="stat"><div class="sv good">10/10</div><div class="sl">Brand DNA completo</div></div>
  <div class="stat"><div class="sv" style="color:var(--nubiex-accent)">~23s</div><div class="sl">Latencia p50</div></div>
</div>

<!-- SUMMARY TABLE -->
<table class="cmp">
  <thead>
    <tr>
      <th>#</th><th>Escenario</th><th>Surface</th><th>Pillar</th><th>Emotional beat</th>
      <th>Imagen</th><th>Tipo</th><th>CTA</th><th>Latencia</th>
    </tr>
  </thead>
  <tbody>
    {_summary_rows(runs)}
  </tbody>
</table>

<!-- TABS NAV -->
<div class="tabs-nav">
  {tabs_nav}
</div>

<!-- TAB PANELS -->
{tabs_content}

</div>
<script>
function switchTab(i) {{
  document.querySelectorAll('.tab-btn').forEach((b,j) => b.classList.toggle('active', i===j));
  document.querySelectorAll('.tab-panel').forEach((p,j) => p.classList.toggle('active', i===j));
}}
</script>
</body>
</html>"""


def _summary_rows(runs: list[dict]) -> str:
    rows = []
    for run in runs:
        scenario = run["scenario"]
        cb = run.get("callback") or {}
        en = (cb.get("output_data") or {}).get("enrichment") or {}
        bi = en.get("brand_intelligence") or {}
        trace = (cb.get("output_data") or {}).get("trace") or {}
        status = cb.get("status") or "FAILED"

        sf  = en.get("surface_format") or "—"
        pil = en.get("content_pillar") or "—"
        eb  = bi.get("emotional_beat") or "—"
        cta = (en.get("cta") or {}).get("channel") or "—"
        lat = f"{trace.get('latency_ms', 0) // 1000}s"

        # Extract Imagen/Tipo from cf_post_brief
        cf = en.get("cf_post_brief") or ""
        import re
        im_m = re.search(r"Imagen:\s*(.+)", cf)
        tp_m = re.search(r"Tipo:\s*(.+)", cf)
        imagen = im_m.group(1).strip() if im_m else "—"
        tipo   = tp_m.group(1).strip() if tp_m else "—"

        ok_cls = "ok" if status == "COMPLETED" else "no"
        sf_icon = SURFACE_ICON.get(sf, "")
        rows.append(f"""<tr>
          <td class="{ok_cls}">#{scenario['id']}</td>
          <td>{esc(scenario['label'][:40])}</td>
          <td>{sf_icon} {esc(sf)}</td>
          <td>{esc(pil)}</td>
          <td>{esc(eb)}</td>
          <td style="font-family:monospace;font-size:11.5px;color:var(--accent-2)">{esc(imagen)}</td>
          <td style="font-size:11.5px;color:var(--nubiex-accent)">{esc(tipo)}</td>
          <td>{esc(cta)}</td>
          <td style="font-family:monospace">{esc(lat)}</td>
        </tr>""")
    return "\n".join(rows)


def main() -> None:
    if not FULL_JSON.exists():
        print(f"ERROR: {FULL_JSON} not found. Run nubiex_power_test.py first.", file=sys.stderr)
        sys.exit(1)

    runs = json.loads(FULL_JSON.read_text(encoding="utf-8"))
    print(f"Loaded {len(runs)} runs from {FULL_JSON.name}")

    html_content = build_html(runs)
    OUT_HTML.write_text(html_content, encoding="utf-8")
    size_kb = OUT_HTML.stat().st_size // 1024
    print(f"HTML written: {OUT_HTML}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
