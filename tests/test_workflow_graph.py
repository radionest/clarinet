"""Unit tests for clarinet.services.workflow_graph (builder + layout + audit).

Pure logic tests — no DB, no HTTP. The builder reads ``RecordFlowEngine``
registries directly; we register a few synthetic flows and inspect the
resulting :class:`WorkflowGraph`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.patient import PatientBase
from clarinet.models.record import RecordRead, RecordTypeBase
from clarinet.models.study import StudyBase
from clarinet.services.recordflow import (
    ENTITY_REGISTRY,
    FILE_REGISTRY,
    RECORD_REGISTRY,
    Field,
    FlowFileRecord,
    FlowRecord,
)
from clarinet.services.recordflow.engine import RecordFlowEngine
from clarinet.services.recordflow.flow_record import series
from clarinet.services.workflow_graph import (
    EdgeKind,
    NodeKind,
    ParentRecordAuditProvider,
    TriggerKind,
    apply_layout,
    build_graph,
    make_pipeline_id,
    make_pipeline_step_id,
    make_record_type_id,
)


def _make_record(
    name: str,
    *,
    record_id: int = 1,
    status: RecordStatus = RecordStatus.pending,
    parent_record_id: int | None = None,
    data: dict | None = None,
) -> RecordRead:
    type_name = name if len(name) >= 5 else f"{name}-type"
    return RecordRead(
        id=record_id,
        data=data,
        status=status,
        record_type_name=type_name,
        patient_id="PAT001",
        study_uid="1.2.3.4.5",
        parent_record_id=parent_record_id,
        created_at=datetime.now(UTC),
        changed_at=datetime.now(UTC),
        patient=PatientBase(id="PAT001", name="Test Patient"),
        study=StudyBase(
            study_uid="1.2.3.4.5",
            date=datetime.now(UTC).date(),
            patient_id="PAT001",
        ),
        series=None,
        record_type=RecordTypeBase(name=type_name, level=DicomQueryLevel.STUDY),
    )


@pytest.fixture(autouse=True)
def _clear_registry():
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


@pytest.fixture
def empty_engine():
    return RecordFlowEngine(AsyncMock())


# ── Builder ──────────────────────────────────────────────────────────────


class TestBuilderRecordTypeFlows:
    def test_create_record_action(self, empty_engine):
        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.add_record("output-type")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})

        node_ids = {n.id for n in graph.nodes}
        assert make_record_type_id("trigger-type") in node_ids
        assert make_record_type_id("output-type") in node_ids

        edges_to_output = [
            e for e in graph.edges if e.to_node == make_record_type_id("output-type")
        ]
        assert len(edges_to_output) == 1
        edge = edges_to_output[0]
        assert edge.kind == EdgeKind.CREATE_RECORD
        assert edge.trigger_kind == TriggerKind.ON_STATUS
        assert edge.trigger_value == "finished"
        assert edge.condition_summary is None

    def test_data_update_trigger(self, empty_engine):
        flow = FlowRecord("trigger-type")
        flow.on_data_update()
        flow.add_record("output-type")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})

        edges = [e for e in graph.edges if e.kind == EdgeKind.CREATE_RECORD]
        assert len(edges) == 1
        assert edges[0].trigger_kind == TriggerKind.ON_DATA_UPDATE
        assert edges[0].trigger_value is None

    def test_invalidate_records_emits_one_edge_per_target(self, empty_engine):
        flow = FlowRecord("master")
        flow.on_data_update()
        flow.invalidate_records("child_a", "child_b", mode="hard")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})
        invalidate_edges = [e for e in graph.edges if e.kind == EdgeKind.INVALIDATE]
        targets = sorted(e.to_node for e in invalidate_edges)
        assert targets == [
            make_record_type_id("child_a"),
            make_record_type_id("child_b"),
        ]
        assert all(e.metadata["mode"] == "hard" for e in invalidate_edges)

    def test_pipeline_action_collapsed_by_default(self, empty_engine):
        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.pipeline("seg_pipeline")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})

        pipeline_node = next(n for n in graph.nodes if n.id == make_pipeline_id("seg_pipeline"))
        assert pipeline_node.kind == NodeKind.PIPELINE
        # No real Pipeline registered → not expandable
        assert pipeline_node.expandable is False
        assert pipeline_node.expanded is False

        # No PIPELINE_STEP nodes when collapsed
        assert not any(n.kind == NodeKind.PIPELINE_STEP for n in graph.nodes)

    def test_conditional_actions_carry_condition_summary(self, empty_engine):
        F = Field()
        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.if_record(F.is_good == True)  # noqa: E712
        flow.add_record("conditional-output")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})

        edge = next(
            e for e in graph.edges if e.to_node == make_record_type_id("conditional-output")
        )
        assert edge.condition_summary  # non-empty
        assert "is_good" in edge.condition_summary

    def test_match_case_emits_edge_per_branch(self, empty_engine):
        F = Field()
        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.match(F.study_type)
        flow.case("CT")
        flow.add_record("seg_CT")
        flow.case("MRI")
        flow.add_record("seg_MRI")
        flow.default()
        flow.add_record("seg_unknown")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})

        targets = sorted(e.to_node for e in graph.edges if e.kind == EdgeKind.CREATE_RECORD)
        assert targets == sorted(
            [
                make_record_type_id("seg_CT"),
                make_record_type_id("seg_MRI"),
                make_record_type_id("seg_unknown"),
            ]
        )


class TestBuilderEntityAndFileFlows:
    def test_entity_flow(self, empty_engine):
        flow = series().on_created().add_record("series_markup")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})

        entity_nodes = [n for n in graph.nodes if n.kind == NodeKind.ENTITY]
        assert len(entity_nodes) == 1
        assert entity_nodes[0].id == "entity:series"

        edge = next(e for e in graph.edges if e.from_node == "entity:series")
        assert edge.trigger_kind == TriggerKind.ON_CREATED
        assert edge.kind == EdgeKind.CREATE_RECORD

    def test_file_flow(self, empty_engine):
        flow = FlowFileRecord("master_model")
        flow.on_update()
        flow.invalidate_all_records("child_x")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})

        file_nodes = [n for n in graph.nodes if n.kind == NodeKind.FILE]
        assert len(file_nodes) == 1
        assert file_nodes[0].label == "master_model"

        edge = next(e for e in graph.edges if e.kind == EdgeKind.INVALIDATE)
        assert edge.from_node == "file:master_model"
        assert edge.trigger_kind == TriggerKind.ON_FILE_UPDATE


class TestBuilderPipelineExpansion:
    def test_pipeline_steps_inline_when_expanded(self, empty_engine):
        from clarinet.services.pipeline import get_all_pipelines, get_broker_for
        from clarinet.services.pipeline.chain import Pipeline

        broker = get_broker_for("test_q")

        @broker.task
        async def step_a(_msg: dict) -> dict:
            return {}

        @broker.task
        async def step_b(_msg: dict) -> dict:
            return {}

        # pipeline_task() decorator binds a queue; mimic that for plain @broker.task
        step_a._pipeline_queue = "test_q"  # type: ignore[attr-defined]
        step_b._pipeline_queue = "test_q"  # type: ignore[attr-defined]

        Pipeline("p1").step(step_a).step(step_b)

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.pipeline("p1")
        empty_engine.register_flow(flow)

        graph = build_graph(
            engine=empty_engine,
            pipelines=get_all_pipelines(),
            expanded_pipelines={"p1"},
        )

        pipeline_node = next(n for n in graph.nodes if n.id == make_pipeline_id("p1"))
        assert pipeline_node.expandable is True
        assert pipeline_node.expanded is True

        step_nodes = sorted(
            (n for n in graph.nodes if n.kind == NodeKind.PIPELINE_STEP),
            key=lambda n: n.metadata["step_index"],
        )
        assert [n.id for n in step_nodes] == [
            make_pipeline_step_id("p1", 0),
            make_pipeline_step_id("p1", 1),
        ]

        chain_edges = [e for e in graph.edges if e.kind == EdgeKind.PIPELINE_STEP_CHAIN]
        assert len(chain_edges) == 2
        # First chain edge must come from the pipeline node
        first = next(e for e in chain_edges if e.from_node == make_pipeline_id("p1"))
        assert first.to_node == make_pipeline_step_id("p1", 0)


class TestAuditProvider:
    def test_parent_record_id_marks_create_edge_fired(self, empty_engine):
        flow = FlowRecord("parent-type")
        flow.on_status("finished")
        flow.add_record("child-type")
        empty_engine.register_flow(flow)

        parent = _make_record("parent-type", record_id=10, status=RecordStatus.finished)
        child = _make_record(
            "child-type",
            record_id=11,
            status=RecordStatus.pending,
            parent_record_id=10,
        )
        unrelated = _make_record(
            "child-type", record_id=12, status=RecordStatus.pending, parent_record_id=999
        )

        provider = ParentRecordAuditProvider(parent, [child, unrelated])
        graph = build_graph(engine=empty_engine, pipelines={}, audit_provider=provider)

        edge = next(e for e in graph.edges if e.to_node == make_record_type_id("child-type"))
        assert len(edge.firings) == 1
        firing = edge.firings[0]
        assert firing.metadata["child_record_id"] == 11


class TestBuilderCallFunction:
    def test_call_function_action_emits_call_function_node(self, empty_engine):
        """N1: `flow.call(fn)` produces a node of kind CALL_FUNCTION (not PIPELINE)."""

        def my_callback(*_args, **_kwargs):
            return None

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.call(my_callback)
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})

        call_node = next(n for n in graph.nodes if n.id == "call:my_callback")
        assert call_node.kind == NodeKind.CALL_FUNCTION
        # No pipeline nodes leak from CallFunction
        assert all(n.kind != NodeKind.PIPELINE for n in graph.nodes)


# ── Layout ──────────────────────────────────────────────────────────────


class TestLayout:
    def test_linear_chain_assigns_ascending_layers(self, empty_engine):
        flow_a = FlowRecord("type_a")
        flow_a.on_status("finished")
        flow_a.add_record("type_b")
        flow_b = FlowRecord("type_b")
        flow_b.on_status("finished")
        flow_b.add_record("type_c")
        empty_engine.register_flow(flow_a)
        empty_engine.register_flow(flow_b)

        graph = build_graph(engine=empty_engine, pipelines={})
        apply_layout(graph)

        positions = {n.id: n.position for n in graph.nodes}
        # type_a < type_b < type_c by x
        x_a = positions[make_record_type_id("type_a")].x
        x_b = positions[make_record_type_id("type_b")].x
        x_c = positions[make_record_type_id("type_c")].x
        assert x_a < x_b < x_c

    def test_layout_handles_cycle(self, empty_engine):
        flow_a = FlowRecord("type_a")
        flow_a.on_status("finished")
        flow_a.add_record("type_b")
        flow_b = FlowRecord("type_b")
        flow_b.on_status("finished")
        flow_b.add_record("type_a")  # cycle!
        empty_engine.register_flow(flow_a)
        empty_engine.register_flow(flow_b)

        graph = build_graph(engine=empty_engine, pipelines={})
        apply_layout(graph)

        # Both nodes get *some* position, no infinite loop
        positions = {n.id: n.position for n in graph.nodes}
        assert all(p.x >= 0 for p in positions.values())
        assert all(p.y >= 0 for p in positions.values())
        assert graph.width > 0
        assert graph.height > 0

    def test_parallel_branches_separate_y(self, empty_engine):
        flow = FlowRecord("source")
        flow.on_status("finished")
        flow.add_record("branch_a")
        flow.add_record("branch_b")
        empty_engine.register_flow(flow)

        graph = build_graph(engine=empty_engine, pipelines={})
        apply_layout(graph)

        a = next(n for n in graph.nodes if n.id == make_record_type_id("branch_a"))
        b = next(n for n in graph.nodes if n.id == make_record_type_id("branch_b"))
        # Same layer => same x
        assert a.position.x == b.position.x
        # Different rows => different y
        assert a.position.y != b.position.y
