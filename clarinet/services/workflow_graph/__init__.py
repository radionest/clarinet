"""WorkflowGraph — visualization graph derived from RecordFlow + Pipeline.

Public API:
    build_graph(engine, pipelines, audit_provider=None, expanded_pipelines=None)
    apply_layout(graph)
    ParentRecordAuditProvider(trigger, candidates)
    CompositeAuditProvider(providers)

The package is read-only: it introspects live registries and produces a
:class:`WorkflowGraph` Pydantic model. The corresponding admin endpoints
(``/api/admin/workflow/graph``, ``/dry-run``, ``/fire``) live in
``clarinet/api/routers/workflow.py``.
"""

from __future__ import annotations

from .audit import (
    CompositeAuditProvider,
    ParentRecordAuditProvider,
    WorkflowAuditProvider,
)
from .builder import build_graph, subgraph_around_record_type
from .layout import apply_layout
from .models import (
    Edge,
    EdgeKind,
    FiringRecord,
    FiringSource,
    Node,
    NodeKind,
    Position,
    TriggerKind,
    WorkflowGraph,
    make_entity_id,
    make_file_id,
    make_pipeline_id,
    make_pipeline_step_id,
    make_record_type_id,
)

__all__ = [
    "CompositeAuditProvider",
    "Edge",
    "EdgeKind",
    "FiringRecord",
    "FiringSource",
    "Node",
    "NodeKind",
    "ParentRecordAuditProvider",
    "Position",
    "TriggerKind",
    "WorkflowAuditProvider",
    "WorkflowGraph",
    "apply_layout",
    "build_graph",
    "make_entity_id",
    "make_file_id",
    "make_pipeline_id",
    "make_pipeline_step_id",
    "make_record_type_id",
    "subgraph_around_record_type",
]
