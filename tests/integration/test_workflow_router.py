"""Integration tests for /api/admin/workflow router.

Covers:
- 503 when recordflow disabled, 403 for non-admin, basic schema/graph response.
- /dry-run returns a plan + stable digest, no side effects on client.
- /fire validates the digest and runs the real engine handler.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

from clarinet.api.app import app
from clarinet.services.recordflow import (
    ENTITY_REGISTRY,
    FILE_REGISTRY,
    RECORD_REGISTRY,
    FlowRecord,
)
from clarinet.services.recordflow.engine import RecordFlowEngine
from tests.conftest import create_authenticated_client, create_mock_superuser
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_study,
    make_user,
    seed_record,
)
from tests.utils.urls import WORKFLOW_DRY_RUN, WORKFLOW_FIRE, WORKFLOW_GRAPH


@pytest.fixture(autouse=True)
def _clear_registries():
    from clarinet.services.pipeline.chain import _PIPELINE_REGISTRY, _TASK_REGISTRY

    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    _TASK_REGISTRY.clear()
    yield
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    _TASK_REGISTRY.clear()


@pytest_asyncio.fixture
async def workflow_env(test_session):
    """Seed patient → study → record_type → user → finished record + a child record."""
    pat = make_patient("WF_PAT", "Workflow Patient")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("WF_PAT", "1.2.3.7000")
    test_session.add(study)
    await test_session.commit()

    parent_rt = make_record_type("wf-parent")
    child_rt = make_record_type("wf-child")
    test_session.add(parent_rt)
    test_session.add(child_rt)
    await test_session.commit()

    user = make_user()
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    parent = await seed_record(
        test_session,
        patient_id="WF_PAT",
        study_uid="1.2.3.7000",
        series_uid=None,
        rt_name="wf-parent",
        user_id=user.id,
    )
    child = await seed_record(
        test_session,
        patient_id="WF_PAT",
        study_uid="1.2.3.7000",
        series_uid=None,
        rt_name="wf-child",
        user_id=user.id,
        parent_record_id=parent.id,
    )
    return {"parent": parent, "child": child, "user": user}


@pytest.fixture
def configured_engine():
    """Install a configured engine on app.state for the duration of the test.

    Registers a single flow:
        record('wf-parent').on_status('finished').add_record('wf-child')

    Always restores the previous app.state value, so other tests aren't affected.
    """
    mock_client = AsyncMock()
    mock_client.find_records = AsyncMock(return_value=[])
    engine = RecordFlowEngine(mock_client)

    flow = FlowRecord("wf-parent")
    flow.on_status("finished")
    flow.add_record("wf-child")
    engine.register_flow(flow)

    previous = getattr(app.state, "recordflow_engine", None)
    previous_cache = getattr(app.state, "workflow_used_digests", None)
    app.state.recordflow_engine = engine
    app.state.workflow_used_digests = None  # let router rebuild a fresh cache
    try:
        yield engine
    finally:
        app.state.recordflow_engine = previous
        app.state.workflow_used_digests = previous_cache


@pytest.fixture
def disabled_engine():
    previous = getattr(app.state, "recordflow_engine", None)
    app.state.recordflow_engine = None
    try:
        yield
    finally:
        app.state.recordflow_engine = previous


@pytest.fixture
def configured_engine_with_pipeline():
    """Same as `configured_engine` but also registers a two-step Pipeline 'p1'.

    The trigger flow dispatches that pipeline so build_graph sees it; passing
    ``expanded=p1`` to /graph then must inline two PIPELINE_STEP nodes.
    """
    from clarinet.services.pipeline import get_broker_for
    from clarinet.services.pipeline.chain import Pipeline

    broker = get_broker_for("test_q")

    @broker.task
    async def step_a(_msg: dict) -> dict:
        return {}

    @broker.task
    async def step_b(_msg: dict) -> dict:
        return {}

    # Mimic @pipeline_task(queue=...) — tests pass a plain @broker.task,
    # but Pipeline.step() needs the bound-queue attribute.
    step_a._pipeline_queue = "test_q"  # type: ignore[attr-defined]
    step_b._pipeline_queue = "test_q"  # type: ignore[attr-defined]

    Pipeline("p1").step(step_a).step(step_b)

    mock_client = AsyncMock()
    mock_client.find_records = AsyncMock(return_value=[])
    engine = RecordFlowEngine(mock_client)

    flow = FlowRecord("wf-parent")
    flow.on_status("finished")
    flow.pipeline("p1")
    engine.register_flow(flow)

    previous = getattr(app.state, "recordflow_engine", None)
    previous_cache = getattr(app.state, "workflow_used_digests", None)
    app.state.recordflow_engine = engine
    app.state.workflow_used_digests = None
    try:
        yield engine
    finally:
        app.state.recordflow_engine = previous
        app.state.workflow_used_digests = previous_cache


# ── /graph ───────────────────────────────────────────────────────────────


class TestGraphEndpoint:
    @pytest.mark.asyncio
    async def test_returns_503_when_engine_disabled(self, client, disabled_engine):
        resp = await client.get(WORKFLOW_GRAPH)
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_schema_graph(self, client, configured_engine):
        resp = await client.get(WORKFLOW_GRAPH)
        assert resp.status_code == 200
        body = resp.json()
        node_ids = {n["id"] for n in body["nodes"]}
        assert "record_type:wf-parent" in node_ids
        assert "record_type:wf-child" in node_ids
        # Edge from parent to child via CREATE_RECORD
        edges = [e for e in body["edges"] if e["kind"] == "create_record"]
        assert any(
            e["from_node"] == "record_type:wf-parent" and e["to_node"] == "record_type:wf-child"
            for e in edges
        )
        # No firings without record_id
        assert all(e["firings"] == [] for e in edges)
        # Layout populated
        assert body["width"] > 0
        assert body["height"] > 0

    @pytest.mark.asyncio
    async def test_instance_graph_marks_fired_edge(self, client, configured_engine, workflow_env):
        parent_id = workflow_env["parent"].id
        resp = await client.get(WORKFLOW_GRAPH, params={"record_id": parent_id})
        assert resp.status_code == 200
        body = resp.json()
        create_edges = [
            e
            for e in body["edges"]
            if e["kind"] == "create_record"
            and e["from_node"] == "record_type:wf-parent"
            and e["to_node"] == "record_type:wf-child"
        ]
        assert len(create_edges) == 1
        firings = create_edges[0]["firings"]
        assert len(firings) == 1
        assert firings[0]["source"] == "parent_record_id"
        assert firings[0]["metadata"]["child_record_id"] == workflow_env["child"].id

    @pytest.mark.asyncio
    async def test_instance_graph_404_when_record_missing(self, client, configured_engine):
        resp = await client.get(WORKFLOW_GRAPH, params={"record_id": 999_999})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_expanded_pipeline_inlines_steps_via_query(
        self, client, configured_engine_with_pipeline
    ):
        """N3: ?expanded=p1 inlines PIPELINE_STEP nodes for the named pipeline."""
        resp = await client.get(WORKFLOW_GRAPH, params={"expanded": "p1"})
        assert resp.status_code == 200
        body = resp.json()
        step_nodes = [n for n in body["nodes"] if n["kind"] == "pipeline_step"]
        assert len(step_nodes) == 2
        pipeline_node = next(n for n in body["nodes"] if n["id"] == "pipeline:p1")
        assert pipeline_node["expanded"] is True


# ── /dry-run and /fire ───────────────────────────────────────────────────


class TestDryRunAndFire:
    @pytest.mark.asyncio
    async def test_dry_run_returns_plan_and_digest(self, client, configured_engine, workflow_env):
        parent_id = workflow_env["parent"].id
        resp = await client.post(
            WORKFLOW_DRY_RUN,
            json={
                "record_id": parent_id,
                "trigger_kind": "status",
                "status_override": "finished",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["digest"], str) and len(body["digest"]) == 16
        assert len(body["plan"]) == 1
        action = body["plan"][0]
        assert action["action_type"] == "create_record"
        assert action["target"] == "wf-child"
        assert action["trigger_record_id"] == parent_id

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_client(self, client, configured_engine, workflow_env):
        """Verify no real engine action ran (the mock client is never called)."""
        parent_id = workflow_env["parent"].id
        await client.post(
            WORKFLOW_DRY_RUN,
            json={
                "record_id": parent_id,
                "trigger_kind": "status",
                "status_override": "finished",
            },
        )
        configured_engine.clarinet_client.create_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_fire_with_correct_digest_runs_engine(
        self, client, configured_engine, workflow_env
    ):
        parent_id = workflow_env["parent"].id
        dry = await client.post(
            WORKFLOW_DRY_RUN,
            json={
                "record_id": parent_id,
                "trigger_kind": "status",
                "status_override": "finished",
            },
        )
        digest = dry.json()["digest"]

        fired = await client.post(
            WORKFLOW_FIRE,
            json={
                "record_id": parent_id,
                "trigger_kind": "status",
                "status_override": "finished",
                "plan_digest": digest,
            },
        )
        assert fired.status_code == 200
        body = fired.json()
        assert len(body["executed_actions"]) == 1
        assert body["executed_actions"][0]["target"] == "wf-child"
        # The real engine handler ran and called create_record on the (mock) client
        configured_engine.clarinet_client.create_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_fire_with_wrong_digest_returns_409(
        self, client, configured_engine, workflow_env
    ):
        """B2: wrong digest -> 409 with machine-readable `code` and diagnostics."""
        parent_id = workflow_env["parent"].id
        resp = await client.post(
            WORKFLOW_FIRE,
            json={
                "record_id": parent_id,
                "trigger_kind": "status",
                "status_override": "finished",
                "plan_digest": "deadbeefdeadbeef",
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "WORKFLOW_PLAN_CHANGED"
        assert body["expected_digest"] == "deadbeefdeadbeef"
        assert isinstance(body["current_digest"], str) and len(body["current_digest"]) == 16
        configured_engine.clarinet_client.create_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_fire_replay_with_same_digest_returns_409(
        self, client, configured_engine, workflow_env
    ):
        """B1: replaying /fire with the same digest is rejected (idempotency cache)."""
        parent_id = workflow_env["parent"].id
        dry = await client.post(
            WORKFLOW_DRY_RUN,
            json={
                "record_id": parent_id,
                "trigger_kind": "status",
                "status_override": "finished",
            },
        )
        digest = dry.json()["digest"]
        payload = {
            "record_id": parent_id,
            "trigger_kind": "status",
            "status_override": "finished",
            "plan_digest": digest,
        }
        first = await client.post(WORKFLOW_FIRE, json=payload)
        assert first.status_code == 200

        second = await client.post(WORKFLOW_FIRE, json=payload)
        assert second.status_code == 409
        body = second.json()
        assert body["code"] == "WORKFLOW_DIGEST_ALREADY_USED"
        assert body["digest"] == digest
        # Engine was called exactly once despite two POSTs
        assert configured_engine.clarinet_client.create_record.call_count == 1

    @pytest.mark.asyncio
    async def test_dry_run_invalid_status_override_returns_422(
        self, client, configured_engine, workflow_env
    ):
        """U4: status_override is validated against RecordStatus enum."""
        resp = await client.post(
            WORKFLOW_DRY_RUN,
            json={
                "record_id": workflow_env["parent"].id,
                "trigger_kind": "status",
                "status_override": "garbage",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_fire_concurrent_same_digest_executes_only_once(
        self, client, configured_engine, workflow_env
    ):
        """Race: N concurrent /fire calls with the same digest — exactly one
        wins, the others see 409 WORKFLOW_DIGEST_ALREADY_USED. The engine
        handler runs exactly once even though we fired the requests in
        parallel via asyncio.gather.
        """
        import asyncio

        parent_id = workflow_env["parent"].id
        dry = await client.post(
            WORKFLOW_DRY_RUN,
            json={
                "record_id": parent_id,
                "trigger_kind": "status",
                "status_override": "finished",
            },
        )
        digest = dry.json()["digest"]
        payload = {
            "record_id": parent_id,
            "trigger_kind": "status",
            "status_override": "finished",
            "plan_digest": digest,
        }

        responses = await asyncio.gather(
            *(client.post(WORKFLOW_FIRE, json=payload) for _ in range(5))
        )
        statuses = sorted(r.status_code for r in responses)
        assert statuses == [200, 409, 409, 409, 409]

        already_used = next(r for r in responses if r.status_code == 409).json()
        assert already_used["code"] == "WORKFLOW_DIGEST_ALREADY_USED"
        assert already_used["digest"] == digest

        # Engine ran for the winner only — race on the cache reservation is closed.
        assert configured_engine.clarinet_client.create_record.call_count == 1

    @pytest.mark.asyncio
    async def test_dry_run_digest_stable_across_calls(
        self, client, configured_engine, workflow_env
    ):
        """Same input ⇒ same digest. Guards against accidental nondeterminism
        in `_compute_digest` (e.g. unsorted dict keys, mutable defaults
        leaking, or a `default=str` fallback masking exotic types).
        """
        parent_id = workflow_env["parent"].id
        body = {
            "record_id": parent_id,
            "trigger_kind": "status",
            "status_override": "finished",
        }
        d1 = (await client.post(WORKFLOW_DRY_RUN, json=body)).json()["digest"]
        d2 = (await client.post(WORKFLOW_DRY_RUN, json=body)).json()["digest"]
        assert d1 == d2


# ── Authorization ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def regular_user_client(test_session, test_settings) -> AsyncGenerator[AsyncClient, None]:
    """A client authenticated as a regular (non-admin) user."""
    user = await create_mock_superuser(test_session, email="regular@test.com")
    user.is_superuser = False  # downgrade
    async for ac in create_authenticated_client(user, test_session, test_settings):
        yield ac


class TestAuthorization:
    @pytest.mark.asyncio
    async def test_non_admin_gets_403_on_graph(self, regular_user_client, configured_engine):
        resp = await regular_user_client.get(WORKFLOW_GRAPH)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_gets_403_on_dry_run(self, regular_user_client, configured_engine):
        resp = await regular_user_client.post(
            WORKFLOW_DRY_RUN,
            json={"record_id": 1, "trigger_kind": "status"},
        )
        assert resp.status_code == 403
