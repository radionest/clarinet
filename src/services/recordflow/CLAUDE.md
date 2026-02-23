# RecordFlow — Workflow Automation

Event-driven workflow engine that creates/updates records on status changes. Disabled by default (`recordflow_enabled = False`).

## Core Concepts

- **FlowRecord**: Trigger-activated workflow definition
- **FlowCondition**: Conditional blocks with actions
- **RecordFlowEngine**: Runtime execution engine
- **FlowResult**: Lazy evaluation of data field comparisons

## DSL Syntax

Workflows are defined in `*_flow.py` files:

```python
from src.services.recordflow import record

record('doctor_report')
    .on_status('finished')
    .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
    .add_record('confirm_birads', context_info='BIRADS disagreement')
```

## Key Methods

- `record('type_name')` — create flow for a record type
- `.on_status('status')` — set trigger status
- `.if_(condition)` / `.or_()` / `.and_()` — conditional logic
- `.add_record('type', **kwargs)` — create new record
- `.update_record('name', status='new_status')` — update existing
- `.call(func)` — execute custom function
- `.else_()` — else branch

## Data Access

Dot notation for record data fields:
```python
record('report').data.findings.tumor_size   # Nested access
record('report').d.field_name               # Shorthand
```

Comparison operators: `==`, `!=`, `<`, `<=`, `>`, `>=`

## Engine Setup

```python
from src.services.recordflow import RecordFlowEngine, discover_and_load_flows
from pathlib import Path

engine = RecordFlowEngine(client)
discover_and_load_flows(engine, [Path('flows/')])
await engine.handle_record_status_change(record, old_status)
```

## Configuration

Set in `src/settings.py`:
- `recordflow_enabled` (bool, default False) — enable engine
- `recordflow_paths` (list[str], default []) — directories with `*_flow.py` files
