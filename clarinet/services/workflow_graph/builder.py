"""Build a :class:`WorkflowGraph` by introspecting the RecordFlow engine.

Pure function — no I/O, no DB. Reads ``engine.flows``, ``entity_flows``,
``file_flows`` plus the global pipeline registry, and emits a graph of
typed nodes and edges. Optional :class:`WorkflowAuditProvider` annotates
edges with firing history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from clarinet.services.recordflow.flow_action import (
    CallFunctionAction,
    CreateRecordAction,
    InvalidateRecordsAction,
    PipelineAction,
    UpdateRecordAction,
)

from .models import (
    Edge,
    EdgeKind,
    Node,
    NodeKind,
    TriggerKind,
    WorkflowGraph,
    make_entity_id,
    make_file_id,
    make_pipeline_id,
    make_pipeline_step_id,
    make_record_type_id,
)

if TYPE_CHECKING:
    from clarinet.services.pipeline.chain import Pipeline
    from clarinet.services.recordflow.engine import RecordFlowEngine
    from clarinet.services.recordflow.flow_action import FlowAction
    from clarinet.services.recordflow.flow_condition import FlowCondition
    from clarinet.services.recordflow.flow_file import FlowFileRecord
    from clarinet.services.recordflow.flow_record import FlowRecord

    from .audit import WorkflowAuditProvider


@dataclass
class _BuildContext:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    edge_id_counter: int = 0

    def add_node(self, node: Node) -> None:
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return
        # Merge metadata — first writer wins on label/kind, but accumulate
        # metadata so trigger-side info (e.g. record-type level) and
        # action-side info (created from action target) coexist.
        merged = dict(existing.metadata)
        for k, v in node.metadata.items():
            merged.setdefault(k, v)
        existing.metadata = merged
        if node.expandable and not existing.expandable:
            existing.expandable = True

    def next_edge_id(self) -> str:
        self.edge_id_counter += 1
        return f"e{self.edge_id_counter}"


def build_graph(
    *,
    engine: RecordFlowEngine,
    pipelines: dict[str, Pipeline],
    audit_provider: WorkflowAuditProvider | None = None,
    expanded_pipelines: set[str] | None = None,
) -> WorkflowGraph:
    """Construct a graph from engine registries and pipeline registry.

    Args:
        engine: Live :class:`RecordFlowEngine`. Reads ``engine.flows``,
            ``engine.entity_flows``, ``engine.file_flows``.
        pipelines: Snapshot of the pipeline registry (e.g. from
            ``get_all_pipelines()``).
        audit_provider: Optional source of firing history. When provided,
            matching edges receive their ``firings`` list populated.
        expanded_pipelines: Names of pipeline nodes whose internal step chain
            should be inlined as ``PIPELINE_STEP`` nodes.

    Returns:
        A :class:`WorkflowGraph` with nodes/edges populated. Layout
        coordinates remain at zero — apply :func:`apply_layout` separately.
    """
    expanded = expanded_pipelines or set()
    ctx = _BuildContext()

    # 1) Record-type triggered flows
    for record_name, record_flows in engine.flows.items():
        source_node = Node(
            id=make_record_type_id(record_name),
            kind=NodeKind.RECORD_TYPE,
            label=record_name,
            metadata={"record_name": record_name},
        )
        ctx.add_node(source_node)
        for flow in record_flows:
            _emit_record_flow_edges(flow, source_node.id, ctx, pipelines, expanded)

    # 2) Entity-creation triggered flows
    for entity_kind, entity_flows in engine.entity_flows.items():
        source_node = Node(
            id=make_entity_id(entity_kind),
            kind=NodeKind.ENTITY,
            label=f"{entity_kind} (created)",
            metadata={"entity_kind": entity_kind},
        )
        ctx.add_node(source_node)
        for flow in entity_flows:
            _emit_entity_flow_edges(flow, source_node.id, ctx, pipelines, expanded)

    # 3) File-update triggered flows
    for file_name, file_flows in engine.file_flows.items():
        source_node = Node(
            id=make_file_id(file_name),
            kind=NodeKind.FILE,
            label=file_name,
            metadata={"file_name": file_name},
        )
        ctx.add_node(source_node)
        for file_flow in file_flows:
            _emit_file_flow_edges(file_flow, source_node.id, ctx, pipelines, expanded)

    # 4) Annotate edges with firings
    if audit_provider is not None:
        firings = audit_provider.get_firings()
        if firings:
            for edge in ctx.edges:
                key = (edge.from_node, edge.to_node, edge.kind.value)
                if key in firings:
                    edge.firings = list(firings[key])

    return WorkflowGraph(
        nodes=list(ctx.nodes.values()),
        edges=ctx.edges,
    )


# ── Flow-walkers ──────────────────────────────────────────────────────────


def _emit_record_flow_edges(
    flow: FlowRecord,
    source_id: str,
    ctx: _BuildContext,
    pipelines: dict[str, Pipeline],
    expanded: set[str],
) -> None:
    trigger_kind, trigger_value = _record_trigger(flow)

    # Unconditional actions
    for action in flow.actions:
        _emit_action_edge(
            source_id=source_id,
            action=action,
            ctx=ctx,
            pipelines=pipelines,
            expanded=expanded,
            trigger_kind=trigger_kind,
            trigger_value=trigger_value,
            condition_summary=None,
        )

    # Conditional actions
    for condition in flow.conditions:
        summary = _condition_summary(condition)
        for action in condition.actions:
            _emit_action_edge(
                source_id=source_id,
                action=action,
                ctx=ctx,
                pipelines=pipelines,
                expanded=expanded,
                trigger_kind=trigger_kind,
                trigger_value=trigger_value,
                condition_summary=summary,
            )


def _emit_entity_flow_edges(
    flow: FlowRecord,
    source_id: str,
    ctx: _BuildContext,
    pipelines: dict[str, Pipeline],
    expanded: set[str],
) -> None:
    trigger_kind = TriggerKind.ON_CREATED
    for action in flow.actions:
        _emit_action_edge(
            source_id=source_id,
            action=action,
            ctx=ctx,
            pipelines=pipelines,
            expanded=expanded,
            trigger_kind=trigger_kind,
            trigger_value=None,
            condition_summary=None,
        )
    for condition in flow.conditions:
        summary = _condition_summary(condition)
        for action in condition.actions:
            _emit_action_edge(
                source_id=source_id,
                action=action,
                ctx=ctx,
                pipelines=pipelines,
                expanded=expanded,
                trigger_kind=trigger_kind,
                trigger_value=None,
                condition_summary=summary,
            )


def _emit_file_flow_edges(
    flow: FlowFileRecord,
    source_id: str,
    ctx: _BuildContext,
    pipelines: dict[str, Pipeline],
    expanded: set[str],
) -> None:
    trigger_kind = TriggerKind.ON_FILE_UPDATE
    for action in flow.actions:
        _emit_action_edge(
            source_id=source_id,
            action=action,
            ctx=ctx,
            pipelines=pipelines,
            expanded=expanded,
            trigger_kind=trigger_kind,
            trigger_value=None,
            condition_summary=None,
        )


# ── Action emission ──────────────────────────────────────────────────────


def _emit_action_edge(
    *,
    source_id: str,
    action: FlowAction,
    ctx: _BuildContext,
    pipelines: dict[str, Pipeline],
    expanded: set[str],
    trigger_kind: TriggerKind,
    trigger_value: str | None,
    condition_summary: str | None,
) -> None:
    match action:
        case CreateRecordAction():
            target_id = make_record_type_id(action.record_type_name)
            ctx.add_node(
                Node(
                    id=target_id,
                    kind=NodeKind.RECORD_TYPE,
                    label=action.record_type_name,
                    metadata={"record_name": action.record_type_name},
                )
            )
            ctx.edges.append(
                Edge(
                    id=ctx.next_edge_id(),
                    from_node=source_id,
                    to_node=target_id,
                    kind=EdgeKind.CREATE_RECORD,
                    trigger_kind=trigger_kind,
                    trigger_value=trigger_value,
                    label=_create_record_label(action),
                    condition_summary=condition_summary,
                    metadata={
                        "inherit_user": action.inherit_user,
                        "parent_record_id": action.parent_record_id,
                    },
                )
            )
        case UpdateRecordAction():
            target_id = make_record_type_id(action.record_name)
            ctx.add_node(
                Node(
                    id=target_id,
                    kind=NodeKind.RECORD_TYPE,
                    label=action.record_name,
                    metadata={"record_name": action.record_name},
                )
            )
            label_status = f"→ {action.status}" if action.status else "(no-op)"
            ctx.edges.append(
                Edge(
                    id=ctx.next_edge_id(),
                    from_node=source_id,
                    to_node=target_id,
                    kind=EdgeKind.UPDATE_RECORD,
                    trigger_kind=trigger_kind,
                    trigger_value=trigger_value,
                    label=label_status,
                    condition_summary=condition_summary,
                    metadata={"strategy": action.strategy, "status": action.status},
                )
            )
        case InvalidateRecordsAction():
            for target_name in action.record_type_names:
                target_id = make_record_type_id(target_name)
                ctx.add_node(
                    Node(
                        id=target_id,
                        kind=NodeKind.RECORD_TYPE,
                        label=target_name,
                        metadata={"record_name": target_name},
                    )
                )
                ctx.edges.append(
                    Edge(
                        id=ctx.next_edge_id(),
                        from_node=source_id,
                        to_node=target_id,
                        kind=EdgeKind.INVALIDATE,
                        trigger_kind=trigger_kind,
                        trigger_value=trigger_value,
                        label=f"invalidate ({action.mode})",
                        condition_summary=condition_summary,
                        metadata={"mode": action.mode},
                    )
                )
        case CallFunctionAction():
            fname = getattr(action.function, "__name__", repr(action.function))
            target_id = f"call:{fname}"
            ctx.add_node(
                Node(
                    id=target_id,
                    kind=NodeKind.CALL_FUNCTION,
                    label=f"call {fname}",
                    metadata={
                        "function_name": fname,
                        "function_module": getattr(action.function, "__module__", None),
                    },
                )
            )
            ctx.edges.append(
                Edge(
                    id=ctx.next_edge_id(),
                    from_node=source_id,
                    to_node=target_id,
                    kind=EdgeKind.CALL_FUNCTION,
                    trigger_kind=trigger_kind,
                    trigger_value=trigger_value,
                    label=f"call {fname}",
                    condition_summary=condition_summary,
                )
            )
        case PipelineAction():
            pipeline_node_id = make_pipeline_id(action.pipeline_name)
            pipeline = pipelines.get(action.pipeline_name)
            is_expanded = action.pipeline_name in expanded
            step_count = len(pipeline.steps) if pipeline else 0
            ctx.add_node(
                Node(
                    id=pipeline_node_id,
                    kind=NodeKind.PIPELINE,
                    label=action.pipeline_name,
                    metadata={
                        "pipeline_name": action.pipeline_name,
                        "step_count": step_count,
                        "exists": pipeline is not None,
                    },
                    expandable=step_count > 0,
                    expanded=is_expanded,
                )
            )
            ctx.edges.append(
                Edge(
                    id=ctx.next_edge_id(),
                    from_node=source_id,
                    to_node=pipeline_node_id,
                    kind=EdgeKind.PIPELINE_DISPATCH,
                    trigger_kind=trigger_kind,
                    trigger_value=trigger_value,
                    label="pipeline",
                    condition_summary=condition_summary,
                )
            )
            if is_expanded and pipeline is not None:
                _emit_pipeline_steps(pipeline, ctx)


def _emit_pipeline_steps(pipeline: Pipeline, ctx: _BuildContext) -> None:
    pipeline_node_id = make_pipeline_id(pipeline.name)
    previous_node_id = pipeline_node_id
    for index, step in enumerate(pipeline.steps):
        step_node_id = make_pipeline_step_id(pipeline.name, index)
        ctx.add_node(
            Node(
                id=step_node_id,
                kind=NodeKind.PIPELINE_STEP,
                label=step.task_name,
                metadata={
                    "pipeline_name": pipeline.name,
                    "step_index": index,
                    "task_name": step.task_name,
                    "queue": step.queue,
                },
            )
        )
        ctx.edges.append(
            Edge(
                id=ctx.next_edge_id(),
                from_node=previous_node_id,
                to_node=step_node_id,
                kind=EdgeKind.PIPELINE_STEP_CHAIN,
                trigger_kind=TriggerKind.NONE,
                label=f"step {index}",
            )
        )
        previous_node_id = step_node_id


# ── Helpers ──────────────────────────────────────────────────────────────


def _record_trigger(flow: FlowRecord) -> tuple[TriggerKind, str | None]:
    if flow.data_update_trigger:
        return TriggerKind.ON_DATA_UPDATE, None
    if flow.file_change_trigger:
        return TriggerKind.ON_FILE_CHANGE, None
    if flow.status_trigger is not None:
        return TriggerKind.ON_STATUS, flow.status_trigger
    # No explicit trigger -> "any status change"
    return TriggerKind.ON_STATUS, None


def _condition_summary(condition: FlowCondition) -> str:
    if condition.is_else:
        return "else"
    if condition.condition is None:
        return ""
    return repr(condition.condition)


def _create_record_label(action: CreateRecordAction) -> str:
    parts = ["create"]
    if action.inherit_user:
        parts.append("(inherit user)")
    if action.parent_record_id is not None:
        parts.append(f"(parent={action.parent_record_id})")
    return " ".join(parts)


__all__ = ["build_graph"]
