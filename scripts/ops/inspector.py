#!/usr/bin/env python3
"""Inspector — render the latest MARKETER runs as a single HTML artifact.

Reads from Postgres (jobs + strategies + raw_briefs) and writes a self-contained
dark-theme dashboard at reports/inspector.html. Always overwrites the same file
so there's no legacy clutter — just refresh the browser tab after every run.

Usage:
    python scripts/ops/inspector.py                    # last 5 runs
    python scripts/ops/inspector.py --limit 10
    python scripts/ops/inspector.py --output path.html

Wire into smoke runs: scripts/ops/db_e2e_smoke.py calls this at the end.

Layout per run (top → bottom):
  1. Header (status, action, latency, strategy version)
  2. CONTENT FACTORY payload (highlighted — what CF actually consumes)
  3. Client + request
  4. Two columns: Brand intelligence (strategy) | Caption / CTA / Hashtags
  5. Image direction (concept / alt / generation_prompt)
  6. Visual selection (recommended + avoid URLs, slide count for carousels)
  7. Strategic decisions (collapsible)
  8. Internal flags: do_not / confidence / narrative
  9. Raw_brief metadata + full job.output JSON (collapsible)
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("DB_USE_NULL_POOL", "true")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from marketer.config import load_settings  # noqa: E402
from marketer.pg_url import normalize_sync_psycopg_url  # noqa: E402
from marketer.db.models import Job, RawBrief, Strategy  # noqa: E402

DEFAULT_OUTPUT = ROOT / "reports" / "inspector.html"
DEFAULT_LIMIT = 5


def esc(value: Any) -> str:
    if value is None:
        return "—"
    return html_lib.escape(str(value), quote=True)


def truncate(value: str | None, max_len: int = 400) -> str:
    if not value:
        return ""
    s = str(value)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def fetch_runs(limit: int) -> list[dict[str, Any]]:
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not configured; cannot render inspector.")
    engine = create_engine(normalize_sync_psycopg_url(settings.database_url))
    runs: list[dict[str, Any]] = []
    with Session(engine) as session:
        rows = (
            session.execute(select(Job).order_by(Job.created_at.desc()).limit(limit))
            .scalars()
            .all()
        )
        for job in rows:
            strat = session.execute(
                select(Strategy).where(Strategy.id == job.strategy_id)
            ).scalar_one_or_none()
            brief = (
                session.execute(
                    select(RawBrief).where(RawBrief.id == job.raw_brief_id)
                ).scalar_one_or_none()
                if job.raw_brief_id
                else None
            )
            runs.append({"job": job, "strategy": strat, "raw_brief": brief})
    return runs


# ─── CSS ─────────────────────────────────────────────────────────────────────

CSS = """
:root {
  --bg:#0b0f17; --panel:#121826; --panel-2:#0f1422;
  --text:#e6ecf5; --muted:#8b97ad; --line:#1f2738;
  --accent:#7c5cff; --a2:#00d4ff;
  --good:#2bd07b; --warn:#f5a524; --bad:#ff5d6c;
  --cf:#2bd07b;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--text);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.5}
.wrap{max-width:1500px;margin:0 auto;padding:24px 20px 80px}
header{display:flex;align-items:baseline;justify-content:space-between;
  padding-bottom:14px;border-bottom:2px solid var(--accent);margin-bottom:24px}
header h1{font-size:22px;letter-spacing:.02em}
header h1 .brand{background:linear-gradient(90deg,var(--accent),var(--a2));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.hdr-meta{font-size:12px;color:var(--muted);text-align:right;line-height:1.7}
.hdr-meta b{color:var(--text)}
.empty{text-align:center;padding:60px 20px;color:var(--muted);font-size:14px}
.run{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:18px 20px;margin-bottom:20px}
.run-head{display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:10px;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.run-id{font-family:ui-monospace,monospace;font-size:11px;color:var(--muted)}
.pill{display:inline-block;font-size:11px;padding:2px 9px;border-radius:999px;
  letter-spacing:.06em;text-transform:uppercase;font-weight:600}
.pill.ok{color:var(--good);background:rgba(43,208,123,.10);border:1px solid rgba(43,208,123,.30)}
.pill.bad{color:var(--bad);background:rgba(255,93,108,.10);border:1px solid rgba(255,93,108,.30)}
.pill.action{color:var(--a2);background:rgba(0,212,255,.10);border:1px solid rgba(0,212,255,.30)}
.pill.surface{color:var(--accent);background:rgba(124,92,255,.10);border:1px solid rgba(124,92,255,.30)}
.pill.muted{color:var(--muted);background:var(--panel-2);border:1px solid var(--line)}
.pill.cf{color:var(--cf);background:rgba(43,208,123,.12);border:1px solid rgba(43,208,123,.4);font-weight:700}
.pill-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}

/* CONTENT FACTORY highlight section */
.cf-block{background:rgba(43,208,123,.05);border:1px solid rgba(43,208,123,.35);
  border-radius:10px;padding:14px 16px;margin-bottom:18px;position:relative}
.cf-badge{position:absolute;top:-9px;left:14px;background:var(--bg);
  padding:0 8px;font-size:10px;letter-spacing:.12em;font-weight:700;color:var(--cf)}
.cf-grid{display:grid;grid-template-columns:140px 1fr;gap:8px 14px;font-size:12.5px}
.cf-k{color:var(--cf);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;font-weight:700;padding-top:3px}
.cf-v{color:var(--text);word-break:break-word;white-space:pre-wrap;line-height:1.6}
.cf-v.mono{font-family:ui-monospace,monospace;font-size:11.5px}
.cf-v .url{color:var(--a2);font-family:ui-monospace,monospace;font-size:11px;display:block;padding:1px 0}

.section{margin-top:16px}
.section h3{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  margin-bottom:8px;font-weight:700}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:16px}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
.kv{display:grid;grid-template-columns:130px 1fr;gap:6px 14px;font-size:13px;margin-bottom:8px}
.kv .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding-top:2px}
.kv .v{color:var(--text);word-break:break-word}
.block{background:var(--panel-2);border:1px solid var(--line);border-radius:8px;
  padding:10px 12px;margin-bottom:8px;font-size:13px;white-space:pre-wrap;line-height:1.6}
