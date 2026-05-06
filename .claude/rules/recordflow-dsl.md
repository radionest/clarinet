---
paths:
  - "clarinet/services/recordflow/**"
  - "tasks/**/*_flow.py"
---

# RecordFlow DSL — Full API Reference

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
- `.update_record('name', status='new_status', strategy='single'|'all')` → `UpdateRecordAction`. `strategy='single'` (default): skip with error log if context contains 0 or >1 matching records. `strategy='all'`: apply to every match.
- `.invalidate_records('type1', 'type2', mode='hard'|'soft', callback=fn)` → `InvalidateRecordsAction`
- `.invalidate_all_records(...)` — alias for `.invalidate_records()`
- `.pipeline('name', **extra_payload)` → `PipelineAction` (dispatches to pipeline service)
- `.do_task(task_func, **extra_payload)` → `PipelineAction` (auto-creates a single-step Pipeline named `_task:{task_name}` from a `@pipeline_task()`-decorated function; deduplicates across calls)
- `.call(func)` → `CallFunctionAction` — invoke arbitrary callable; model fields: `function` (Callable), `args` (tuple), `extra_kwargs` (dict). Engine invokes `function(*args, **extra_kwargs)`
- `.else_()` — else branch
- `.is_active_flow()` — check if flow has triggers/actions (vs data-reference only)

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

## Tree-filtered context

When a record-trigger fires, the engine builds `record_context: dict[str, list[RecordRead]]`
filtered by the **DICOM tree slice** of the trigger — `ancestors(trigger) ∪ subtree(trigger)`.
Sibling branches are excluded:

| Trigger.level | Records in context |
|---|---|
| `SERIES` | PATIENT-level (same `patient_id`) + STUDY-level (same `study_uid`) + SERIES-level (same `series_uid`) |
| `STUDY` | PATIENT-level (same `patient_id`) + STUDY-level (same `study_uid`) + SERIES-level of any series in that study |
| `PATIENT` | every record of the patient (entire subtree) |

Multiple records of the same type may appear in one list (e.g. PATIENT-trigger sees one
`first-check` per study). Use a strategy modifier on `record(...)`:

```python
# Default — single record expected; >1 raises AmbiguousContextError
record('first-check').d.is_good == True

# At least one record matches
record('first-check').any().d.is_good == True

# Every record matches (empty list ⇒ False)
record('measurement').all().d.value > 100
```

Two multi-valued sides in one comparison (`record('a').any() == record('b').any()`)
is unsupported — reduce one side to a single record or constant.

`Field()` / `F.x` self-references always resolve to the trigger record (single).

### Custom callbacks (`.call(func)`)

`func` receives `context: dict[str, list[RecordRead]]` (same tree-filtered map as
the DSL conditions see). Earlier this kwarg was `dict[str, RecordRead]`; downstream
callbacks that read it must iterate the list. `record`, `client`, and any
`extra_kwargs` from `.call(func, **kwargs)` are unchanged.
