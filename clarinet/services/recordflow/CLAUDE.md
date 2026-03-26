# RecordFlow — Workflow Automation

Event-driven workflow engine that creates/updates/invalidates records on status changes or data updates. Disabled by default (`recordflow_enabled = False`).

## Core Concepts

- **FlowRecord**: Trigger-activated workflow definition
- **FlowFileRecord**: File-level trigger workflow definition (`flow_file.py`)
- **FlowCondition**: Conditional blocks with actions
- **FlowAction**: Typed Pydantic models for actions (`CreateRecordAction`, `UpdateRecordAction`, `CallFunctionAction`, `InvalidateRecordsAction`, `PipelineAction`)
- **RecordFlowEngine**: Runtime execution engine — dispatches via `isinstance()` on action models
- **FlowResult**: Lazy evaluation of data field comparisons
- **Field**: Self-referential proxy (`F.field_name`) for triggering record's own data

## DSL Syntax

Workflows are defined in `*_flow.py` files:

```python
from clarinet.services.recordflow import record, flow, series, file  # flow is an alias for record

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

# Invalidate records when a project-level file changes
file(master_model).on_update().invalidate_all_records('create_master_projection')
```

## Action Models (`flow_action.py`)

Actions are Pydantic models (not dicts). Each has a `type` Literal field:
- `CreateRecordAction(record_type_name, series_uid?, user_id?, parent_record_id?, inherit_user?, context_info?)`
- `UpdateRecordAction(record_name, status?)`
- `CallFunctionAction(function, args, kwargs)` — needs `arbitrary_types_allowed`
- `InvalidateRecordsAction(record_type_names, mode, callback?)` — needs `arbitrary_types_allowed`
- `PipelineAction(pipeline_name, extra_payload?)` — dispatches to pipeline task queue
- `FlowAction` — union type of all five

## Key Methods

- `record('type_name')` — create flow for a record type (always creates new instance)
- `file(file_obj)` — create flow for a project-level file (accepts `.name` attr or string)
- `series()` / `study()` / `patient()` — create entity creation flow
- `.on_status('status')` — trigger on status change
- `.on_finished()` — shorthand for `.on_status('finished')`
- `.on_data_update()` — trigger when finished record's data is updated via PATCH
- `.on_created()` — trigger on entity creation (for entity flows)
- `.on_creation()` — alias for `.on_created()`
- `.if_(condition)` / `.or_()` / `.and_()` — conditional logic (cross-record comparisons)
- `.if_record(F.x == val, F.y > 0, on_missing="skip"|"raise")` — self-referential conditions with AND semantics
- `.match(F.field)` — start pattern matching on a field; absorbs preceding `if_record()` as guard
- `.case(value)` — add a case branch (`guard AND field == value`); stop-on-first-match semantics
- `.default()` — fallback branch; fires only when no case matched (and guard is True)
- `.add_record('type', **kwargs)` → `CreateRecordAction` (supports `parent_record_id`, `inherit_user` kwargs)
- `.create_record('type1', 'type2', inherit_user=False)` — convenience wrapper calling `.add_record()` for each name
- `.update_record('name', status='new_status')` → `UpdateRecordAction`
- `.invalidate_records('type1', 'type2', mode='hard'|'soft', callback=fn)` → `InvalidateRecordsAction`
- `.invalidate_all_records(...)` — alias for `.invalidate_records()`
- `.pipeline('name', **extra_payload)` → `PipelineAction` (dispatches to pipeline service)
- `.do_task(task_func, **extra_payload)` → `PipelineAction` (auto-creates a single-step Pipeline named `_task:{task_name}` from a `@pipeline_task()`-decorated function; deduplicates across calls)
- `.call(func)` → `CallFunctionAction`
- `.else_()` — else branch
- `.is_active_flow()` — check if flow has triggers/actions (vs data-reference only)

## Triggers

- **`on_status('finished')`** — fires when record status changes to specified value
- **`on_data_update()`** — fires when `PATCH /records/{id}/data` updates a record's data
- **`on_file_change()`** — fires when `POST /records/{id}/check-files` detects changed checksums
- **Entity creation** — fires when a new entity (patient/study/series) is created

Record triggers (`on_status`, `on_data_update`, `on_file_change`) are mutually exclusive per FlowRecord instance. Use separate `record()` calls for different triggers on the same type.

Entity triggers use separate factory functions (`series()`, `study()`, `patient()`) and are stored in `ENTITY_REGISTRY`.

- **File update** — fires when a project-level file changes (detected by pipeline task pre/post checksum comparison)

File triggers use the `file()` factory function and are stored in `FILE_REGISTRY`.

## Invalidation

