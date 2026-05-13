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
from clarinet.client import ClarinetClient
from clarinet.services.recordflow import FlowRecord
from clarinet.services.recordflow.engine import RecordFlowEngine
from tests.conftest import create_authenticated_client, create_mock_superuser
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_study,
    make_user,
    seed_record,
)
from tests.utils.urls import (
    WORKFLOW_DISPATCH,
    WORKFLOW_DISPATCH_DRY_RUN,
    WORKFLOW_DRY_RUN,
    WORKFLOW_FIRE,
    WORKFLOW_GRAPH,
)

pytestmark = pytest.mark.usefixtures("clear_recordflow_registries")


@pytest_asyncio.fixture
async def workflow_env(fresh_session):
    """Seed patient → study → record_type → user → parent + child record.

    Uses ``fresh_session`` so the suite exercises a clean identity map —
    catches lazy-load / MissingGreenlet regressions that ``test_session``
    would mask by serving relationships from its cache.
    """
    pat = make_patient("WF_PAT", "Workflow Patient")
    fresh_session.add(pat)
    await fresh_session.commit()

    study = make_study("WF_PAT", "1.2.3.7000")
    fresh_session.add(study)
    await fresh_session.commit()

    parent_rt = make_record_type("wf-parent")
    child_rt = make_record_type("wf-child")
    fresh_session.add(parent_rt)
    fresh_session.add(child_rt)
    await fresh_session.commit()

    user = make_user()
    fresh_session.add(user)
    await fresh_session.commit()
    await fresh_session.refresh(user)

    parent = await seed_record(
        fresh_session,
        patient_id="WF_PAT",
        study_uid="1.2.3.7000",
        series_uid=None,
        rt_name="wf-parent",
        user_id=user.id,
    )
    child = await seed_record(
        fresh_session,
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
    mock_client = AsyncMock(spec=ClarinetClient)
    mock_client.find_records.return_value = []
    # Instance attrs invisible to spec=Class — set explicitly so the engine's
    # auth/reachability probes short-circuit instead of raising.
    mock_client._authenticated = True
    mock_client.service_token = None
    mock_client.client = AsyncMock()
    engine = RecordFlowEngine(mock_client)
    engine._api_verified = True

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

    mock_client = AsyncMock(spec=ClarinetClient)
    mock_client.find_records.return_value = []
    # Instance attrs invisible to spec=Class — set explicitly so the engine's
    # auth/reachability probes short-circuit instead of raising.
    mock_client._authenticated = True
    mock_client.service_token = None
    mock_client.client = AsyncMock()
    engine = RecordFlowEngine(mock_client)
    engine._api_verified = True

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

    @pytest.mark.asyncio
    async def test_default_scope_is_schema(self, client, configured_engine):
        """No ?scope param → same shape as the legacy schema graph (back-compat)."""
        resp_default = await client.get(WORKFLOW_GRAPH)
        resp_explicit = await client.get(WORKFLOW_GRAPH, params={"scope": "schema"})
        assert resp_default.status_code == 200
        assert resp_explicit.status_code == 200
        ids_default = {n["id"] for n in resp_default.json()["nodes"]}
        ids_explicit = {n["id"] for n in resp_explicit.json()["nodes"]}
        assert ids_default == ids_explicit

    @pytest.mark.asyncio
    async def test_instance_scope_returns_subgraph(self, client, configured_engine, workflow_env):
        """scope=instance + record_id → subgraph around record's record_type.

        Schema graph contains both wf-parent and wf-child. For a wf-parent
        record, the subgraph centered on record_type:wf-parent must still
        contain both (1-hop boundary along create_record edges).
        """
        # Add a third unrelated record_type to the engine so we can verify it's filtered out.
        flow_unrelated = FlowRecord("wf-unrelated")
        flow_unrelated.on_status("finished")
        flow_unrelated.add_record("wf-other")
        configured_engine.register_flow(flow_unrelated)

        parent_id = workflow_env["parent"].id
        resp = await client.get(
            WORKFLOW_GRAPH, params={"scope": "instance", "record_id": parent_id}
        )
        assert resp.status_code == 200
        body = resp.json()
        node_ids = {n["id"] for n in body["nodes"]}
        assert "record_type:wf-parent" in node_ids
        assert "record_type:wf-child" in node_ids
        # Unrelated record_types must not leak in
        assert "record_type:wf-unrelated" not in node_ids
        assert "record_type:wf-other" not in node_ids

    @pytest.mark.asyncio
    async def test_instance_scope_without_record_id_returns_422(self, client, configured_engine):
        """scope=instance must reject calls that don't carry a record_id."""
        resp = await client.get(WORKFLOW_GRAPH, params={"scope": "instance"})
        assert resp.status_code == 422
        body = resp.json()
        # detail field exists and mentions record_id
        detail = body.get("detail") or body.get("message", "")
        assert "record_id" in str(detail).lower()


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


# ── /dispatch-dry-run and /dispatch ─────────────────────────────────────


def dispatch_test_callback(record, context, client):
    """Module-level callback so its (module, __name__) is stable across runs."""
    return None


@pytest.fixture
def configured_engine_with_call():
    """Engine with a flow that has a ``.call(dispatch_test_callback)`` action.

    The DSL method auto-registers the callable into
    :mod:`clarinet.services.recordflow.call_function_registry`, so
    ``/dispatch-dry-run`` and ``/dispatch`` can look up the bound
    :class:`CallFunctionAction` by its node id.
    """
    from clarinet.services.pipeline import get_broker_for
    from clarinet.services.pipeline.chain import Pipeline

    mock_client = AsyncMock(spec=ClarinetClient)
    mock_client.find_records.return_value = []
    mock_client._authenticated = True
    mock_client.service_token = None
    mock_client.client = AsyncMock()
    engine = RecordFlowEngine(mock_client)
    engine._api_verified = True

    flow = FlowRecord("wf-parent")
    flow.on_status("finished")
    flow.call(dispatch_test_callback)
    engine.register_flow(flow)

    # Also register a pipeline so dispatching ``pipeline:p1`` is exercisable.
    broker = get_broker_for("test_q")

    @broker.task
    async def dispatch_step(_msg: dict) -> dict:
        return {}

    dispatch_step._pipeline_queue = "test_q"  # type: ignore[attr-defined]
    Pipeline("p1").step(dispatch_step)

    previous_engine = getattr(app.state, "recordflow_engine", None)
    previous_cache = getattr(app.state, "workflow_used_digests", None)
    app.state.recordflow_engine = engine
    app.state.workflow_used_digests = None
    try:
        yield engine
    finally:
        app.state.recordflow_engine = previous_engine
        app.state.workflow_used_digests = previous_cache


def _make_dispatch_kiq_mock(task_id: str = "fake-task-1"):
    """Helper: AsyncMock-shaped task object returning ``.task_id``."""
    return AsyncMock(return_value=type("FakeTask", (), {"task_id": task_id})())


class TestDispatchDryRun:
    @pytest.mark.asyncio
    async def test_unknown_node_kind_returns_422(
        self, client, configured_engine_with_call, workflow_env
    ):
        resp = await client.post(
            WORKFLOW_DISPATCH_DRY_RUN,
            json={"node_id": "record_type:wf-parent", "record_id": workflow_env["parent"].id},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_call_function_returns_404(
        self, client, configured_engine_with_call, workflow_env
    ):
        resp = await client.post(
            WORKFLOW_DISPATCH_DRY_RUN,
            json={"node_id": "call:nope.fn", "record_id": workflow_env["parent"].id},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_pipeline_returns_404(
        self, client, configured_engine_with_call, workflow_env
    ):
        resp = await client.post(
            WORKFLOW_DISPATCH_DRY_RUN,
            json={"node_id": "pipeline:does_not_exist", "record_id": workflow_env["parent"].id},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_call_function_returns_preview_and_digest(
        self, client, configured_engine_with_call, workflow_env
    ):
        from clarinet.services.recordflow import call_function_registry

        node_id = call_function_registry.make_call_function_id(dispatch_test_callback)
        resp = await client.post(
            WORKFLOW_DISPATCH_DRY_RUN,
            json={"node_id": node_id, "record_id": workflow_env["parent"].id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["digest"], str) and len(body["digest"]) == 16
        preview = body["preview"]
        assert preview["kind"] == "call_function"
        assert preview["node_id"] == node_id
        assert preview["record_id"] == workflow_env["parent"].id
        assert preview["payload_preview"]["function_name"] == "dispatch_test_callback"

    @pytest.mark.asyncio
    async def test_pipeline_returns_preview_and_digest(
        self, client, configured_engine_with_call, workflow_env
    ):
        resp = await client.post(
            WORKFLOW_DISPATCH_DRY_RUN,
            json={"node_id": "pipeline:p1", "record_id": workflow_env["parent"].id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["preview"]["kind"] == "pipeline"
        assert body["preview"]["payload_preview"]["pipeline_name"] == "p1"
        assert body["preview"]["payload_preview"]["step_count"] == 1

    @pytest.mark.asyncio
    async def test_digest_stable_across_calls(
        self, client, configured_engine_with_call, workflow_env
    ):
        from clarinet.services.recordflow import call_function_registry

        node_id = call_function_registry.make_call_function_id(dispatch_test_callback)
        payload = {"node_id": node_id, "record_id": workflow_env["parent"].id}
        d1 = (await client.post(WORKFLOW_DISPATCH_DRY_RUN, json=payload)).json()["digest"]
        d2 = (await client.post(WORKFLOW_DISPATCH_DRY_RUN, json=payload)).json()["digest"]
        assert d1 == d2


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_call_function_enqueues_and_returns_task_id(
        self, client, configured_engine_with_call, workflow_env, monkeypatch
    ):
        from clarinet.services.recordflow import call_function_registry

        node_id = call_function_registry.make_call_function_id(dispatch_test_callback)
        kiq_mock = _make_dispatch_kiq_mock("task-abc")
        monkeypatch.setattr(
            "clarinet.api.routers.workflow.call_registered_callable.kiq",
            kiq_mock,
        )

        dry = await client.post(
            WORKFLOW_DISPATCH_DRY_RUN,
            json={"node_id": node_id, "record_id": workflow_env["parent"].id},
        )
        digest = dry.json()["digest"]

        resp = await client.post(
            WORKFLOW_DISPATCH,
            json={
                "node_id": node_id,
                "record_id": workflow_env["parent"].id,
                "plan_digest": digest,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == "task-abc"
        assert body["preview"]["kind"] == "call_function"
        kiq_mock.assert_awaited_once()
        sent_msg = kiq_mock.await_args[0][0]
        assert sent_msg["payload"]["call_function_id"] == node_id
        assert sent_msg["record_id"] == workflow_env["parent"].id

    @pytest.mark.asyncio
    async def test_dispatch_pipeline_calls_run_and_returns_task_id(
        self, client, configured_engine_with_call, workflow_env, monkeypatch
    ):
        from clarinet.services.pipeline.chain import Pipeline

        run_mock = AsyncMock(return_value=type("FakeTask", (), {"task_id": "task-pipe-1"})())
        monkeypatch.setattr(Pipeline, "run", run_mock)

        dry = await client.post(
            WORKFLOW_DISPATCH_DRY_RUN,
            json={"node_id": "pipeline:p1", "record_id": workflow_env["parent"].id},
        )
        digest = dry.json()["digest"]

        resp = await client.post(
            WORKFLOW_DISPATCH,
            json={
                "node_id": "pipeline:p1",
                "record_id": workflow_env["parent"].id,
                "plan_digest": digest,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == "task-pipe-1"
        assert body["preview"]["kind"] == "pipeline"
        run_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_wrong_digest_returns_409(
        self, client, configured_engine_with_call, workflow_env, monkeypatch
    ):
        from clarinet.services.recordflow import call_function_registry

        node_id = call_function_registry.make_call_function_id(dispatch_test_callback)
        kiq_mock = _make_dispatch_kiq_mock()
        monkeypatch.setattr(
            "clarinet.api.routers.workflow.call_registered_callable.kiq",
            kiq_mock,
        )

        resp = await client.post(
            WORKFLOW_DISPATCH,
            json={
                "node_id": node_id,
                "record_id": workflow_env["parent"].id,
                "plan_digest": "deadbeefdeadbeef",
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "WORKFLOW_PLAN_CHANGED"
        kiq_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_replay_returns_409(
        self, client, configured_engine_with_call, workflow_env, monkeypatch
    ):
        from clarinet.services.recordflow import call_function_registry

        node_id = call_function_registry.make_call_function_id(dispatch_test_callback)
        kiq_mock = _make_dispatch_kiq_mock("replay-task")
        monkeypatch.setattr(
            "clarinet.api.routers.workflow.call_registered_callable.kiq",
            kiq_mock,
        )

        dry = await client.post(
            WORKFLOW_DISPATCH_DRY_RUN,
            json={"node_id": node_id, "record_id": workflow_env["parent"].id},
        )
        digest = dry.json()["digest"]
        payload = {
            "node_id": node_id,
            "record_id": workflow_env["parent"].id,
            "plan_digest": digest,
        }
        first = await client.post(WORKFLOW_DISPATCH, json=payload)
        assert first.status_code == 200

        second = await client.post(WORKFLOW_DISPATCH, json=payload)
        assert second.status_code == 409
        body = second.json()
        assert body["code"] == "WORKFLOW_DIGEST_ALREADY_USED"
        # Enqueue happened exactly once across both POSTs.
        assert kiq_mock.await_count == 1


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