.block.mono{font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap}
.block.label{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);margin-bottom:4px;font-weight:600}
.tag{display:inline-block;background:var(--panel-2);border:1px solid var(--line);
  border-radius:4px;padding:2px 7px;margin:2px 4px 2px 0;font-size:11px;color:var(--a2);font-family:ui-monospace,monospace}
.dont{display:inline-block;background:rgba(255,93,108,.08);border:1px solid rgba(255,93,108,.3);
  border-radius:4px;padding:2px 7px;margin:2px 4px 2px 0;font-size:11px;color:var(--bad)}
.conf-row{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.conf-chip{font-size:10.5px;padding:2px 7px;border-radius:4px;letter-spacing:.04em;text-transform:uppercase;font-weight:700}
.conf-high{color:var(--good);background:rgba(43,208,123,.10);border:1px solid rgba(43,208,123,.30)}
.conf-medium{color:var(--warn);background:rgba(245,165,36,.10);border:1px solid rgba(245,165,36,.30)}
.conf-low{color:var(--bad);background:rgba(255,93,108,.10);border:1px solid rgba(255,93,108,.30)}

/* Strategic-decisions choice rows */
.choice{padding:10px 12px;background:var(--panel-2);border:1px solid var(--line);
  border-radius:8px;margin-bottom:8px}
.choice .ch-head{display:flex;align-items:baseline;gap:10px;margin-bottom:6px}
.choice .ch-key{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:700}
.choice .ch-val{font-size:14px;color:var(--text);font-weight:600}
.choice .ch-rationale{font-size:12.5px;color:var(--text);line-height:1.55}
.choice .ch-alts{font-size:11px;color:var(--muted);margin-top:6px;font-style:italic}

/* Image / visual selection */
.url-list{font-family:ui-monospace,monospace;font-size:11px}
.url-list .url{color:var(--a2);display:block;padding:2px 0;word-break:break-all}
.url-list .url.avoid{color:var(--bad)}
.slide-count{display:inline-block;font-size:11px;font-weight:700;
  background:rgba(124,92,255,.15);color:var(--accent);padding:1px 8px;border-radius:4px;margin-left:8px}

details{margin-top:10px}
summary{cursor:pointer;color:var(--muted);font-size:11px;text-transform:uppercase;
  letter-spacing:.05em;padding:6px 0;user-select:none;font-weight:700}
summary:hover{color:var(--text)}
"""


# ─── Section renderers ───────────────────────────────────────────────────────


def render_cf_payload(output: dict[str, Any], enrichment: dict[str, Any]) -> str:
    """Highlight what Content Factory actually receives.

    Falls back to deriving from enrichment if the optional CFPayload `data`
    block is absent (HEAD schema doesn't include it).
    """
    out_data = output.get("output_data") or {}
    cf = out_data.get("data") or {}

    recs = (
        cf.get("resources")
        or (enrichment.get("visual_selection") or {}).get("recommended_asset_urls")
        or []
    )
    total_items = cf.get("total_items") or (
        len(recs) if enrichment.get("surface_format") == "carousel" and recs else 1
    )
    client_dna = cf.get("client_dna") or enrichment.get("brand_dna") or ""
    client_request = cf.get("client_request") or enrichment.get("cf_post_brief") or ""

    resources_html = (
        "".join(f'<span class="url">{esc(u)}</span>' for u in recs) if recs else "—"
    )

    return (
        '<section class="cf-block">'
        '<span class="cf-badge">→ CONTENT FACTORY PAYLOAD</span>'
        '<div class="cf-grid">'
        f'<div class="cf-k">total_items</div><div class="cf-v"><b>{total_items}</b></div>'
        f'<div class="cf-k">resources</div><div class="cf-v mono">{resources_html}</div>'
        f'<div class="cf-k">client_request</div><div class="cf-v">{esc(client_request)}</div>'
        "</div>"
        f"<details><summary>client_dna ({len(client_dna)} chars)</summary>"
        f'<div class="block mono">{esc(client_dna)}</div></details>'
        "</section>"
    )


def render_brand_intelligence(bi: dict[str, Any]) -> str:
    keys = [
        ("Taxonomy", "business_taxonomy"),
        ("Voice", "voice_register"),
        ("Emotion", "emotional_beat"),
        ("Audience", "audience_persona"),
        ("Edge", "unfair_advantage"),
        ("Device", "rhetorical_device"),
        ("Funnel", "funnel_stage_target"),
        ("Risks", "risk_flags"),
    ]
    parts = ['<div class="kv">']
    for label, key in keys:
        value = bi.get(key)
        if isinstance(value, list):
            value = ", ".join(value) if value else "—"
        parts.append(
            f'<div class="k">{esc(label)}</div><div class="v">{esc(value)}</div>'
        )
    parts.append("</div>")
    return "".join(parts)


def render_caption_and_hashtags(enrichment: dict[str, Any]) -> str:
    title = enrichment.get("title") or "—"
    pillar = enrichment.get("content_pillar") or "—"
    surface_format = enrichment.get("surface_format") or "—"
    objective = enrichment.get("objective") or ""
    caption = enrichment.get("caption") or {}
    cta = enrichment.get("cta") or {}
    hashtags = (enrichment.get("hashtag_strategy") or {}).get("tags") or []

    parts = []
    parts.append(
        '<div class="pill-row" style="margin-bottom:10px">'
        f'<span class="pill surface">{esc(surface_format)}</span>'
        f'<span class="pill action">{esc(pillar)}</span>'
        f'<span class="pill muted">{esc(title)}</span>'
        "</div>"
    )
    if objective:
        parts.append(
            f'<div class="block label">Objective</div><div class="block">{esc(objective)}</div>'
        )

    hook = caption.get("hook") or ""
    body = caption.get("body") or ""
    cta_line = caption.get("cta_line") or ""
    if hook:
        parts.append(
            f'<div class="block label">Hook</div><div class="block"><b>{esc(hook)}</b></div>'
        )
    if body:
        parts.append(
            f'<div class="block label">Body</div><div class="block">{esc(body)}</div>'
        )
    if cta_line:
        parts.append(
            f'<div class="block label">CTA line</div><div class="block">{esc(cta_line)}</div>'
        )

    if cta.get("channel") or cta.get("label"):
        ch = cta.get("channel") or "—"
        lbl = cta.get("label") or ""
        url = cta.get("url_or_handle") or ""
        cta_text = f"channel={ch}"
        if lbl:
            cta_text += f"  label={lbl!r}"
        if url:
            cta_text += f"  url_or_handle={url}"
        parts.append(
            '<div class="block label">CTA structured</div>'
            f'<div class="block mono">{esc(cta_text)}</div>'
        )

    if hashtags:
        parts.append('<div class="block label">Hashtags</div>')
        parts.append(
            "<div>"
            + "".join(f'<span class="tag">{esc(h)}</span>' for h in hashtags)
            + "</div>"
        )

    return "".join(parts)


def render_image_direction(image: dict[str, Any]) -> str:
    if not image:
        return ""
    return (
        '<section class="section"><h3>Image direction</h3>'
        '<div class="kv">'
        f'<div class="k">Concept</div><div class="v">{esc(image.get("concept"))}</div>'
        f'<div class="k">Alt text</div><div class="v">{esc(image.get("alt_text"))}</div>'
        "</div>"
        f'<div class="block label">Generation prompt</div>'
        f'<div class="block mono">{esc(image.get("generation_prompt"))}</div>'
        "</section>"
    )


def render_visual_selection(vs: dict[str, Any], surface_format: str) -> str:
    if not vs:
        return ""
    rec = vs.get("recommended_asset_urls") or []
    avoid = vs.get("avoid_asset_urls") or []
    refs = vs.get("recommended_reference_urls") or []
    badge = (
        f'<span class="slide-count">{len(rec)} slides</span>'
        if surface_format == "carousel" and rec
        else ""
    )
    parts = [f'<section class="section"><h3>Visual selection{badge}</h3>']
    if rec:
        parts.append('<div class="block label">Recommended asset URLs</div>')
        parts.append(
            '<div class="url-list">'
            + "".join(f'<span class="url">{esc(u)}</span>' for u in rec)
            + "</div>"
        )
    if refs:
        parts.append(
            '<div class="block label" style="margin-top:8px">Reference URLs</div>'
        )
        parts.append(
            '<div class="url-list">'
            + "".join(f'<span class="url">{esc(u)}</span>' for u in refs)
            + "</div>"
        )
    if avoid:
        parts.append('<div class="block label" style="margin-top:8px">Avoid</div>')
        parts.append(
            '<div class="url-list">'
            + "".join(f'<span class="url avoid">{esc(u)}</span>' for u in avoid)
            + "</div>"
        )
    parts.append("</section>")
    return "".join(parts)


def render_strategic_decisions(sd: dict[str, Any]) -> str:
    if not sd:
        return ""
    parts = ["<details open><summary>Strategic decisions</summary>"]
    for key in ("surface_format", "angle", "voice"):
        choice = sd.get(key) or {}
        chosen = choice.get("chosen") or "—"
        rationale = choice.get("rationale") or ""
        alts = choice.get("alternatives_considered") or []
        parts.append(
            '<div class="choice">'
            f'<div class="ch-head"><span class="ch-key">{esc(key)}</span> <span class="ch-val">{esc(chosen)}</span></div>'
            f'<div class="ch-rationale">{esc(rationale)}</div>'
        )
        if alts:
            parts.append(f'<div class="ch-alts">vs. {esc(", ".join(alts))}</div>')
        parts.append("</div>")
    parts.append("</details>")
    return "".join(parts)


def render_internal_flags(enrichment: dict[str, Any]) -> str:
    do_not = enrichment.get("do_not") or []
    confidence = enrichment.get("confidence") or {}
    narrative = enrichment.get("narrative_connection")
    visual_style = enrichment.get("visual_style_notes") or ""

    if not (do_not or confidence or narrative or visual_style):
        return ""
    parts = [
        "<details><summary>Confidence · do_not · style notes · narrative</summary>"
    ]
    if confidence:
        parts.append('<div class="block label">Confidence</div><div class="conf-row">')
        for k, v in confidence.items():
            cls = f"conf-{(v or 'medium').lower()}"
            parts.append(f'<span class="conf-chip {cls}">{esc(k)}: {esc(v)}</span>')
        parts.append("</div>")
    if do_not:
        parts.append('<div class="block label">Do not</div><div>')
        parts.extend(f'<span class="dont">{esc(item)}</span>' for item in do_not)
        parts.append("</div>")
    if visual_style:
        parts.append(
            f'<div class="block label">Visual style notes</div><div class="block">{esc(visual_style)}</div>'
        )
    if narrative:
        parts.append(
            f'<div class="block label">Narrative connection</div><div class="block">{esc(narrative)}</div>'
        )
    parts.append("</details>")
    return "".join(parts)


def render_run(idx: int, row: dict[str, Any]) -> str:
    job = row["job"]
    strat = row["strategy"]
    brief = row["raw_brief"]

    status = job.status or "unknown"
    status_pill = "ok" if status == "done" else "bad" if status == "failed" else "muted"

    bi = (strat.brand_intelligence if strat else {}) or {}
    output = job.output or {}
    enrichment = (output.get("output_data") or {}).get("enrichment") or {}

    created = (
        job.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if job.created_at
        else "—"
    )
    latency = f"{job.latency_ms} ms" if job.latency_ms is not None else "—"
    user_request = (job.input or {}).get("user_request") or ""
    client_name = (job.input or {}).get("client_name") or ""
    surface_format = enrichment.get("surface_format") or ""
    strategy_label = f"strategy v{strat.version}" if strat else "no strategy"

    head = (
        '<div class="run-head">'
        '<div class="pill-row">'
        f'<span class="pill {status_pill}">{esc(status)}</span>'
        f'<span class="pill action">{esc(job.action_code)}</span>'
        + (
            f'<span class="pill surface">{esc(surface_format)}</span>'
            if surface_format
            else ""
        )
        + f'<span class="pill muted">{esc(strategy_label)}</span>'
        f'<span class="pill muted">{esc(latency)}</span>'
        "</div>"
        f'<div class="run-id">#{idx} · {esc(created)} · job <code>{esc(str(job.id)[:8])}</code></div>'
        "</div>"
    )

    request_block = ""
    if user_request or client_name:
        request_block = (
            '<div class="kv">'
            f'<div class="k">Client</div><div class="v">{esc(client_name)}</div>'
            f'<div class="k">Request</div><div class="v">{esc(truncate(user_request, 600))}</div>'
            "</div>"
        )

    cf_section = render_cf_payload(output, enrichment)

    cols = (
        '<div class="cols">'
        f"<div><h3>Brand intelligence (strategy)</h3>{render_brand_intelligence(bi)}</div>"
        f"<div><h3>Caption · CTA · Hashtags</h3>{render_caption_and_hashtags(enrichment)}</div>"
        "</div>"
    )

    image_html = render_image_direction(enrichment.get("image") or {})
    visual_html = render_visual_selection(
        enrichment.get("visual_selection") or {}, surface_format
    )
    strategic_html = render_strategic_decisions(
        enrichment.get("strategic_decisions") or {}
    )
    flags_html = render_internal_flags(enrichment)

    raw = ""
    if brief is not None:
        raw_brief_summary = {
            "router_task_id": str(brief.router_task_id),
            "router_correlation_id": brief.router_correlation_id,
            "envelope_keys": list((brief.envelope or {}).keys()),
        }
        raw = (
            "<details><summary>raw_brief metadata</summary>"
            f'<div class="block mono">{esc(json.dumps(raw_brief_summary, indent=2, ensure_ascii=False))}</div>'
            "</details>"
        )
    full_dump = (
        "<details><summary>full job.output JSON</summary>"
        f'<div class="block mono">{esc(json.dumps(output, indent=2, ensure_ascii=False))}</div>'
        "</details>"
    )

    return (
        f'<section class="run">{head}{request_block}{cf_section}{cols}'
        f"{image_html}{visual_html}{strategic_html}{flags_html}{raw}{full_dump}</section>"
    )


def render_html(runs: list[dict[str, Any]]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if not runs:
        body = '<div class="empty">No runs yet. Run scripts/ops/db_e2e_smoke.py to generate one.</div>'
    else:
        body = "".join(render_run(i + 1, r) for i, r in enumerate(runs))

    header = (
        "<header>"
        '<h1><span class="brand">MARKETER</span> Inspector</h1>'
        '<div class="hdr-meta">'
        f"<div>Generated <b>{esc(now)}</b></div>"
        f"<div>Showing <b>{len(runs)}</b> latest run{'s' if len(runs) != 1 else ''}</div>"
        "</div></header>"
    )

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
        "<title>MARKETER Inspector</title>"
        f"<style>{CSS}</style></head>"
        f'<body><div class="wrap">{header}{body}</div></body></html>'
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    runs = fetch_runs(args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(runs), encoding="utf-8")
    print(f"Wrote {args.output} ({len(runs)} run{'s' if len(runs) != 1 else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
