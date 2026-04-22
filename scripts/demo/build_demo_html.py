"""Generate docs/examples/runs/marketer_demo.html from docs/examples/runs/<run>.json (PostEnrichment v2).

Embeds the JSON so the HTML works opened directly from disk (no fetch / CORS).

Usage:
  python scripts/demo/build_demo_html.py                       # uses docs/examples/runs/casa_maruja_run.json
  python scripts/demo/build_demo_html.py --in docs/examples/runs/x.json
  python scripts/demo/build_demo_html.py --out docs/examples/runs/demo.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>MARKETER — Post Enrichment v2</title>
<style>
  :root {
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
    --in: #00d4ff;
    --out: #7c5cff;
    --merge: #2bd07b;
    --code-bg: #0a0e17;
    --string: #a5d6ff;
    --number: #f6c177;
    --bool: #ff7eb6;
    --null: #c4a7e7;
    --key: #7ee787;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f6f7fb;
      --panel: #ffffff;
      --panel-2: #fafbff;
      --text: #1a2030;
      --muted: #5b6577;
      --line: #e6e8ef;
      --code-bg: #f1f3f9;
      --string: #0f6cbf;
      --number: #b3580a;
      --bool: #b6357a;
      --null: #6841c5;
      --key: #166534;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text);
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif; }
  a { color: var(--accent-2); }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 32px 24px 80px; }
  header.hero {
    display: grid; grid-template-columns: 1fr auto; gap: 24px; align-items: end;
    padding-bottom: 24px; border-bottom: 1px solid var(--line); margin-bottom: 28px;
  }
  .hero h1 { margin: 0; font-size: 28px; letter-spacing: -0.01em; }
  .hero h1 .grad {
    background: linear-gradient(90deg, var(--accent), var(--accent-2));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .hero p { margin: 6px 0 0; color: var(--muted); max-width: 780px; line-height: 1.5; }
  .meta { color: var(--muted); font-size: 13px; text-align: right; line-height: 1.6; }
  .meta b { color: var(--text); font-weight: 600; }

  .pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
    border: 1px solid var(--line); border-radius: 999px; font-size: 12px; color: var(--muted); }

  .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 16px 0 28px; }
  @media (max-width: 900px) { .grid-3 { grid-template-columns: 1fr; } }

  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; }
  .card h3 { margin: 0 0 8px; font-size: 14px; letter-spacing: 0.03em; text-transform: uppercase; color: var(--muted); }
  .card .v { font-size: 18px; font-weight: 600; }
  .card .v small { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 6px; }

  section.block { margin: 28px 0; }
  .section-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .section-head .num {
    width: 28px; height: 28px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 13px; color: #0b0f17;
  }
  .section-head h2 { margin: 0; font-size: 20px; letter-spacing: -0.01em; }
  .section-head .tag { margin-left: auto; font-size: 12px; color: var(--muted); border: 1px solid var(--line); padding: 4px 10px; border-radius: 999px; }
  .num.hero { background: linear-gradient(135deg, #f5a524, #ff7eb6); }
  .num.in { background: linear-gradient(135deg, var(--in), #6ad6ff); }
  .num.out { background: linear-gradient(135deg, var(--out), #b39bff); }
  .num.merge { background: linear-gradient(135deg, var(--merge), #88f0c0); }
  .lead { color: var(--muted); margin: 0 0 14px; line-height: 1.55; }

  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; }
  .panel-head { display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-bottom: 1px solid var(--line); background: var(--panel-2); }
  .panel-head .label { font-size: 12px; letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); }
  .panel-head .right { margin-left: auto; display: flex; gap: 8px; }
  .btn { font: inherit; font-size: 12px; cursor: pointer; background: transparent; color: var(--muted);
    border: 1px solid var(--line); padding: 4px 10px; border-radius: 8px; }
  .btn:hover { color: var(--text); border-color: var(--accent); }
  pre.json { margin: 0; padding: 16px; background: var(--code-bg); color: var(--text);
    overflow: auto; max-height: 540px; font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace;
    font-size: 12.5px; line-height: 1.55; }
  .json .k { color: var(--key); }
  .json .s { color: var(--string); }
  .json .n { color: var(--number); }
  .json .b { color: var(--bool); }
  .json .nu { color: var(--null); }

  /* Post proposal card */
  .post-card { background: var(--panel); border: 1px solid var(--line); border-radius: 18px; overflow: hidden; margin-bottom: 20px; }
  .post-head { padding: 22px 26px 14px; border-bottom: 1px solid var(--line); display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
  .surface-pill { display: inline-block; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--accent); background: rgba(124,92,255,0.12); border: 1px solid rgba(124,92,255,0.35);
    padding: 4px 10px; border-radius: 999px; }
  .pillar-pill { display: inline-block; font-size: 11px; letter-spacing: 0.10em; text-transform: uppercase;
    color: var(--accent-2); background: rgba(0,212,255,0.10); border: 1px solid rgba(0,212,255,0.30);
    padding: 4px 10px; border-radius: 999px; }
  .post-title { width:100%; margin: 10px 0 0; font-size: 26px; line-height: 1.25; letter-spacing: -0.01em; font-weight: 700; }
  .post-grid { display: grid; grid-template-columns: 1.3fr 1fr; gap: 0; }
  @media (max-width: 980px) { .post-grid { grid-template-columns: 1fr; } }
  .post-col { padding: 22px 26px; }
  .post-col + .post-col { border-left: 1px solid var(--line); }
  @media (max-width: 980px) {
    .post-col + .post-col { border-left: none; border-top: 1px solid var(--line); }
  }
  .post-section { margin: 0 0 18px; }
  .post-section:last-child { margin-bottom: 0; }
  .post-section h4 {
    margin: 0 0 6px; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted); display: flex; align-items: center; gap: 6px;
  }
  .post-section h4 .emoji { font-size: 14px; }
  .post-section p { margin: 0; line-height: 1.6; }

  /* Strategic decision blocks */
  .decision { background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; margin-bottom: 10px; }
  .decision .label { font-size: 11px; letter-spacing: 0.10em; text-transform: uppercase; color: var(--muted); }
  .decision .chosen { margin-top: 4px; font-weight: 600; font-size: 15px; color: var(--text); }
  .decision .alts { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; }
  .decision .alt { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: transparent;
    border: 1px dashed var(--line); color: var(--muted); text-decoration: line-through; }
  .decision .why { margin-top: 8px; font-size: 13px; color: var(--text); line-height: 1.55; opacity: 0.92; }
  .decision .why::before { content: "↳ "; color: var(--muted); }

  /* Caption blocks */
  .cap { background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px;
    padding: 12px 14px; margin-bottom: 10px; }
  .cap .lbl { font-size: 11px; letter-spacing: 0.10em; text-transform: uppercase; color: var(--muted); display:flex; justify-content:space-between; }
  .cap .lbl .len { font-variant-numeric: tabular-nums; }
  .cap .txt { margin-top: 6px; white-space: pre-wrap; line-height: 1.6; font-size: 14.5px;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
  .cap.hook .txt { font-weight: 600; }

  /* CTA */
  .cta-card { display:flex; align-items:center; gap:10px; padding: 12px 14px;
    background: linear-gradient(90deg, rgba(124,92,255,0.12), rgba(0,212,255,0.10));
    border: 1px solid rgba(124,92,255,0.30); border-radius: 12px; margin-bottom: 10px; }
  .cta-card .ch {
    background: linear-gradient(90deg, var(--accent), var(--accent-2));
    color: white; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 4px 10px; border-radius: 999px; font-weight: 700;
  }
  .cta-card .lbl { font-weight: 600; }
  .cta-card .url { color: var(--muted); font-size: 12px; word-break: break-all; }

  /* Image brief */
  .img-brief { background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px;
    padding: 12px 14px; margin-bottom: 10px; }
  .img-brief .lbl { font-size: 11px; letter-spacing: 0.10em; text-transform: uppercase; color: var(--muted); margin-bottom: 4px; }
  .img-brief .body { line-height: 1.55; font-size: 14px; }
  .img-brief.gen .body { font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace;
    font-size: 12.5px; color: var(--accent-2); }
  .img-brief.alt .body { color: var(--muted); font-style: italic; font-size: 13px; }

  /* Visual selection gallery */
  .img-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; margin-top: 10px; }
  .thumb { background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px; padding: 8px; }
  .thumb .t { font-size: 11px; color: var(--muted); word-break: break-all; line-height: 1.4; }
  .thumb .role { display: inline-block; font-size: 10px; padding: 2px 6px; border-radius: 999px;
    background: rgba(124,92,255,0.15); color: var(--accent); margin-bottom: 4px; margin-right: 4px; }
  .thumb.use { outline: 2px solid var(--good); outline-offset: -2px; }
  .thumb.avoid { opacity: 0.55; outline: 2px dashed var(--bad); outline-offset: -2px; }
  .thumb.reference { outline: 2px solid var(--accent-2); outline-offset: -2px; }

  .pill-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 999px;
    border: 1px solid var(--line); background: var(--panel-2); font-size: 12px; color: var(--text); }
  .chip.good { color: var(--good); border-color: rgba(43,208,123,0.4); }
  .chip.bad  { color: var(--bad);  border-color: rgba(255,93,108,0.4); }
  .chip.warn { color: var(--warn); border-color: rgba(245,165,36,0.4); }
  .chip.ghost { color: var(--muted); border-style: dashed; }

  .conf-row { display:grid; grid-template-columns: repeat(2,1fr); gap:10px; }
  @media (max-width: 700px) { .conf-row { grid-template-columns: 1fr; } }
  .conf { display:flex; align-items:center; justify-content:space-between;
    background: var(--panel-2); border:1px solid var(--line); border-radius:10px; padding:8px 12px; font-size:13px; }
  .conf b { font-weight:600; }
  .conf .lvl { font-size:11px; padding:2px 8px; border-radius:999px; text-transform:uppercase; letter-spacing:.08em; }
  .lvl.high   { background: rgba(43,208,123,0.15); color: var(--good); }
  .lvl.medium { background: rgba(245,165,36,0.15); color: var(--warn); }
  .lvl.low    { background: rgba(255,93,108,0.15); color: var(--bad); }

  details.acc { margin-top: 12px; }
  details.acc > summary { cursor: pointer; color: var(--muted); font-size: 13px; padding: 8px 12px;
    border: 1px dashed var(--line); border-radius: 10px; }
  details.acc[open] > summary { color: var(--text); border-style: solid; }

  footer { color: var(--muted); font-size: 12px; margin-top: 32px; text-align: center; }
</style>
</head>
<body>
<div class="wrap">

  <header class="hero">
    <div>
      <h1>MARKETER <span class="grad">post enrichment v2</span></h1>
      <p>One ROUTER dispatch, one MARKETER reply, one merged record. The agent reads the brief, gallery and live request,
        <b>compares alternatives, commits, and explains</b> — then hands a concrete post proposal to Content Factory:
        strategic decisions, structured caption, image brief, channel-aware CTA, hashtag direction, and anti-patterns.</p>
    </div>
    <div class="meta">
      <div><b>task_id</b> __TASK_ID__</div>
      <div><b>action_code</b> __ACTION_CODE__</div>
      <div><b>model</b> __MODEL__</div>
      <div><b>generated</b> __GENERATED__</div>
    </div>
  </header>

  <div class="grid-3">
    <div class="card">
      <h3>Status</h3>
      <div class="v">__STATUS__ <small>__SURFACE__ / __MODE__</small></div>
    </div>
    <div class="card">
      <h3>Latency</h3>
      <div class="v">__LATENCY__ ms <small>repair: __REPAIR__</small></div>
    </div>
    <div class="card">
      <h3>Gallery</h3>
      <div class="v">__GALLERY_ACCEPTED__ / __GALLERY_RAW__ <small>truncated: __TRUNCATED__</small></div>
    </div>
  </div>

  <!-- POST PROPOSAL -->
  <section class="block">
    <div class="section-head">
      <span class="num hero">!</span>
      <h2>Post proposal — strategic output</h2>
      <span class="tag">post_enrichment.v2</span>
    </div>
    <p class="lead">Anchored on brand_tokens, available_channels and brief_facts. Every load-bearing decision lists
      <em>chosen</em>, <em>alternatives considered</em>, and <em>rationale</em>.</p>

    <div class="post-card">
      <div class="post-head">
        <span class="surface-pill">__SURFACE_FORMAT__</span>
        <span class="pillar-pill">__CONTENT_PILLAR__</span>
        <h3 class="post-title">__TITLE__</h3>
      </div>
      <div class="post-grid">
        <div class="post-col">
          <div class="post-section">
            <h4><span class="emoji">&#127919;</span> Objective</h4>
            <p>__OBJECTIVE__</p>
          </div>
          <div class="post-section">
            <h4><span class="emoji">&#9878;</span> Strategic decisions</h4>
            __DECISIONS__
          </div>
          <div class="post-section">
            <h4><span class="emoji">&#10024;</span> Visual style notes</h4>
            <p>__VISUAL_STYLE_NOTES__</p>
          </div>
          __NARRATIVE_BLOCK__
          <div class="post-section">
            <h4><span class="emoji">&#128683;</span> Do not</h4>
            <div class="pill-row">__DO_NOT__</div>
          </div>
        </div>
        <div class="post-col">
          <div class="post-section">
            <h4><span class="emoji">&#128444;</span> Image brief</h4>
            <div class="img-brief">
              <div class="lbl">Concept</div>
              <div class="body">__IMG_CONCEPT__</div>
            </div>
            <div class="img-brief gen">
              <div class="lbl">Generation prompt</div>
              <div class="body">__IMG_PROMPT__</div>
            </div>
            <div class="img-brief alt">
              <div class="lbl">Alt text</div>
              <div class="body">__IMG_ALT__</div>
            </div>
          </div>
          <div class="post-section">
            <h4><span class="emoji">&#128221;</span> Caption</h4>
            <div class="cap hook">
              <div class="lbl"><span>Hook</span><span class="len">__LEN_HOOK__ ch</span></div>
              <div class="txt">__CAP_HOOK__</div>
            </div>
            <div class="cap body">
              <div class="lbl"><span>Body</span><span class="len">__LEN_BODY__ ch</span></div>
              <div class="txt">__CAP_BODY__</div>
            </div>
            <div class="cap cta">
              <div class="lbl"><span>CTA line</span><span class="len">__LEN_CTA__ ch</span></div>
              <div class="txt">__CAP_CTA__</div>
            </div>
          </div>
          <div class="post-section">
            <h4><span class="emoji">&#128073;</span> Call to action</h4>
            __CTA_CARD__
          </div>
          <div class="post-section">
            <h4><span class="emoji">&#127991;</span> Hashtag strategy</h4>
            <div class="pill-row">
              <span class="chip">intent · __HT_INTENT__</span>
              <span class="chip">volume · __HT_VOLUME__</span>
            </div>
            <div class="pill-row">__HT_THEMES__</div>
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:20px;">
      <h3>Visual selection</h3>
      <div class="pill-row" style="margin:6px 0 10px;">
        <span class="chip good">use · __RECOMMENDED_COUNT__</span>
        <span class="chip">reference · __REFERENCE_COUNT__</span>
        <span class="chip bad">avoid · __AVOID_COUNT__</span>
      </div>
      <div class="img-row">__IMG_ROW__</div>
    </div>

    <div class="card" style="margin-top:16px;">
      <h3>Confidence</h3>
      <div class="conf-row">
        <div class="conf"><b>surface_format</b><span class="lvl __CONF_SURFACE_CLS__">__CONF_SURFACE__</span></div>
        <div class="conf"><b>angle</b><span class="lvl __CONF_ANGLE_CLS__">__CONF_ANGLE__</span></div>
        <div class="conf"><b>palette_match</b><span class="lvl __CONF_PALETTE_CLS__">__CONF_PALETTE__</span></div>
        <div class="conf"><b>cta_channel</b><span class="lvl __CONF_CTA_CLS__">__CONF_CTA__</span></div>
      </div>
    </div>

    <div class="card" style="margin-top:16px;">
      <h3>Warnings &amp; trace</h3>
      <div class="pill-row">__WARNING_CHIPS__</div>
      <details class="acc"><summary>Show raw trace</summary>
        <pre class="json" id="trace-pre"></pre>
      </details>
    </div>
  </section>

  <!-- INPUT -->
  <section class="block">
    <div class="section-head">
      <span class="num in">1</span>
      <h2>Input — what ROUTER sends MARKETER</h2>
      <span class="tag">Contrato B · POST /tasks</span>
    </div>
    <p class="lead">The orchestrator dispatches one envelope: <code>client_request</code>, <code>context</code>,
      <code>action_execution_gates</code> (brief + image catalog), <code>agent_sequence</code>.</p>
    <div class="panel">
      <div class="panel-head">
        <span class="label">router_dispatch.body</span>
        <div class="right">
          <button class="btn" data-copy="input-pre">Copy</button>
          <button class="btn" data-toggle="input-pre">Collapse</button>
        </div>
      </div>
      <pre class="json" id="input-pre"></pre>
    </div>
  </section>

  <!-- OUTPUT -->
  <section class="block">
    <div class="section-head">
      <span class="num out">2</span>
      <h2>Output — what MARKETER returns</h2>
      <span class="tag">Contrato C · PATCH /api/v1/tasks/&lt;id&gt;/callback</span>
    </div>
    <p class="lead">A single callback body: <code>status</code>, the strategic <code>enrichment</code> (v2),
      <code>warnings</code>, and a <code>trace</code> with model + latency + gallery stats.</p>
    <div class="panel">
      <div class="panel-head">
        <span class="label">marketer_callback.body</span>
        <div class="right">
          <button class="btn" data-copy="output-pre">Copy</button>
          <button class="btn" data-toggle="output-pre">Collapse</button>
        </div>
      </div>
      <pre class="json" id="output-pre"></pre>
    </div>
  </section>

  <!-- MERGED -->
  <section class="block">
    <div class="section-head">
      <span class="num merge">3</span>
      <h2>Merged — orchestrator-side record after MARKETER</h2>
      <span class="tag">gate_responses + sequence_responses[marketer]</span>
    </div>
    <p class="lead">After the callback, ROUTER stores the gate responses and the marketer step output keyed by
      <code>step_code</code>. This is the shape the next agent will see in <code>agent_sequence.previous</code>.</p>
    <div class="panel">
      <div class="panel-head">
        <span class="label">router_record_after_step</span>
        <div class="right">
          <button class="btn" data-copy="merged-pre">Copy</button>
          <button class="btn" data-toggle="merged-pre">Collapse</button>
        </div>
      </div>
      <pre class="json" id="merged-pre"></pre>
    </div>
  </section>

  <footer>
    Generated from <code>docs/examples/runs/__SOURCE_FILENAME__</code> ·
    <a href="__SOURCE_FILENAME__">open raw JSON</a>
  </footer>
</div>

<script>
  const DATA = __DATA_JSON__;

  function escapeHtml(s) { return s.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
  function highlight(jsonStr) {
    const esc = escapeHtml(jsonStr);
    return esc.replace(
      /("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
      function (match) {
        let cls = 'n';
        if (/^"/.test(match)) {
          cls = /:$/.test(match) ? 'k' : 's';
        } else if (/true|false/.test(match)) cls = 'b';
        else if (/null/.test(match)) cls = 'nu';
        return '<span class="' + cls + '">' + match + '</span>';
      }
    );
  }
  function fillJson(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = highlight(JSON.stringify(value, null, 2));
  }

  fillJson('input-pre', DATA.router_dispatch);
  fillJson('output-pre', DATA.marketer_callback);
  fillJson('merged-pre', DATA.router_record_after_step);
  fillJson('trace-pre', DATA.marketer_callback.body.output_data && DATA.marketer_callback.body.output_data.trace || {});

  document.querySelectorAll('button[data-copy]').forEach(b => {
    b.addEventListener('click', async () => {
      const id = b.getAttribute('data-copy');
      const text = document.getElementById(id).innerText;
      try { await navigator.clipboard.writeText(text); b.textContent = 'Copied'; setTimeout(()=>b.textContent='Copy', 1200); }
      catch(e) { b.textContent = 'Copy failed'; }
    });
  });
  document.querySelectorAll('button[data-toggle]').forEach(b => {
    b.addEventListener('click', () => {
      const el = document.getElementById(b.getAttribute('data-toggle'));
      el.style.display = (el.style.display === 'none') ? '' : 'none';
      b.textContent = (el.style.display === 'none') ? 'Expand' : 'Collapse';
    });
  });
</script>
</body>
</html>
"""


