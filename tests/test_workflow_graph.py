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
    Field,
    FlowFileRecord,
    FlowRecord,
)
from clarinet.services.recordflow.engine import RecordFlowEngine
from clarinet.services.recordflow.flow_record import series
from clarinet.services.workflow_graph import (
    Edge,
    EdgeKind,
    Node,
    NodeKind,
    ParentRecordAuditProvider,
    TriggerKind,
    WorkflowGraph,
    apply_layout,
    build_graph,
    make_entity_id,
    make_pipeline_id,
    make_pipeline_step_id,
    make_record_type_id,
    subgraph_around_record_type,
)

pytestmark = pytest.mark.usefixtures("clear_recordflow_registries")


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

    def test_pipeline_expanded_dedups_step_chain_across_flows(self, empty_engine):
        """Two flows dispatching the same expanded pipeline must inline its
        step chain only once — duplicating PIPELINE_STEP nodes and chain
        edges leaks O(N * flows) noise into the graph for no reason.
        """
        from clarinet.services.pipeline import get_all_pipelines, get_broker_for
        from clarinet.services.pipeline.chain import Pipeline

        broker = get_broker_for("test_q")

        @broker.task
        async def step_a(_msg: dict) -> dict:
            return {}

        @broker.task
        async def step_b(_msg: dict) -> dict:
            return {}

        step_a._pipeline_queue = "test_q"  # type: ignore[attr-defined]
        step_b._pipeline_queue = "test_q"  # type: ignore[attr-defined]

        Pipeline("p1").step(step_a).step(step_b)

        flow_a = FlowRecord("rt_a")
        flow_a.on_status("finished")
        flow_a.pipeline("p1")
        flow_b = FlowRecord("rt_b")
        flow_b.on_status("finished")
        flow_b.pipeline("p1")
        empty_engine.register_flow(flow_a)
        empty_engine.register_flow(flow_b)

        graph = build_graph(
            engine=empty_engine,
            pipelines=get_all_pipelines(),
            expanded_pipelines={"p1"},
        )

        step_nodes = [n for n in graph.nodes if n.kind == NodeKind.PIPELINE_STEP]
        assert len(step_nodes) == 2  # not 4
        chain_edges = [e for e in graph.edges if e.kind == EdgeKind.PIPELINE_STEP_CHAIN]
        assert len(chain_edges) == 2  # not 4
        # Two pipeline-dispatch edges (one per flow) still expected
        dispatch_edges = [e for e in graph.edges if e.kind == EdgeKind.PIPELINE_DISPATCH]
        assert len(dispatch_edges) == 2


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

        call_nodes = [n for n in graph.nodes if n.kind == NodeKind.CALL_FUNCTION]
        assert len(call_nodes) == 1
        call_node = call_nodes[0]
        # Id is module-qualified so callbacks from different modules can't collide.
        assert call_node.id.startswith("call:")
        assert call_node.id.endswith(".my_callback")
        assert call_node.metadata["function_name"] == "my_callback"
        # No pipeline nodes leak from CallFunction
        assert all(n.kind != NodeKind.PIPELINE for n in graph.nodes)

    def test_call_function_id_includes_module(self, empty_engine):
        """Same __name__ from different modules must yield distinct call nodes."""

        def _make(name: str, module: str):
            def fn(*_a, **_k):
                return None

            fn.__name__ = name
            fn.__module__ = module
            return fn

        same_name_a = _make("shared", "module_a")
        same_name_b = _make("shared", "module_b")

        flow_a = FlowRecord("trigger_a")
        flow_a.on_status("finished")
        flow_a.call(same_name_a)
        flow_b = FlowRecord("trigger_b")
        flow_b.on_status("finished")
        flow_b.call(same_name_b)
        empty_engine.register_flow(flow_a)
        empty_engine.register_flow(flow_b)

        graph = build_graph(engine=empty_engine, pipelines={})
        call_nodes = sorted(
            (n for n in graph.nodes if n.kind == NodeKind.CALL_FUNCTION),
            key=lambda n: n.id,
        )
        assert len(call_nodes) == 2
        assert {n.id for n in call_nodes} == {
            "call:module_a.shared",
            "call:module_b.shared",
        }


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


# ── Subgraph filtering ───────────────────────────────────────────────────


def _rt_node(name: str) -> Node:
    return Node(id=make_record_type_id(name), kind=NodeKind.RECORD_TYPE, label=name)


def _pipeline_node(name: str) -> Node:
    return Node(id=make_pipeline_id(name), kind=NodeKind.PIPELINE, label=name)


def _step_node(pipeline: str, idx: int) -> Node:
    return Node(
        id=make_pipeline_step_id(pipeline, idx),
        kind=NodeKind.PIPELINE_STEP,
        label=f"step{idx}",
    )


def _entity_node(kind: str) -> Node:
    return Node(id=make_entity_id(kind), kind=NodeKind.ENTITY, label=f"{kind} (created)")


