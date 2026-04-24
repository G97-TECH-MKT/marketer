"""Tests for the subscription_strategy dispatch flow.

Verifies the contract requested by the orchestrator team (docs/ROUTER CONTRACT.md §2):

1. For a multi-job `subscription_strategy` task marketer MUST emit exactly ONE
   terminal callback (PATCH) to the original `callback_url`; sub-jobs must NOT
   be reported as multiple COMPLETED callbacks (that yields HTTP 409 at the
   orchestrator state machine).
2. Each `job-router` sub-job persisted in `marketer.jobs` MUST be dispatched
   to the orchestrator via POST {ORCH_API_BASE_URL}/api/v1/jobs with a valid
   CreateJobRequest body (action, client_request, context.account_uuid,
   idempotency_key scoped by task_id+db_job_id).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from marketer import main as main_module
from marketer.persistence import PersistCtx
from marketer.schemas.enrichment import (
    BrandIntelligence,
    CallbackBody,
    CallbackOutputData,
    CaptionParts,
    CallToAction,
    CFPayload,
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
from marketer.schemas.internal_context import SubscriptionJob


def _callback_for(job_index: int, status: str = "COMPLETED") -> CallbackBody:
    if status != "COMPLETED":
        return CallbackBody(status="FAILED", error_message="synthetic")

    enrichment = PostEnrichment(
        schema_version="2.0",
        surface_format="post",
        content_pillar="product",
        title=f"Post {job_index}",
        objective="Objective for test.",
        brand_dna="Marca de prueba con narrativa de test.",
        strategic_decisions=StrategicDecisions(
            surface_format=StrategicChoice(
                chosen="post", alternatives_considered=[], rationale="t"
            ),
            angle=StrategicChoice(
                chosen="x", alternatives_considered=[], rationale="t"
            ),
            voice=StrategicChoice(
                chosen="x", alternatives_considered=[], rationale="t"
            ),
        ),
        visual_style_notes="n",
        image=ImageBrief(concept="c", generation_prompt="p", alt_text="a"),
        caption=CaptionParts(hook="h", body="b", cta_line="Reserva"),
        cta=CallToAction(channel="dm", url_or_handle=None, label="DM"),
        hashtag_strategy=HashtagStrategy(
            intent="local_discovery", suggested_volume=5, themes=[]
        ),
        do_not=[],
        visual_selection=VisualSelection(),
        confidence=Confidence(),
        brand_intelligence=BrandIntelligence(
            business_taxonomy="taxonomy",
            funnel_stage_target="awareness",
            voice_register="register",
            emotional_beat="beat",
            audience_persona="persona",
            unfair_advantage="adv",
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
        job_index=job_index,
        total_jobs=3,
    )
    return CallbackBody(
        status="COMPLETED",
        output_data=CallbackOutputData(
            data=CFPayload(
                total_items=1,
                client_dna=enrichment.brand_dna,
                client_request="Brief for CF.",
                resources=[],
            ),
            enrichment=enrichment,
            warnings=[],
            trace=trace,
        ),
    )


def _sub_job(index: int, *, action: str = "create_post") -> SubscriptionJob:
    return SubscriptionJob(
        action_key=action,
        description=f"Descripción del job {index} con tono íntimo y seguro.",
        index=index,
        quantity=1,
        slug="POST-INSTAGRAM",
        orchestrator_agent="job-router",
        product_uuid=f"prod-uuid-{index:03d}",
    )


def _envelope() -> dict[str, Any]:
    return {
        "task_id": "task-xyz",
        "action_code": "subscription_strategy",
        "callback_url": "https://orch.example/api/v1/tasks/task-xyz/callback",
        "correlation_id": "corr-xyz",
        "payload": {
            "client_request": {
                "description": "Estrategia semanal.",
                "jobs": [
                    {
                        "action_key": "create_post",
                        "description": _sub_job(0).description,
                        "quantity": 1,
                        "slug": "POST-INSTAGRAM",
                        "orchestrator_agent": "job-router",
                        "product_uuid": "prod-uuid-000",
                    },
                    {
                        "action_key": "create_post",
                        "description": _sub_job(1).description,
                        "quantity": 1,
                        "slug": "POST-INSTAGRAM",
                        "orchestrator_agent": "job-router",
                        "product_uuid": "prod-uuid-001",
                    },
                ],
            },
            "context": {
                "account_uuid": "acct-1",
                "client_name": "Test Co",
                "platform": "instagram",
            },
        },
    }


@pytest.fixture
def patched_dispatch(monkeypatch):
    """Intercept reason_multi*, _patch_callback, _post_orch_job and persistence."""
    patch_calls: list[dict[str, Any]] = []
    orch_posts: list[dict[str, Any]] = []
    dispatch_updates: list[tuple[Any, str]] = []

    async def fake_patch(callback_url, body, correlation_id, task_id):
        patch_calls.append(
            {
                "callback_url": callback_url,
                "body": body,
                "correlation_id": correlation_id,
                "task_id": task_id,
            }
        )

    async def fake_post_orch(
        *,
        action,
        client_request,
        context,
        correlation_id,
        task_id,
        db_job_id,
        initiator_callback_url=None,
    ):
        orch_posts.append(
            {
                "action": action,
                "client_request": client_request,
                "context": context,
                "correlation_id": correlation_id,
                "task_id": task_id,
                "db_job_id": db_job_id,
                "initiator_callback_url": initiator_callback_url,
            }
        )
        return True

    async def fake_update_dispatch(db_job_id, status):
        dispatch_updates.append((db_job_id, status))

    async def fake_persist_multi(pctx, envelope, valid_results, latency_ms):
        created = []
        for _cb, job in valid_results:
            created.append((uuid.uuid4(), job))
        return created

    async def fake_reason_multi_fanout(*args, **kwargs):
        envelope = args[0]
        jobs_raw = envelope["payload"]["client_request"]["jobs"]
        results: list[tuple[CallbackBody, SubscriptionJob | None]] = []
        for idx, j in enumerate(jobs_raw):
            sj = SubscriptionJob(
                action_key=j["action_key"],
                description=j["description"],
                index=idx,
                quantity=1,
                slug=j.get("slug"),
                orchestrator_agent=j.get("orchestrator_agent"),
                product_uuid=j.get("product_uuid"),
            )
            results.append((_callback_for(idx), sj))
        return results

    monkeypatch.setattr(main_module, "_patch_callback", fake_patch)
    monkeypatch.setattr(main_module, "_post_orch_job", fake_post_orch)
    monkeypatch.setattr(main_module, "update_dispatch_status", fake_update_dispatch)
    monkeypatch.setattr(main_module, "persist_on_complete_multi", fake_persist_multi)
    monkeypatch.setattr(main_module, "reason_multi_fanout", fake_reason_multi_fanout)
    monkeypatch.setattr(main_module.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(main_module.settings, "llm_fanout_enabled", True)
    monkeypatch.setattr(
        main_module.settings, "orch_api_base_url", "https://orch.example"
    )

    return SimpleNamespace(
        patches=patch_calls,
        orch_posts=orch_posts,
        dispatch_updates=dispatch_updates,
    )


def _run_multi(envelope, pctx):
    return asyncio.run(
        main_module._run_multi_and_callback(
            envelope,
            pctx,
            envelope["callback_url"],
            envelope["correlation_id"],
            envelope["task_id"],
            user_profile=None,
            usp_warning=None,
            gallery_pool=None,
            gallery_warning=None,
        )
    )


def test_subscription_strategy_emits_exactly_one_callback(patched_dispatch):
    pctx = PersistCtx(
        raw_brief_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action_code="subscription_strategy",
    )
    _run_multi(_envelope(), pctx)

    assert len(patched_dispatch.patches) == 1, (
        "subscription_strategy must emit EXACTLY ONE terminal PATCH to the "
        "original callback_url; per-sub-job PATCHes cause HTTP 409 at the router."
    )
    only_patch = patched_dispatch.patches[0]
    assert only_patch["body"]["status"] == "COMPLETED"
    assert only_patch["callback_url"].endswith("/callback")


def test_subscription_strategy_posts_one_orch_job_per_llm_result(patched_dispatch):
    pctx = PersistCtx(
        raw_brief_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action_code="subscription_strategy",
    )
    _run_multi(_envelope(), pctx)

    assert len(patched_dispatch.orch_posts) == 2
    for post in patched_dispatch.orch_posts:
        assert post["action"] == "create_post"
        assert post["context"]["account_uuid"] == "acct-1"
        assert post["context"]["source_task_id"] == "task-xyz"
        assert post["context"]["source_action_code"] == "subscription_strategy"
        assert "cf_payload" in post["client_request"]
        assert "enrichment" in post["client_request"]


def test_orch_post_idempotency_key_scopes_task_and_db_job(patched_dispatch):
    """The helper receives task_id + db_job_id; the real function uses both to
    build `idempotency_key=f"marketer:{task_id}:{db_job_id}"` so orchestrator
    can dedupe retries. This test checks the helper sees both."""
    pctx = PersistCtx(
        raw_brief_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action_code="subscription_strategy",
    )
    _run_multi(_envelope(), pctx)

    db_ids = {p["db_job_id"] for p in patched_dispatch.orch_posts}
    assert len(db_ids) == len(patched_dispatch.orch_posts), (
        "each sub-job POST must carry a distinct db_job_id"
    )
    for post in patched_dispatch.orch_posts:
        assert post["task_id"] == "task-xyz"


def test_failed_llm_sub_job_is_not_posted_to_orchestrator(
    patched_dispatch, monkeypatch
):
    async def reason_with_one_failure(*args, **kwargs):
        envelope = args[0]
        jobs_raw = envelope["payload"]["client_request"]["jobs"]
        results: list[tuple[CallbackBody, SubscriptionJob | None]] = []
        for idx, j in enumerate(jobs_raw):
            sj = SubscriptionJob(
                action_key=j["action_key"],
                description=j["description"],
                index=idx,
                quantity=1,
                slug=j.get("slug"),
                orchestrator_agent=j.get("orchestrator_agent"),
                product_uuid=j.get("product_uuid"),
            )
            status = "FAILED" if idx == 1 else "COMPLETED"
            results.append((_callback_for(idx, status=status), sj))
        return results

    monkeypatch.setattr(main_module, "reason_multi_fanout", reason_with_one_failure)

    pctx = PersistCtx(
        raw_brief_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action_code="subscription_strategy",
    )
    _run_multi(_envelope(), pctx)

    assert len(patched_dispatch.patches) == 1
    assert patched_dispatch.patches[0]["body"]["status"] == "COMPLETED"
    assert len(patched_dispatch.orch_posts) == 1
    assert patched_dispatch.orch_posts[0]["client_request"]["slug"] == "POST-INSTAGRAM"

    statuses = [s for _, s in patched_dispatch.dispatch_updates]
    assert "failed" in statuses
    assert "ok" in statuses


def test_no_orch_base_url_falls_back_to_failed(monkeypatch):
    monkeypatch.setattr(main_module.settings, "orch_api_base_url", "")
    monkeypatch.setattr(main_module.settings, "orch_callback_api_key", "")
    monkeypatch.setattr(main_module.settings, "callback_retry_attempts", 1)

    async def never_called(*a, **kw):
        raise AssertionError("HTTP client must not be instantiated when base is empty")

    class _Boom:
        def __init__(self, *a, **kw):
            raise AssertionError(
                "httpx.AsyncClient must not be built when base is empty"
            )

    monkeypatch.setattr(main_module.httpx, "AsyncClient", _Boom)

    result = asyncio.run(
        main_module._post_orch_job(
            action="create_post",
            client_request={"description": "x"},
            context={"account_uuid": "acct-1"},
            correlation_id=None,
            task_id="t1",
            db_job_id="db1",
        )
    )
    assert result is False
