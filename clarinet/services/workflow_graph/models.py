"""Domain models for the workflow visualization graph.

These Pydantic models are the single source of truth for the
``GET /api/admin/workflow/graph`` payload and the ``WorkflowGraph`` object
consumed by the Lustre frontend. They describe a directed graph of record
types, entities, files and pipelines with a per-edge **firing log** so the
view can highlight branches that have actually fired (today: only via
``parent_record_id``; later: via a future audit table — without API churn,
since :class:`Edge.firings` is already a list).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NodeKind(str, Enum):
    RECORD_TYPE = "record_type"
    ENTITY = "entity"
    FILE = "file"
    PIPELINE = "pipeline"
    PIPELINE_STEP = "pipeline_step"
    CALL_FUNCTION = "call_function"


class EdgeKind(str, Enum):
    """How one node leads to another.

    Trigger-edges describe the *source* of a flow (what fired it); action-edges
    describe what the flow *does*. A typical FlowRecord produces N action-edges
    (one per CreateRecord/UpdateRecord/Invalidate/Pipeline action) — the
    trigger metadata is stored on each action-edge.
    """

    CREATE_RECORD = "create_record"
    UPDATE_RECORD = "update_record"
    INVALIDATE = "invalidate"
    CALL_FUNCTION = "call_function"
    PIPELINE_DISPATCH = "pipeline_dispatch"
    PIPELINE_STEP_CHAIN = "pipeline_step_chain"


class TriggerKind(str, Enum):
    """How a flow is fired."""

    ON_STATUS = "on_status"
    ON_DATA_UPDATE = "on_data_update"
    ON_FILE_CHANGE = "on_file_change"
    ON_CREATED = "on_created"  # entity flows
    ON_FILE_UPDATE = "on_file_update"  # project-file flows
    NONE = "none"  # pipeline step chain — no trigger semantics


class FiringSource(str, Enum):
    """Where evidence of a firing came from."""

    PARENT_RECORD_ID = "parent_record_id"
    PIPELINE_AUDIT = "pipeline_audit"
    INVALIDATION_AUDIT = "invalidation_audit"
    STATUS_AUDIT = "status_audit"


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class FiringRecord(BaseModel):
    """One observed firing of an edge (one historical execution)."""

    fired_at: datetime
    source: FiringSource
    metadata: dict[str, Any] = Field(default_factory=dict)


class Node(BaseModel):
    """A graph node.

    The ``id`` is unique within the graph — generated as ``{kind}:{name}`` for
    record types, ``entity:{kind}`` for entity factories, ``file:{name}``,
    ``pipeline:{name}``, ``pipeline_step:{pipeline}::{step_index}``.
    Use :func:`make_record_type_id` etc. helpers to keep ids consistent
    between the builder and the audit provider.
    """

    id: str
    kind: NodeKind
    label: str
    position: Position = Field(default_factory=Position)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expandable: bool = False
    expanded: bool = False


class Edge(BaseModel):
    """A directed edge from ``from_node`` to ``to_node``.

    `firings` is a list, not a flag — empty means "potential", non-empty means
    "fired N times". Future audit data (pipeline runs, invalidation events)
    plug in as additional :class:`FiringRecord` entries without breaking the
    schema or the frontend renderer.
    """

    id: str
    from_node: str
    to_node: str
    kind: EdgeKind

    trigger_kind: TriggerKind = TriggerKind.NONE
    trigger_value: str | None = None
    """For ON_STATUS — the target status (e.g. ``"finished"``)."""

    label: str | None = None
    """One-line summary for tooltip/legend (e.g. condition pretty-print)."""

    condition_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    firings: list[FiringRecord] = Field(default_factory=list)


class WorkflowGraph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    width: float = 0.0
    height: float = 0.0


def make_record_type_id(record_name: str) -> str:
    return f"record_type:{record_name}"


def make_entity_id(entity_kind: str) -> str:
    return f"entity:{entity_kind}"


def make_file_id(file_name: str) -> str:
    return f"file:{file_name}"


def make_pipeline_id(pipeline_name: str) -> str:
    return f"pipeline:{pipeline_name}"


def make_pipeline_step_id(pipeline_name: str, step_index: int) -> str:
    return f"pipeline_step:{pipeline_name}::{step_index}"
