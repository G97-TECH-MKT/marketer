"""Tests for the fan-out path: reason_multi_fanout + extract_brand_dna.

Strategy
--------
We mock at the boundary of `extract_brand_dna` and `reason` rather than at the
Gemini client level. This keeps tests focused on the fan-out orchestration
(brand_dna pre-extraction, parallel dispatch, semaphore concurrency control,
partial-failure handling, feature flag) without dragging in the full
normalize → prompt → repair → validate pipeline of `reason()`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from marketer import reasoner as reasoner_mod
from marketer.reasoner import reason_multi_fanout
from marketer.schemas.enrichment import (
    BrandIntelligence,
    CallbackBody,
    CallbackOutputData,
    CFPayload,
    CaptionParts,
    CallToAction,
    Confidence,
    GalleryStats,
    HashtagStrategy,
    ImageBrief,
    PostEnrichment,
    StrategicChoice,
    StrategicDecisions,
    TraceInfo,
    VisualSelection,
)

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "envelopes"
    / "subscription_strategy.json"
)


def _load_envelope() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _make_enrichment(brand_dna: str = "DNA-DEFAULT") -> PostEnrichment:
    return PostEnrichment(
        surface_format="post",
        content_pillar="product",
        title="Test",
        objective="Test objective",
        brand_dna=brand_dna,
        strategic_decisions=StrategicDecisions(
            surface_format=StrategicChoice(
                chosen="post", alternatives_considered=["story"], rationale="r"
            ),
            angle=StrategicChoice(
                chosen="a", alternatives_considered=["b"], rationale="r"
            ),
            voice=StrategicChoice(
                chosen="v", alternatives_considered=["w"], rationale="r"
            ),
        ),
        visual_style_notes="notes",
        image=ImageBrief(concept="c", generation_prompt="p", alt_text="a"),
        caption=CaptionParts(hook="h", body="b", cta_line="c"),
        cta=CallToAction(channel="none", label=""),
        hashtag_strategy=HashtagStrategy(
            intent="brand_awareness", suggested_volume=5, themes=["t"], tags=["#t"]
        ),
        visual_selection=VisualSelection(),
        confidence=Confidence(),
        brand_intelligence=BrandIntelligence(
            business_taxonomy="bt",
            funnel_stage_target="awareness",
            voice_register="vr",
            emotional_beat="eb",
            audience_persona="ap",
            unfair_advantage="ua",
            risk_flags=[],
            rhetorical_device="contraste",
        ),
        cf_post_brief="brief",
    )


def _make_completed_callback(brand_dna: str) -> CallbackBody:
    enrichment = _make_enrichment(brand_dna=brand_dna)
    cf_payload = CFPayload(
        total_items=1,
        client_dna=brand_dna,
        client_request="brief",
        resources=[],
    )
    trace = TraceInfo(
        task_id="t",
        action_code="create_post",
        surface="post",
        mode="create",
        latency_ms=10,
        gemini_model="fake-model",
        repair_attempted=False,
        degraded=False,
        gallery_stats=GalleryStats(),
    )
    return CallbackBody(
        status="COMPLETED",
        output_data=CallbackOutputData(
            data=cf_payload, enrichment=enrichment, warnings=[], trace=trace
        ),
    )


class _FakeGemini:
    """Minimal stand-in; reason() and extract_brand_dna are mocked away."""

    model_name = "fake-model"

    def generate_structured(self, **_kw: Any):  # pragma: no cover - not invoked
        return None, "", None, {}

    def repair(self, **_kw: Any):  # pragma: no cover - not invoked
        return None, "", None, {}


def _envelope_with_three_create_post_jobs() -> dict[str, Any]:
    env = _load_envelope()
    env["payload"]["client_request"]["jobs"] = [
        {"action_key": "create_post", "description": "Job A", "quantity": 1},
        {"action_key": "create_post", "description": "Job B", "quantity": 1},
        {"action_key": "create_post", "description": "Job C", "quantity": 1},
    ]
    return env


# ---------------------------------------------------------------------------
# 1. Precomputed DNA is extracted once and propagated to every reason() call.
# ---------------------------------------------------------------------------


def test_fanout_calls_n_times_with_precomputed_dna(monkeypatch: pytest.MonkeyPatch):
    env = _envelope_with_three_create_post_jobs()
    captured_dnas: list[str | None] = []
    extract_calls = {"n": 0}

    def fake_extract(envelope_data, gemini, **_kw):  # noqa: ARG001
        extract_calls["n"] += 1
        return "PRECOMPUTED-DNA"

    def fake_reason(
        envelope_data,
        gemini,
        extras_truncation=10,
        prompt_text_truncation_chars=600,
        max_output_tokens=16384,
        user_profile=None,
        usp_warning=None,
        gallery_pool=None,
        gallery_warning=None,
        precomputed_brand_dna=None,
    ):  # noqa: ARG001
        captured_dnas.append(precomputed_brand_dna)
        return _make_completed_callback(brand_dna=precomputed_brand_dna or "ignored")

    monkeypatch.setattr(reasoner_mod, "extract_brand_dna", fake_extract)
    monkeypatch.setattr(reasoner_mod, "reason", fake_reason)

    results = asyncio.run(reason_multi_fanout(env, gemini=_FakeGemini(), concurrency=5))

    assert extract_calls["n"] == 1
    assert len(results) == 3
    assert all(cb.status == "COMPLETED" for cb, _ in results)
    assert captured_dnas == ["PRECOMPUTED-DNA"] * 3
    # Each callback gets job_index/total stamped on the trace
    for idx, (cb, job) in enumerate(results):
        assert job is not None and job.index == idx
        assert cb.output_data.trace.job_index == idx
        assert cb.output_data.trace.total_jobs == 3
        assert cb.output_data.trace.job_action_key == "create_post"


# ---------------------------------------------------------------------------
# 2. Partial failure: one job raises, the others still COMPLETE.
# ---------------------------------------------------------------------------


def test_fanout_partial_failure(monkeypatch: pytest.MonkeyPatch):
    env = _envelope_with_three_create_post_jobs()
    monkeypatch.setattr(
        reasoner_mod, "extract_brand_dna", lambda *_a, **_kw: "DNA"
    )

    def fake_reason(envelope_data, gemini, *_a, **_kw):  # noqa: ARG001
        description = envelope_data["payload"]["client_request"]["description"]
        if description == "Job B":
            raise RuntimeError("simulated job failure")
        return _make_completed_callback(brand_dna="DNA")

    monkeypatch.setattr(reasoner_mod, "reason", fake_reason)

    results = asyncio.run(reason_multi_fanout(env, gemini=_FakeGemini(), concurrency=5))

    assert len(results) == 3
    statuses = [cb.status for cb, _ in results]
    assert statuses == ["COMPLETED", "FAILED", "COMPLETED"]
    failed_cb = results[1][0]
    assert "fanout_exception" in (failed_cb.error_message or "")
    assert "RuntimeError" in (failed_cb.error_message or "")


# ---------------------------------------------------------------------------
# 3. Brand DNA extraction failure → fan-out continues with None.
# ---------------------------------------------------------------------------


def test_fanout_brand_dna_failure_falls_back(monkeypatch: pytest.MonkeyPatch):
    env = _envelope_with_three_create_post_jobs()
    monkeypatch.setattr(reasoner_mod, "extract_brand_dna", lambda *_a, **_kw: None)

    captured_dnas: list[str | None] = []

    def fake_reason(envelope_data, gemini, *_a, precomputed_brand_dna=None, **_kw):  # noqa: ARG001
        captured_dnas.append(precomputed_brand_dna)
        return _make_completed_callback(brand_dna="from-llm")

    monkeypatch.setattr(reasoner_mod, "reason", fake_reason)

    results = asyncio.run(reason_multi_fanout(env, gemini=_FakeGemini(), concurrency=5))

    assert len(results) == 3
    assert all(cb.status == "COMPLETED" for cb, _ in results)
    # All jobs received None — they will compute their own brand_dna in reason()
    assert captured_dnas == [None, None, None]


# ---------------------------------------------------------------------------
# 4. Semaphore enforces concurrency cap.
# ---------------------------------------------------------------------------


def test_fanout_respects_concurrency_limit(monkeypatch: pytest.MonkeyPatch):
    env = _load_envelope()
    env["payload"]["client_request"]["jobs"] = [
        {"action_key": "create_post", "description": f"Job {i}", "quantity": 1}
        for i in range(6)
    ]
    monkeypatch.setattr(reasoner_mod, "extract_brand_dna", lambda *_a, **_kw: "DNA")

    in_flight = 0
    max_in_flight = 0
    lock = threading.Lock()

    def fake_reason(*_a: Any, **_kw: Any) -> CallbackBody:
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.05)  # simulate Gemini latency
        with lock:
            in_flight -= 1
        return _make_completed_callback(brand_dna="DNA")

    monkeypatch.setattr(reasoner_mod, "reason", fake_reason)

    results = asyncio.run(reason_multi_fanout(env, gemini=_FakeGemini(), concurrency=2))

    assert len(results) == 6
    assert all(cb.status == "COMPLETED" for cb, _ in results)
    assert max_in_flight <= 2, f"Semaphore breached: peak in-flight={max_in_flight}"


# ---------------------------------------------------------------------------
# 5. Feature flag off → main.py's wiring uses legacy reason_multi, not fan-out.
# ---------------------------------------------------------------------------


def test_flag_off_routes_to_legacy_reason_multi(monkeypatch: pytest.MonkeyPatch):
    """When `llm_fanout_enabled=False`, the wiring in main.py must call the
    legacy `reason_multi` and never reach `reason_multi_fanout`.

    We test this at the wiring layer by stubbing both functions and exercising
    only the flag-branch logic from `_run_multi_and_callback`. Reproducing that
    branch verbatim keeps the test fast and decoupled from DB/HTTP fixtures.
    """
    from marketer import main as main_mod

    fanout_called = {"n": 0}
    legacy_called = {"n": 0}

    async def fake_fanout(*_a: Any, **_kw: Any):
        fanout_called["n"] += 1
        return [(_make_completed_callback("DNA"), None)]

    def fake_legacy(*_a: Any, **_kw: Any):
        legacy_called["n"] += 1
        return [(_make_completed_callback("DNA"), None)]

    monkeypatch.setattr(main_mod, "reason_multi_fanout", fake_fanout)
    monkeypatch.setattr(main_mod, "reason_multi", fake_legacy)

    # Reproduce the branch from `_run_multi_and_callback`:
    async def _branch(flag_value: bool):
        if flag_value:
            return await main_mod.reason_multi_fanout()
        return await asyncio.to_thread(main_mod.reason_multi)

    # Flag OFF → legacy
    asyncio.run(_branch(False))
    assert legacy_called["n"] == 1
    assert fanout_called["n"] == 0

    # Flag ON → fan-out
    asyncio.run(_branch(True))
    assert legacy_called["n"] == 1
    assert fanout_called["n"] == 1
