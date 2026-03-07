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
- `CreateRecordAction(record_type_name, series_uid?, user_id?, parent_record_id?, context_info?)`
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
- `.add_record('type', **kwargs)` → `CreateRecordAction` (supports `parent_record_id` kwarg; inherits `user_id` from triggering record)
- `.create_record('type1', 'type2')` — convenience wrapper calling `.add_record()` for each name
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

## File Flows (`flow_file.py`)

`file(file_obj)` creates a `FlowFileRecord` and adds it to `FILE_REGISTRY`. Accepts any object with `.name` attribute or a plain string.

DSL methods:
- `.on_update()` — trigger when file changes (checksum comparison)
- `.invalidate_all_records('type1', 'type2', mode='hard'|'soft', callback=fn)` → `InvalidateRecordsAction`
- `.call(func)` → `CallFunctionAction` (receives `file_name`, `patient_id`, `client` kwargs)

Engine methods:
- `engine.handle_file_update(file_name, patient_id)` — main entry point
- File flows are stored in `engine.file_flows` dict (keyed by file name)
- `_invalidate_by_file()` — like `_invalidate_records` but without source record (no self-skip)

Event source: `@pipeline_task` wrapper computes pre/post checksums, notifies API via `POST /patients/{id}/file-events`.

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

## Match/Case — Pattern Matching (Python-style semantics)

When multiple conditions share the same trigger and guard but differ only by one field value, use `match()/case()/default()` for Python-like pattern matching:

```python
(
    record("first_check")
    .on_finished()
    .if_record(F.is_good == True)
    .match(F.study_type)
    .case("CT").create_record("seg_CT_single", "seg_CT_archive")
    .case("MRI").create_record("seg_MRI_single")
    .case("CT-AG").create_record("seg_CTAG_single")
    .default().create_record("seg_unknown")
)
```

**How it works:** Each `.case(value)` generates a `FlowCondition` with `match_group` set. The engine uses `match_group` for stop-on-first-match: once a case matches, remaining cases in the same group are skipped. `default()` fires only when no case in the group matched.

- `.match(field)` — saves the match field and assigns a unique `match_group` id; absorbs a preceding `if_record()` (without actions) as guard
- `.case(value)` — creates `FlowCondition(guard AND field == value, match_group=id)`
- `.default()` — creates `FlowCondition(guard, is_else=True, match_group=id)`; fires only when no case in the group matched and the guard (if any) is True
- **Stop-on-first-match**: the engine skips remaining cases after the first match in a group
- Guard is optional: `match(F.x).case("a")` works without `if_record()`
- When guard is False, neither cases nor default fire
- `on_missing` from `if_record()` propagates to all case and default conditions
- `validate()` fails if `match()` has no `case()` branches

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

- `PATCH /records/{id}/status` triggers `handle_record_status_change` via BackgroundTasks
- `PATCH /records/{id}/data` triggers `handle_record_data_update` via BackgroundTasks
- `POST /records/{id}/invalidate` — direct invalidation endpoint (mode, source_record_id, reason)
- `POST /patients`, `POST /studies`, `POST /series` trigger `handle_entity_created` via BackgroundTasks
- `POST /dicom/import-study` triggers `handle_entity_created` for each imported series
- `POST /patients/{id}/file-events` triggers `handle_file_update` via BackgroundTasks (called by pipeline task wrapper)

## Configuration

Set in `clarinet/settings.py`:
- `recordflow_enabled` (bool, default False) — enable engine
- `recordflow_paths` (list[str], default []) — directories with `*_flow.py` files