def _html(text: object) -> str:
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _img_card(item: dict, use_urls: set, avoid_urls: set, reference_urls: set) -> str:
    url = item.get("url", "")
    name = item.get("name") or url.rsplit("/", 1)[-1]
    role = item.get("role") or "unknown"
    cls = ""
    if url in use_urls:
        cls = "use"
    elif url in avoid_urls:
        cls = "avoid"
    elif url in reference_urls:
        cls = "reference"
    chip = f'<span class="role">{_html(role)}</span>'
    if url in use_urls:
        chip += ' <span class="role" style="background:rgba(43,208,123,0.18);color:var(--good)">use</span>'
    elif url in avoid_urls:
        chip += ' <span class="role" style="background:rgba(255,93,108,0.18);color:var(--bad)">avoid</span>'
    elif url in reference_urls:
        chip += ' <span class="role" style="background:rgba(0,212,255,0.18);color:var(--in)">reference</span>'
    return (
        f'<div class="thumb {cls}">'
        f"  {chip}"
        f'  <div class="t"><b>{_html(name)}</b><br/>{_html(url)}</div>'
        f"</div>"
    )


def _gather_gallery(dispatch_body: dict) -> list[dict]:
    items: list[dict] = []
    gates = dispatch_body.get("payload", {}).get("action_execution_gates", {}) or {}
    for _code, gate in gates.items():
        resp = (gate or {}).get("response") or {}
        data = resp.get("data")
        if isinstance(data, list):
            items.extend(x for x in data if isinstance(x, dict) and x.get("url"))
        elif isinstance(data, dict):
            for key in ("items", "images", "assets", "media"):
                sub = data.get(key)
                if isinstance(sub, list):
                    items.extend(x for x in sub if isinstance(x, dict) and x.get("url"))
            brief_obj = data.get("brief")
            if isinstance(brief_obj, dict):
                fv = brief_obj.get("form_values") or {}
                for x in fv.get("FIELD_BRAND_MATERIAL") or []:
                    if isinstance(x, dict) and x.get("url"):
                        x = dict(x)
                        x.setdefault("role", "brand_asset")
                        items.append(x)
    seen: set[str] = set()
    unique: list[dict] = []
    for it in items:
        u = it.get("url")
        if u in seen:
            continue
        if u:
            seen.add(u)
        unique.append(it)
    return unique