`invalidate_records()` searches by **patient_id** (broadest scope), enabling cross-level invalidation:
- Series-level change can invalidate patient-level records
- Patient-level change can invalidate series-level records

Modes:
- **hard**: reset status to `pending`, append reason to `context_info` (keeps `user_id`)
- **soft**: only append reason to `context_info`

Optional `callback(record, source_record, client)` for per-project custom behavior.

## User Inheritance & Parent Record

When a flow creates a child record from a record-triggered flow:

1. **`parent_record_id`**: Set explicitly via `add_record("type", parent_record_id=42)`. No auto-resolve — flows must specify parent links explicitly.

2. **`inherit_user` flag** (default `False`): Set `inherit_user=True` to inherit `user_id` from the triggering record. Without this flag, `user_id` is `None` for child records.

3. **Explicit `user_id`** in `add_record()` always takes priority over `inherit_user`.

```python
# Explicit parent link + user inheritance via API
record("parent_type").on_finished().add_record("child_type", parent_record_id=42)

# User inheritance without parent link
record("trigger").on_finished().add_record("unlinked_output", inherit_user=True)
```

## Registries

- `record()` → `RECORD_REGISTRY` (data-reference-only instances filtered by `is_active_flow()`)
- `series()`/`study()`/`patient()` → `ENTITY_REGISTRY`
- `file(file_obj)` → `FILE_REGISTRY` (accepts `.name` attr or string)

File flows: `.on_update()` + `.invalidate_all_records()` / `.call()`. Event source: `@pipeline_task` wrapper checksums → `POST /patients/{id}/file-events`.

## Data Access

Two patterns for referencing record data fields:

```python
# Cross-record: explicit record type name (creates side-effect FlowRecord)
record('report').data.findings.tumor_size
record('report').d.field_name               # Shorthand

# Self-referential: Field proxy for triggering record's own data
from clarinet.services.recordflow import Field
F = Field()
record("first_check")
    .on_status("finished")
    .if_record(F.is_good == True, F.study_type == "CT")
    .add_record("segment_CT")
```

`if_record(*conditions, on_missing="skip")` — AND semantics for multiple conditions.
`on_missing="skip"` (default): missing/None fields → condition evaluates to False.
`on_missing="raise"`: missing fields → propagate error.

Comparison operators: `==`, `!=`, `<`, `<=`, `>`, `>=`

## Match/Case — Pattern Matching

`.match(F.field).case(value).action()` — Python-like pattern matching with stop-on-first-match.

```python
record("first_check").on_finished().if_record(F.is_good == True)
    .match(F.study_type)
    .case("CT").create_record("seg_CT_single", "seg_CT_archive")
    .case("MRI").create_record("seg_MRI_single")
    .default().create_record("seg_unknown")
```

- `.match(field)` absorbs preceding `if_record()` as guard; assigns `match_group` id
- `.case(value)` — stop-on-first-match within group
- `.default()` — fires only when no case matched (and guard is True)
- `on_missing` from `if_record()` propagates to all cases

## Engine Setup

```python
from clarinet.services.recordflow import RecordFlowEngine, discover_and_load_flows, load_flows_from_file
from pathlib import Path

engine = RecordFlowEngine(client)
# discover_and_load_flows accepts directories OR individual files:
discover_and_load_flows(engine, [Path('flows/')])
# or load a single file directly:
# load_flows_from_file(Path('flows/ct_flow.py'))  # clears RECORD_REGISTRY/ENTITY_REGISTRY/FILE_REGISTRY first

await engine.handle_record_status_change(record, old_status)
await engine.handle_record_data_update(record)  # For data update triggers
await engine.handle_entity_created("series", patient_id, study_uid, series_uid)
await engine.handle_file_update("master_model", patient_id)  # For file change triggers
```

**Loader implementation**: uses `importlib.util.spec_from_file_location()` to load flow files as modules (replaces former `exec()`). `load_flows_from_file()` clears `RECORD_REGISTRY`, `ENTITY_REGISTRY`, and `FILE_REGISTRY` before each file load to prevent duplicate registrations.

## API Integration

Triggers are dispatched via the **service layer** (awaited during request), not from routers:
- `RecordService` fires record-level triggers on `update_status`, `submit_data`, `update_data`, `notify_file_change`, `bulk_update_status`, `notify_file_updates`
- `StudyService` fires entity triggers via `engine.fire()` (fire-and-forget) on entity creation
- `POST /records/{id}/invalidate` → hard mode fires RecordFlow triggers
- `POST /patients/{id}/file-events` → `notify_file_updates()` (called by pipeline task wrapper)

## Configuration

Set in `clarinet/settings.py`:
- `recordflow_enabled` (bool, default False) — enable engine
- `recordflow_paths` (list[str], default []) — directories with `*_flow.py` files
