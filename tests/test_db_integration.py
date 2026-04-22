"""End-to-end persistence test.

Exercises the full POST /tasks path with persistence enabled and verifies
rows in all 5 tables (users, action_types, raw_briefs, strategies, jobs).

Uses TestClient for the HTTP dispatch (async handler runs on its internal
loop, background task + async DB writes included). Assertions query the DB
via a SYNC engine (psycopg driver) to avoid cross-loop pool issues.

Skipped when DATABASE_URL is not configured.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketer import main as main_module
from marketer.config import load_settings
from marketer.db.models import ActionType, Job, RawBrief, Strategy, User
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

_SETTINGS = load_settings()
pytestmark = pytest.mark.skipif(
    not _SETTINGS.database_url,
    reason="DATABASE_URL not set; skipping DB integration tests",
)


def _sync_engine():
    """psycopg-driven sync engine for test-side reads."""
    url = _SETTINGS.database_url
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return create_engine(url)


def _fake_callback() -> CallbackBody:
    enrichment = PostEnrichment(
        schema_version="2.0",
        surface_format="post",
        content_pillar="product",
        title="DB-test",
        objective="Integration test post.",
        brand_dna="Brand DNA for DB integration test.",
        strategic_decisions=StrategicDecisions(
            surface_format=StrategicChoice(chosen="post", alternatives_considered=[], rationale="t"),
            angle=StrategicChoice(chosen="producto", alternatives_considered=[], rationale="t"),
            voice=StrategicChoice(chosen="cercano", alternatives_considered=[], rationale="t"),
        ),
        visual_style_notes="style",
        image=ImageBrief(concept="c", generation_prompt="p", alt_text="a"),
        caption=CaptionParts(hook="h", body="b", cta_line="Reserva por DM"),
        cta=CallToAction(channel="dm", url_or_handle=None, label="DM"),
        hashtag_strategy=HashtagStrategy(intent="local_discovery", suggested_volume=5, themes=[]),
        do_not=[],
        visual_selection=VisualSelection(),
        confidence=Confidence(),
        brand_intelligence=BrandIntelligence(
            business_taxonomy="test_integration",
            funnel_stage_target="awareness",
            voice_register="test-register",
            emotional_beat="curiosidad",
            audience_persona="Integration-test audience.",
            unfair_advantage="Integration-test advantage.",
            risk_flags=[],
            rhetorical_device="ninguno",
        ),
    )
    trace = TraceInfo(
        task_id="integration", action_code="create_post", surface="post", mode="create",
        latency_ms=10, gemini_model="fake", repair_attempted=False, degraded=False,
        gallery_stats=GalleryStats(),
    )
    out_kwargs = {"enrichment": enrichment, "warnings": [], "trace": trace}
    if CFPayload is not None:
        out_kwargs["data"] = CFPayload(
            total_items=1,
            client_dna=enrichment.brand_dna,
            client_request="CF req.",
            resources=[],
        )
    return CallbackBody(
        status="COMPLETED",
        output_data=CallbackOutputData(**out_kwargs),
        error_message=None,
    )


def _envelope(account_uuid: UUID, task_uuid: UUID) -> dict:
    return {
        "task_id": str(task_uuid),
        "action_code": "create_post",
        "callback_url": "https://example.test/cb",
        "correlation_id": f"int-test-{task_uuid}",
        "payload": {
            "client_request": {"description": "Integration test post request."},
            "context": {
                "account_uuid": str(account_uuid),
                "client_name": "Integration Test Brand",
                "platform": "instagram",
            },
        },
    }


@pytest.fixture
def patched(monkeypatch):
    """Short-circuit LLM + callback PATCH so the test is offline."""
    def fake_reason(envelope, gemini, extras_truncation=10):
        return _fake_callback()

    async def fake_patch(callback_url, body, correlation_id, task_id):
        return None

    class _FakeClient:
        model = "fake"

    monkeypatch.setattr(main_module, "reason", fake_reason)
    monkeypatch.setattr(main_module, "_patch_callback", fake_patch)
    monkeypatch.setattr(main_module, "GeminiClient", lambda **_: _FakeClient())
    monkeypatch.setattr(main_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(main_module.settings, "inbound_token", "")


def test_first_brief_per_user_creates_all_five_table_rows(patched):
    account_uuid = uuid4()
    task_uuid = uuid4()

    with TestClient(main_module.app) as client:
        resp = client.post("/tasks", json=_envelope(account_uuid, task_uuid))
    assert resp.status_code == 202

    engine = _sync_engine()
    with Session(engine) as session:
        user = session.execute(select(User).where(User.id == account_uuid)).scalar_one()
        assert user.id == account_uuid

        # MVP seeded action_types must still be there
        action = session.execute(
            select(ActionType).where(ActionType.code == "create_post")
        ).scalar_one()
        assert action.is_enabled is True

        raw_brief = session.execute(
            select(RawBrief).where(RawBrief.router_task_id == task_uuid)
        ).scalar_one()
        assert raw_brief.user_id == account_uuid
        assert raw_brief.action_code == "create_post"
        assert raw_brief.status == "done"
        assert raw_brief.envelope["task_id"] == str(task_uuid)
        assert raw_brief.processed_at is not None

        strategy = session.execute(
            select(Strategy).where(Strategy.user_id == account_uuid, Strategy.is_active.is_(True))
        ).scalar_one()
        assert strategy.version == 1
        # brand_intelligence promoted from the fake enrichment
        assert strategy.brand_intelligence["business_taxonomy"] == "test_integration"
        assert strategy.brand_intelligence["voice_register"] == "test-register"

        job = session.execute(
            select(Job).where(Job.user_id == account_uuid, Job.raw_brief_id == raw_brief.id)
        ).scalar_one()
        assert job.status == "done"
        assert job.strategy_id == strategy.id
        assert job.action_code == "create_post"
        assert job.output["status"] == "COMPLETED"
        assert job.latency_ms is not None and job.latency_ms >= 0
        assert job.input["router_task_id"] == str(task_uuid)


def test_second_brief_same_user_reuses_strategy(patched):
    account_uuid = uuid4()
    task1 = uuid4()
    task2 = uuid4()

    with TestClient(main_module.app) as client:
        r1 = client.post("/tasks", json=_envelope(account_uuid, task1))
        assert r1.status_code == 202
        r2 = client.post("/tasks", json=_envelope(account_uuid, task2))
        assert r2.status_code == 202

    engine = _sync_engine()
    with Session(engine) as session:
        strategies = (
            session.execute(select(Strategy).where(Strategy.user_id == account_uuid))
            .scalars()
            .all()
        )
        # Exactly one strategy, shared by both jobs
        assert len(strategies) == 1
        strategy_id = strategies[0].id

        jobs = (
            session.execute(select(Job).where(Job.user_id == account_uuid))
            .scalars()
            .all()
        )
        assert len(jobs) == 2
        assert {j.strategy_id for j in jobs} == {strategy_id}


def test_envelope_without_account_uuid_skips_persistence(patched):
    """Degraded path — envelope missing account_uuid should still ACK 202
    but write nothing to the DB."""
    task_uuid = uuid4()
    envelope = {
        "task_id": str(task_uuid),
        "action_code": "create_post",
        "callback_url": "https://example.test/cb",
        "payload": {"client_request": {"description": "x"}, "context": {}},
    }

    with TestClient(main_module.app) as client:
        resp = client.post("/tasks", json=envelope)
    assert resp.status_code == 202

    engine = _sync_engine()
    with Session(engine) as session:
        rows = (
            session.execute(select(RawBrief).where(RawBrief.router_task_id == task_uuid))
            .scalars()
            .all()
        )
        assert rows == []


# ─── Golden fixture — real ROUTER envelope shape ─────────────────────────────

GOLDEN_ENVELOPE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "envelopes" / "golden_post.json"
)


@pytest.mark.skipif(
    not GOLDEN_ENVELOPE_PATH.exists(),
    reason=f"fixture missing: {GOLDEN_ENVELOPE_PATH}",
)
def test_golden_envelope_persists_real_router_shape(patched):
    """Load a real ROUTER envelope fixture, override the IDs so the test is
    isolated, and verify every v2 anchor flowed into the DB as expected."""
    golden = json.loads(GOLDEN_ENVELOPE_PATH.read_text(encoding="utf-8"))

    account_uuid = uuid4()
    task_uuid = uuid4()
    golden["task_id"] = str(task_uuid)
    golden["callback_url"] = f"https://example.test/cb/{task_uuid}"
    golden["payload"]["context"]["account_uuid"] = str(account_uuid)

    with TestClient(main_module.app) as client:
        resp = client.post("/tasks", json=golden)
    assert resp.status_code == 202

    engine = _sync_engine()
    with Session(engine) as session:
        raw_brief = session.execute(
            select(RawBrief).where(RawBrief.router_task_id == task_uuid)
        ).scalar_one()
        # Full envelope stored verbatim — spot-check structural fields
        env = raw_brief.envelope
        assert env["action_code"] == "create_post"
        assert env["payload"]["context"]["client_name"] == "Nubiex Men's Massage by Bruno"
        assert env["payload"]["action_execution_gates"]["brief"]["passed"] is True
        assert env["payload"]["action_execution_gates"]["image_catalog"]["passed"] is True
        assert raw_brief.action_code == "create_post"
        assert raw_brief.status == "done"

        job = session.execute(
            select(Job).where(Job.raw_brief_id == raw_brief.id)
        ).scalar_one()
        assert job.status == "done"
        # Distilled input captures the account_uuid + user_request we sent
        assert job.input["account_uuid"] == str(account_uuid)
        assert "Crea un post" in job.input["user_request"]


# ─── DB integrity — constraints must actually fire ───────────────────────────


def test_fk_rejects_raw_brief_with_unknown_action_code():
    """raw_briefs.action_code has FK to action_types; bogus codes must fail."""
    account_uuid = uuid4()
    engine = _sync_engine()
    with Session(engine) as session:
        session.execute(
            User.__table__.insert().values(id=account_uuid, brand={})
        )
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                RawBrief.__table__.insert().values(
                    user_id=account_uuid,
                    router_task_id=uuid4(),
                    action_code="bogus_action_not_in_catalog",
                    envelope={},
                )
            )
            session.commit()


def test_partial_unique_index_enforces_one_active_strategy_per_user():
    """strategies has a partial UNIQUE (user_id) WHERE is_active — two active
    rows for the same user must fail; two with only one active must succeed."""
    account_uuid = uuid4()
    engine = _sync_engine()
    with Session(engine) as session:
        session.execute(User.__table__.insert().values(id=account_uuid, brand={}))
        session.execute(
            Strategy.__table__.insert().values(
                user_id=account_uuid,
                version=1,
                is_active=True,
                brand_intelligence={"v": 1},
            )
        )
        session.commit()

        # Second active row → integrity failure
        with pytest.raises(IntegrityError):
            session.execute(
                Strategy.__table__.insert().values(
                    user_id=account_uuid,
                    version=2,
                    is_active=True,
                    brand_intelligence={"v": 2},
                )
            )
            session.commit()

    # Separate session for the success-case (previous session is poisoned)
    with Session(engine) as session:
        session.execute(
            Strategy.__table__.insert().values(
                user_id=account_uuid,
                version=2,
                is_active=False,
                brand_intelligence={"v": 2},
            )
        )
        session.commit()
        rows = (
            session.execute(select(Strategy).where(Strategy.user_id == account_uuid))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert sum(1 for r in rows if r.is_active) == 1


# ─── action_types catalog gates the ingest path ──────────────────────────────


def test_unknown_action_code_returns_422(patched):
    """ROUTER → MARKETER with an action_code that's not in action_types
    catalog must be rejected at ingest with 422 (no background work)."""
    envelope = {
        "task_id": str(uuid4()),
        "action_code": "no_such_action_xyz",
        "callback_url": "https://example.test/cb",
        "payload": {
            "client_request": {"description": "x"},
            "context": {"account_uuid": str(uuid4())},
        },
    }
    with TestClient(main_module.app) as client:
        resp = client.post("/tasks", json=envelope)
    assert resp.status_code == 422
    assert "action_unknown" in resp.json()["detail"]


def test_disabled_action_code_returns_422(patched):
    """Toggling action_types.is_enabled=false at runtime should reject new
    requests for that code within one cache refresh. Restores state when done."""
    from marketer.db import actions_cache

    engine = _sync_engine()

    # Flip create_post off in DB and invalidate cache
    with Session(engine) as session:
        session.execute(
            ActionType.__table__.update()
            .where(ActionType.code == "create_post")
            .values(is_enabled=False)
        )
        session.commit()
    actions_cache.invalidate()

    try:
        envelope = {
            "task_id": str(uuid4()),
            "action_code": "create_post",
            "callback_url": "https://example.test/cb",
            "payload": {
                "client_request": {"description": "x"},
                "context": {"account_uuid": str(uuid4())},
            },
        }
        with TestClient(main_module.app) as client:
            resp = client.post("/tasks", json=envelope)
        assert resp.status_code == 422
        assert "not_enabled" in resp.json()["detail"]
    finally:
        # Restore for other tests + next startup check
        with Session(engine) as session:
            session.execute(
                ActionType.__table__.update()
                .where(ActionType.code == "create_post")
                .values(is_enabled=True)
            )
            session.commit()
        actions_cache.invalidate()


def test_check_constraint_rejects_invalid_job_status():
    """jobs.status CHECK restricts values; bogus strings must fail."""
    account_uuid = uuid4()
    engine = _sync_engine()
    with Session(engine) as session:
        session.execute(User.__table__.insert().values(id=account_uuid, brand={}))
        strat_row = session.execute(
            Strategy.__table__.insert()
            .values(
                user_id=account_uuid,
                version=1,
                is_active=True,
                brand_intelligence={"v": 1},
            )
            .returning(Strategy.id)
        ).scalar_one()
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(
                Job.__table__.insert().values(
                    user_id=account_uuid,
                    strategy_id=strat_row,
                    action_code="create_post",
                    input={},
                    status="nonsense_status",
                )
            )
            session.commit()