def _decision_block(label: str, choice: dict | None) -> str:
    if not choice:
        return ""
    chosen = choice.get("chosen", "")
    rationale = choice.get("rationale", "")
    alts = choice.get("alternatives_considered") or []
    alts_html = "".join(f'<span class="alt">{_html(a)}</span>' for a in alts)
    alts_block = f'<div class="alts">{alts_html}</div>' if alts_html else ""
    return (
        f'<div class="decision">'
        f'  <div class="label">{_html(label)}</div>'
        f'  <div class="chosen">{_html(chosen)}</div>'
        f"  {alts_block}"
        f'  <div class="why">{_html(rationale)}</div>'
        f"</div>"
    )


def _cta_card(cta: dict) -> str:
    channel = (cta.get("channel") or "none").upper()
    label = cta.get("label") or ""
    url = cta.get("url_or_handle") or ""
    url_html = f'<span class="url">→ {_html(url)}</span>' if url else ""
    return (
        f'<div class="cta-card">'
        f'  <span class="ch">{_html(channel)}</span>'
        f'  <span class="lbl">{_html(label)}</span>'
        f"  {url_html}"
        f"</div>"
    )


def _conf_class(level: str) -> str:
    lvl = (level or "medium").lower()
    return lvl if lvl in {"high", "medium", "low"} else "medium"


