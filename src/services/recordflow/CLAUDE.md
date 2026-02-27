# RecordFlow — Workflow Automation

Event-driven workflow engine that creates/updates/invalidates records on status changes or data updates. Disabled by default (`recordflow_enabled = False`).

## Core Concepts

- **FlowRecord**: Trigger-activated workflow definition
- **FlowCondition**: Conditional blocks with actions
- **FlowAction**: Typed Pydantic models for actions (`CreateRecordAction`, `UpdateRecordAction`, `CallFunctionAction`, `InvalidateRecordsAction`, `PipelineAction`)
- **RecordFlowEngine**: Runtime execution engine — dispatches via `isinstance()` on action models
- **FlowResult**: Lazy evaluation of data field comparisons

## DSL Syntax

Workflows are defined in `*_flow.py` files:

```python
from src.services.recordflow import record, series

record('doctor_report')
    .on_status('finished')
    .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
    .add_record('confirm_birads', context_info='BIRADS disagreement')

# Invalidate dependent records when parent data is updated
record('master_model')
    .on_data_update()
    .invalidate_records('child_analysis', 'derived_report', mode='hard')

# Auto-create record when a new series is imported
series().on_created().add_record('series_markup')
```

## Action Models (`flow_action.py`)

Actions are Pydantic models (not dicts). Each has a `type` Literal field:
- `CreateRecordAction(record_type_name, series_uid?, user_id?, context_info?)`
- `UpdateRecordAction(record_name, status?)`
- `CallFunctionAction(function, args, kwargs)` — needs `arbitrary_types_allowed`
- `InvalidateRecordsAction(record_type_names, mode, callback?)` — needs `arbitrary_types_allowed`
- `PipelineAction(pipeline_name, extra_payload?)` — dispatches to pipeline task queue
- `FlowAction` — union type of all five

## Key Methods

- `record('type_name')` — create flow for a record type (always creates new instance)
- `series()` / `study()` / `patient()` — create entity creation flow
- `.on_status('status')` — trigger on status change
- `.on_data_update()` — trigger when finished record's data is updated via PATCH
- `.on_created()` — trigger on entity creation (for entity flows)
- `.if_(condition)` / `.or_()` / `.and_()` — conditional logic
- `.add_record('type', **kwargs)` → `CreateRecordAction`
- `.update_record('name', status='new_status')` → `UpdateRecordAction`
- `.invalidate_records('type1', 'type2', mode='hard'|'soft', callback=fn)` → `InvalidateRecordsAction`
- `.pipeline('name', **extra_payload)` → `PipelineAction` (dispatches to pipeline service)
- `.call(func)` → `CallFunctionAction`
- `.else_()` — else branch
- `.is_active_flow()` — check if flow has triggers/actions (vs data-reference only)

## Triggers

- **`on_status('finished')`** — fires when record status changes to specified value
- **`on_data_update()`** — fires when `PATCH /records/{id}/data` updates a record's data
- **Entity creation** — fires when a new entity (patient/study/series) is created

Record triggers (`on_status`, `on_data_update`) are mutually exclusive per FlowRecord instance. Use separate `record()` calls for different triggers on the same type.

Entity triggers use separate factory functions (`series()`, `study()`, `patient()`) and are stored in `ENTITY_REGISTRY`.

## Invalidation

`invalidate_records()` searches by **patient_id** (broadest scope), enabling cross-level invalidation:
- Series-level change can invalidate patient-level records
- Patient-level change can invalidate series-level records

Modes:
- **hard**: reset status to `pending`, append reason to `context_info` (keeps `user_id`)
- **soft**: only append reason to `context_info`

Optional `callback(record, source_record, client)` for per-project custom behavior.

## record() Factory

Each `record()` call creates a **new** FlowRecord and adds it to `RECORD_REGISTRY`.
Instances used only for data references (e.g. `record('type').data.field` in comparisons)
are filtered out by the loader via `is_active_flow()`.

## Entity Factories

`series()`, `study()`, `patient()` create FlowRecord instances with `entity_trigger` set
and add them to `ENTITY_REGISTRY`. These are loaded alongside record flows by `load_flows_from_file()`.

Engine methods:
- `engine.handle_entity_created(entity_type, patient_id, study_uid?, series_uid?)` — main entry point
- `engine._execute_entity_action()` — handles `create_record` and `call_function` actions

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
await engine.handle_record_data_update(record)  # For data update triggers
await engine.handle_entity_created("series", patient_id, study_uid, series_uid)
```

## API Integration

- `PATCH /records/{id}/status` triggers `handle_record_status_change` via BackgroundTasks
- `PATCH /records/{id}/data` triggers `handle_record_data_update` via BackgroundTasks
- `POST /records/{id}/invalidate` — direct invalidation endpoint (mode, source_record_id, reason)
- `POST /patients`, `POST /studies`, `POST /series` trigger `handle_entity_created` via BackgroundTasks
- `POST /dicom/import-study` triggers `handle_entity_created` for each imported series

## Configuration

Set in `src/settings.py`:
- `recordflow_enabled` (bool, default False) — enable engine
- `recordflow_paths` (list[str], default []) — directories with `*_flow.py` files