def _edge(eid: str, src: str, dst: str, kind: EdgeKind = EdgeKind.CREATE_RECORD) -> Edge:
    return Edge(id=eid, from_node=src, to_node=dst, kind=kind, trigger_kind=TriggerKind.NONE)


class TestSubgraphAroundRecordType:
    def test_simple_pair_forward(self):
        a, b = _rt_node("a"), _rt_node("b")
        e = _edge("e1", a.id, b.id)
        graph = WorkflowGraph(nodes=[a, b], edges=[e])

        sub = subgraph_around_record_type(graph, center_id=a.id)

        assert {n.id for n in sub.nodes} == {a.id, b.id}
        assert [edge.id for edge in sub.edges] == ["e1"]

    def test_simple_pair_backward(self):
        """Subgraph around B includes A — BFS traverses incoming edges too."""
        a, b = _rt_node("a"), _rt_node("b")
        e = _edge("e1", a.id, b.id)
        graph = WorkflowGraph(nodes=[a, b], edges=[e])

        sub = subgraph_around_record_type(graph, center_id=b.id)

        assert {n.id for n in sub.nodes} == {a.id, b.id}
        assert [edge.id for edge in sub.edges] == ["e1"]

    def test_pipeline_glue_between_record_types(self):
        """A → pipeline:p → step0 → step1 → B — all intermediates kept."""
        a, b = _rt_node("a"), _rt_node("b")
        p, s0, s1 = _pipeline_node("p"), _step_node("p", 0), _step_node("p", 1)
        edges = [
            _edge("e1", a.id, p.id, EdgeKind.PIPELINE_DISPATCH),
            _edge("e2", p.id, s0.id, EdgeKind.PIPELINE_STEP_CHAIN),
            _edge("e3", s0.id, s1.id, EdgeKind.PIPELINE_STEP_CHAIN),
            _edge("e4", s1.id, b.id, EdgeKind.CREATE_RECORD),
        ]
        graph = WorkflowGraph(nodes=[a, b, p, s0, s1], edges=edges)

        sub = subgraph_around_record_type(graph, center_id=a.id)

        assert {n.id for n in sub.nodes} == {a.id, b.id, p.id, s0.id, s1.id}
        assert {edge.id for edge in sub.edges} == {"e1", "e2", "e3", "e4"}

    def test_unrelated_record_type_excluded(self):
        a, b, c = _rt_node("a"), _rt_node("b"), _rt_node("c")
        e_ab = _edge("e1", a.id, b.id)
        # c is a standalone record_type with no edges connecting to A
        graph = WorkflowGraph(nodes=[a, b, c], edges=[e_ab])

        sub = subgraph_around_record_type(graph, center_id=a.id)

        assert c.id not in {n.id for n in sub.nodes}
        assert {n.id for n in sub.nodes} == {a.id, b.id}

    def test_cycle_does_not_loop(self):
        """A → B → A — BFS terminates and keeps both record_types."""
        a, b = _rt_node("a"), _rt_node("b")
        edges = [_edge("e1", a.id, b.id), _edge("e2", b.id, a.id)]
        graph = WorkflowGraph(nodes=[a, b], edges=edges)

        sub = subgraph_around_record_type(graph, center_id=a.id)

        assert {n.id for n in sub.nodes} == {a.id, b.id}
        assert {edge.id for edge in sub.edges} == {"e1", "e2"}

    def test_three_record_types_stop_after_one_hop(self):
        """A → B → C — subgraph(A) stops at B (foreign rt boundary), C excluded."""
        a, b, c = _rt_node("a"), _rt_node("b"), _rt_node("c")
        edges = [_edge("e1", a.id, b.id), _edge("e2", b.id, c.id)]
        graph = WorkflowGraph(nodes=[a, b, c], edges=edges)

        sub = subgraph_around_record_type(graph, center_id=a.id)

        assert {n.id for n in sub.nodes} == {a.id, b.id}
        assert {edge.id for edge in sub.edges} == {"e1"}

    def test_center_not_in_graph_returns_empty(self):
        a = _rt_node("a")
        graph = WorkflowGraph(nodes=[a], edges=[])

        sub = subgraph_around_record_type(graph, center_id=make_record_type_id("does-not-exist"))

        assert sub.nodes == []
        assert sub.edges == []

    def test_entity_node_traversed_as_intermediate(self):
        """series-entity → A creates 'series' branch backward; subgraph(A)
        should include series entity (intermediate kind, not a stop)."""
        a = _rt_node("a")
        ent = _entity_node("series")
        e = _edge("e1", ent.id, a.id, EdgeKind.CREATE_RECORD)
        graph = WorkflowGraph(nodes=[a, ent], edges=[e])

        sub = subgraph_around_record_type(graph, center_id=a.id)

        assert {n.id for n in sub.nodes} == {a.id, ent.id}

    def test_center_isolated_keeps_only_center(self):
        a = _rt_node("a")
        graph = WorkflowGraph(nodes=[a], edges=[])

        sub = subgraph_around_record_type(graph, center_id=a.id)

        assert {n.id for n in sub.nodes} == {a.id}
        assert sub.edges == []
