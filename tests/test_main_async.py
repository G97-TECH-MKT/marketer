"""Async dispatch tests for POST /tasks.

Verifies the router-compatible contract (§3-4 of ROUTER CONTRACT):
- POST /tasks ACKs with 202 immediately.
- Background worker runs reason() and PATCHes callback_url with CallbackBody.
- Auth gating when INBOUND_TOKEN is set.
- Sync fallback /tasks/sync keeps returning the body inline.

No live LLM: reason() and httpx.AsyncClient.patch are monkeypatched.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from marketer import main as main_module
from marketer.schemas.enrichment import (
    BrandIntelligence,
    CallbackBody,
    CallbackOutputData,
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

# CFPayload exists only in the post-WIP schema; tolerate HEAD where it isn't
# defined so CI can collect the module either way.
try:
    from marketer.schemas.enrichment import CFPayload  # type: ignore
except ImportError:
    CFPayload = None  # type: ignore[assignment]


def _fake_callback() -> CallbackBody:
    enrichment = PostEnrichment(
        schema_version="2.0",
        surface_format="post",
        content_pillar="product",
        title="Fake",
        objective="Fake objective for async test.",
        brand_dna="Bienvenidos a Fake. Narrativa de prueba para el test.",
        strategic_decisions=StrategicDecisions(
            surface_format=StrategicChoice(chosen="post", alternatives_considered=["story"], rationale="test"),
            angle=StrategicChoice(chosen="producto", alternatives_considered=[], rationale="test"),
            voice=StrategicChoice(chosen="cercano", alternatives_considered=[], rationale="test"),
        ),
        visual_style_notes="test",
        image=ImageBrief(concept="c", generation_prompt="p", alt_text="a"),
        caption=CaptionParts(hook="h", body="b", cta_line="Reserva por DM"),
        cta=CallToAction(channel="dm", url_or_handle=None, label="DM"),
        hashtag_strategy=HashtagStrategy(intent="local_discovery", suggested_volume=5, themes=[]),
        do_not=[],
        visual_selection=VisualSelection(),
        confidence=Confidence(),
        brand_intelligence=BrandIntelligence(
            business_taxonomy="test_fake",
            funnel_stage_target="awareness",
            voice_register="fake-register",
            emotional_beat="test",
            audience_persona="Fake persona for unit test.",
            unfair_advantage="Fake advantage for unit test.",
            risk_flags=[],
            rhetorical_device="ninguno",
        ),
    )
    trace = TraceInfo(
        task_id="t1",
        action_code="create_post",
        surface="post",
        mode="create",
        latency_ms=10,
        gemini_model="fake",
        repair_attempted=False,
        degraded=False,
        gallery_stats=GalleryStats(),
    )
    out_kwargs = {"enrichment": enrichment, "warnings": [], "trace": trace}
    if CFPayload is not None:
        out_kwargs["data"] = CFPayload(
            total_items=1,
            client_dna=enrichment.brand_dna,
            client_request="Fake CF request.",
            resources=[],
        )
    return CallbackBody(
        status="COMPLETED",
        output_data=CallbackOutputData(**out_kwargs),
        error_message=None,
    )


@pytest.fixture
def patched_pipeline(monkeypatch):
    """Intercept reason() + callback PATCH so no network or LLM is used."""
    calls: dict[str, Any] = {"patch_called": False, "patch_args": None, "reason_called_with": None}

    def fake_reason(envelope, gemini, extras_truncation=10):
        calls["reason_called_with"] = envelope
        return _fake_callback()

    async def fake_patch(callback_url, body, correlation_id, task_id):
        calls["patch_called"] = True
        calls["patch_args"] = {
            "callback_url": callback_url,
            "body": body,
            "correlation_id": correlation_id,
            "task_id": task_id,
        }

    # Ensure the code path does not need a real Gemini client either.
    class _FakeClient:
        model = "fake"

    def fake_client_ctor(**kwargs):
        return _FakeClient()

    monkeypatch.setattr(main_module, "reason", fake_reason)
    monkeypatch.setattr(main_module, "_patch_callback", fake_patch)
    monkeypatch.setattr(main_module, "GeminiClient", fake_client_ctor)
    # Ensure settings.gemini_api_key is truthy for the 503 guard
    monkeypatch.setattr(main_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(main_module.settings, "inbound_token", "")
    return calls


@pytest.fixture
def client(patched_pipeline):
    return TestClient(main_module.app)


def _valid_envelope() -> dict:
    return {
        "task_id": "t-123",
        "action_code": "create_post",
        "callback_url": "https://example.com/cb/t-123",
        "correlation_id": "corr-1",
        "payload": {
            "client_request": {"description": "x"},
            "context": {"platform": "instagram"},
        },
    }


def test_post_tasks_returns_202_ack(client, patched_pipeline):
    resp = client.post("/tasks", json=_valid_envelope())
    assert resp.status_code == 202
    body = resp.json()
    assert body == {"status": "ACCEPTED", "task_id": "t-123"}


def test_post_tasks_schedules_callback_patch(client, patched_pipeline):
    client.post("/tasks", json=_valid_envelope())
    # TestClient runs background tasks after the response returns
    assert patched_pipeline["patch_called"] is True
    args = patched_pipeline["patch_args"]
    assert args["callback_url"] == "https://example.com/cb/t-123"
    assert args["correlation_id"] == "corr-1"
    assert args["task_id"] == "t-123"
    # Callback body shape
    body = args["body"]
    assert body["status"] == "COMPLETED"
    assert body["output_data"]["enrichment"]["schema_version"] == "2.0"


def test_post_tasks_reason_receives_full_envelope(client, patched_pipeline):
    env = _valid_envelope()
    client.post("/tasks", json=env)
    assert patched_pipeline["reason_called_with"] == env


def test_post_tasks_rejects_missing_task_id(client):
    env = _valid_envelope()
    del env["task_id"]
    resp = client.post("/tasks", json=env)
    assert resp.status_code == 400
    assert "task_id" in resp.json()["detail"]


def test_post_tasks_rejects_missing_callback_url(client):
    env = _valid_envelope()
    del env["callback_url"]
    resp = client.post("/tasks", json=env)
    assert resp.status_code == 400
    assert "callback_url" in resp.json()["detail"]


def test_post_tasks_rejects_invalid_json(client):
    resp = client.post("/tasks", content=b"not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400


def test_inbound_auth_rejects_bad_token(patched_pipeline, monkeypatch):
    monkeypatch.setattr(main_module.settings, "inbound_token", "secret")
    c = TestClient(main_module.app)
    resp = c.post("/tasks", json=_valid_envelope(), headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_inbound_auth_accepts_correct_token(patched_pipeline, monkeypatch):
    monkeypatch.setattr(main_module.settings, "inbound_token", "secret")
    c = TestClient(main_module.app)
    resp = c.post("/tasks", json=_valid_envelope(), headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 202


def test_inbound_auth_disabled_by_default(patched_pipeline):
    c = TestClient(main_module.app)
    resp = c.post("/tasks", json=_valid_envelope())
    assert resp.status_code == 202


def test_sync_endpoint_returns_callback_body(client, patched_pipeline):
    resp = client.post("/tasks/sync", json=_valid_envelope())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["output_data"]["enrichment"]["schema_version"] == "2.0"


def test_503_when_gemini_key_missing(monkeypatch):
    monkeypatch.setattr(main_module.settings, "gemini_api_key", "")
    monkeypatch.setattr(main_module.settings, "inbound_token", "")
    c = TestClient(main_module.app)
    resp = c.post("/tasks", json=_valid_envelope())
    assert resp.status_code == 503


def test_health_and_ready(patched_pipeline):
    c = TestClient(main_module.app)
    assert c.get("/health").json() == {"status": "healthy"}
    assert c.get("/ready").json() == {"status": "ready"}
