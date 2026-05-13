# WorkflowGraph — visualization service

Read-only introspection over `RecordFlowEngine` and the pipeline registry.
Produces a typed `WorkflowGraph` Pydantic model that the Lustre frontend
renders as native SVG.

## Files

- `models.py` — `Node`, `Edge`, `WorkflowGraph`, `FiringRecord`, enums.
  Public `make_*_id()` helpers keep node ids consistent between builder
  and audit providers.
- `builder.py` — pure `build_graph(engine, pipelines, ...)`. Walks
  `engine.flows / entity_flows / file_flows`, emits action-edges for each
  Pydantic action model. Pipelines optionally inline as `PIPELINE_STEP`
  nodes via `expanded_pipelines={"name", ...}`.
- `layout.py` — pure `apply_layout(graph)`. Kahn topological layered
  layout; cycle-tolerant. Sets `node.position.x/y` + graph width/height.
- `audit.py` — `WorkflowAuditProvider` Protocol + first impl
  `ParentRecordAuditProvider` (recovers `CreateRecord` firings via
  `parent_record_id`). When a real audit table arrives, add a sibling
  provider and combine through `CompositeAuditProvider`.

## Invariants

- `Edge.firings: list[FiringRecord]`. Empty ⇒ potential, non-empty ⇒
  fired (frontend uses solid vs dashed). New audit sources only add
  entries; the schema does not change.
- Builder is pure — no DB, no `await`. Caller fetches descendants and
  passes them via the audit provider when needed.
- Layout is also pure. Coordinates may be overridden client-side later
  for drag-reflow without changing the API shape.

## Usage from the API

```python
from clarinet.services.workflow_graph import (
    ParentRecordAuditProvider,
    apply_layout,
    build_graph,
)
from clarinet.services.pipeline import get_all_pipelines

graph = build_graph(
    engine=request.app.state.recordflow_engine,
    pipelines=get_all_pipelines(),
    audit_provider=ParentRecordAuditProvider(record, descendants),
    expanded_pipelines=expanded,
)
apply_layout(graph)
return graph
```