def render(merged: dict, source_filename: str) -> str:
    dispatch = merged["router_dispatch"]["body"]
    callback_body = merged["marketer_callback"]["body"]
    output = callback_body.get("output_data") or {}
    enrich = output.get("enrichment") or {}
    vs = enrich.get("visual_selection") or {}
    trace = output.get("trace") or {}
    warnings = output.get("warnings") or []

    use_urls = set(vs.get("recommended_asset_urls") or [])
    avoid_urls = set(vs.get("avoid_asset_urls") or [])
    ref_urls = set(vs.get("recommended_reference_urls") or [])
    gallery_items = _gather_gallery(dispatch)
    img_html = (
        "".join(_img_card(it, use_urls, avoid_urls, ref_urls) for it in gallery_items)
        or '<div class="thumb"><div class="t">No gallery items.</div></div>'
    )

    chips: list[str] = []
    for w in warnings:
        code = (w or {}).get("code", "")
        cls = "chip"
        if code in (
            "claim_not_in_brief",
            "palette_mismatch",
            "cta_channel_invalid",
            "cta_url_invalid",
            "visual_hallucinated",
            "field_missing",
            "brief_missing",
            "gallery_empty",
            "gallery_all_filtered",
            "prior_post_missing",
        ):
            cls = "chip bad"
        elif code in (
            "price_not_in_brief",
            "caption_length_exceeded",
            "do_not_truncated",
            "surface_format_overridden",
        ):
            cls = "chip warn"
        elif code == "schema_repair_used":
            cls = "chip good"
        msg = (w or {}).get("message", "")
        title = f' title="{_html(msg)}"' if msg else ""
        chips.append(f'<span class="{cls}"{title}>{_html(code)}</span>')
    chips_html = "".join(chips) or '<span class="chip good">no warnings</span>'

    decisions = enrich.get("strategic_decisions") or {}
    decisions_html = (
        _decision_block("Surface format", decisions.get("surface_format"))
        + _decision_block("Angle", decisions.get("angle"))
        + _decision_block("Voice", decisions.get("voice"))
    )

    image = enrich.get("image") or {}
    caption = enrich.get("caption") or {}
    cta = enrich.get("cta") or {}
    hashtag = enrich.get("hashtag_strategy") or {}
    confidence = enrich.get("confidence") or {}

    do_not_items = enrich.get("do_not") or []
    do_not_html = (
        "".join(f'<span class="chip ghost">{_html(x)}</span>' for x in do_not_items)
        or '<span class="chip ghost">—</span>'
    )

    themes = hashtag.get("themes") or []
    themes_html = (
        "".join(f'<span class="chip">#{_html(t)}</span>' for t in themes)
        or '<span class="chip ghost">no themes</span>'
    )

    narrative = enrich.get("narrative_connection")
    narrative_block = ""
    if narrative:
        narrative_block = (
            '<div class="post-section">'
            '<h4><span class="emoji">&#128279;</span> Narrative connection</h4>'
            f"<p>{_html(narrative)}</p>"
            "</div>"
        )

    surface_format = (enrich.get("surface_format") or "post").upper()
    pillar = (enrich.get("content_pillar") or "—").replace("_", " ").upper()

    hook = caption.get("hook", "")
    body = caption.get("body", "")
    cta_line = caption.get("cta_line", "")

    replacements = {
        "__TASK_ID__": _html(dispatch.get("task_id", "")),
        "__ACTION_CODE__": _html(dispatch.get("action_code", "")),
        "__MODEL__": _html(trace.get("gemini_model", "")),
        "__GENERATED__": _html(merged.get("transcript", {}).get("generated_at", "")),
        "__STATUS__": _html(callback_body.get("status", "")),
        "__SURFACE__": _html(trace.get("surface", "")),
        "__MODE__": _html(trace.get("mode", "")),
        "__LATENCY__": str(trace.get("latency_ms", "—")),
        "__REPAIR__": "yes" if trace.get("repair_attempted") else "no",
        "__GALLERY_ACCEPTED__": str(
            (trace.get("gallery_stats") or {}).get("accepted_count", 0)
        ),
        "__GALLERY_RAW__": str((trace.get("gallery_stats") or {}).get("raw_count", 0)),
        "__TRUNCATED__": "yes"
        if (trace.get("gallery_stats") or {}).get("truncated")
        else "no",
        "__SURFACE_FORMAT__": _html(surface_format),
        "__CONTENT_PILLAR__": _html(pillar),
        "__TITLE__": _html(enrich.get("title", "")),
        "__OBJECTIVE__": _html(enrich.get("objective", "")),
        "__DECISIONS__": decisions_html
        or '<p class="lead">No structured decisions.</p>',
        "__VISUAL_STYLE_NOTES__": _html(enrich.get("visual_style_notes", "")),
        "__NARRATIVE_BLOCK__": narrative_block,
        "__DO_NOT__": do_not_html,
        "__IMG_CONCEPT__": _html(image.get("concept", "")),
        "__IMG_PROMPT__": _html(image.get("generation_prompt", "")),
        "__IMG_ALT__": _html(image.get("alt_text", "")),
        "__CAP_HOOK__": _html(hook),
        "__CAP_BODY__": _html(body),
        "__CAP_CTA__": _html(cta_line),
        "__LEN_HOOK__": str(len(hook)),
        "__LEN_BODY__": str(len(body)),
        "__LEN_CTA__": str(len(cta_line)),
        "__CTA_CARD__": _cta_card(cta),
        "__HT_INTENT__": _html(hashtag.get("intent", "—")),
        "__HT_VOLUME__": str(hashtag.get("suggested_volume", 0)),
        "__HT_THEMES__": themes_html,
        "__RECOMMENDED_COUNT__": str(len(use_urls)),
        "__REFERENCE_COUNT__": str(len(ref_urls)),
        "__AVOID_COUNT__": str(len(avoid_urls)),
        "__IMG_ROW__": img_html,
        "__CONF_SURFACE__": _html(confidence.get("surface_format", "medium")),
        "__CONF_SURFACE_CLS__": _conf_class(confidence.get("surface_format", "medium")),
        "__CONF_ANGLE__": _html(confidence.get("angle", "medium")),
        "__CONF_ANGLE_CLS__": _conf_class(confidence.get("angle", "medium")),
        "__CONF_PALETTE__": _html(confidence.get("palette_match", "medium")),
        "__CONF_PALETTE_CLS__": _conf_class(confidence.get("palette_match", "medium")),
        "__CONF_CTA__": _html(confidence.get("cta_channel", "medium")),
        "__CONF_CTA_CLS__": _conf_class(confidence.get("cta_channel", "medium")),
        "__WARNING_CHIPS__": chips_html,
        "__SOURCE_FILENAME__": _html(source_filename),
        "__DATA_JSON__": json.dumps(merged, ensure_ascii=False),
    }

    html = HTML_TEMPLATE
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--in", dest="input", default="docs/examples/runs/casa_maruja_run.json"
    )
    parser.add_argument(
        "--out", dest="output", default="docs/examples/runs/marketer_demo.html"
    )
    args = parser.parse_args()
    src = (ROOT / args.input).resolve()
    dst = (ROOT / args.output).resolve()
    merged = json.loads(src.read_text(encoding="utf-8"))
    html = render(merged, src.name)
    dst.write_text(html, encoding="utf-8")
    print(f"Wrote {dst} ({dst.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
