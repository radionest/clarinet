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
    app.state.recordflow_engine = engine
    try:
        yield engine
    finally:
        app.state.recordflow_engine = previous


@pytest.fixture
def disabled_engine():
    previous = getattr(app.state, "recordflow_engine", None)
    app.state.recordflow_engine = None
    try:
        yield
    finally:
        app.state.recordflow_engine = previous


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
        configured_engine.clarinet_client.create_record.assert_not_called()


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
