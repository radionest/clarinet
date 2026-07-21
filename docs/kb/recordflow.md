---
type: Subsystem
title: RecordFlow workflow engine
description: The event-driven DSL that creates, updates and invalidates records when statuses change, data is submitted or files move — triggers, actions, evaluation context and invalidation semantics.
tags: [recordflow, workflow, dsl, invalidation, triggers]
timestamp: 2026-07-21T19:46:32Z
---

RecordFlow is what turns a pile of record types into a study protocol: it
reacts to events and creates the next piece of work. It is **disabled by
default** (`recordflow_enabled = False`) and reads `*_flow.py` files from
`recordflow_paths`, which must live inside `config_tasks_path` — see
[The clarinet_plan package](/plan-package.md).

```python
from clarinet.services.recordflow import Field, record, study, file

F = Field()

# a new study arrives -> create the initial assessment
study().on_creation().create_record("first-check")

# assessment finished and good -> branch on modality
(
    record("first-check")
    .on_finished()
    .if_record(F.is_good == True)
    .match(F.study_type)
    .case("CT").create_record("segment-ct", "segment-ct-archive")
    .case("UT").create_record("segment-ut")
)

# a project-level file changed -> invalidate everything derived from it
file("master_model").on_update().invalidate_all_records("create-projection")
```

## Triggers

| Trigger | Fires when |
|---|---|
| `.on_status('x')` / `.on_finished()` | a record's status changes to `x` |
| `.on_data_update()` | `PATCH /records/{id}/data` updates a finished record |
| `.on_file_change()` | `POST /records/{id}/check-files` sees changed checksums |
| `.on_created()` / `.on_creation()` | a patient, study or series entity is created |
| `.on_update()` on `file(...)` | a pipeline task's pre/post checksum comparison reports a changed project-level file |

The three record triggers are mutually exclusive **per `record()` instance** —
use separate `record()` calls for different triggers on the same type. Factories
route to three registries: `record()` → `RECORD_REGISTRY`,
`series()`/`study()`/`patient()` → `ENTITY_REGISTRY`, `file()` → `FILE_REGISTRY`.

## Actions

Actions are typed Pydantic models, not dicts, and the engine dispatches on
`isinstance()`:

| DSL | Action model |
|---|---|
| `.add_record()` / `.create_record()` | `CreateRecordAction` |
| `.update_record(name, status=, strategy=)` | `UpdateRecordAction` |
| `.invalidate_records(*types, mode=, callback=)` | `InvalidateRecordsAction` |
| `.pipeline(name)` / `.do_task(fn)` | `PipelineAction` — see [Pipeline](/pipeline.md) |
| `.call(fn)` | `CallFunctionAction` |

Conditions come in two flavours: `.if_(...)` for cross-record comparisons
(`record('a').data.x != record('b').data.x`) and `.if_record(F.x == v, ...)` for
self-referential ones, with AND semantics and `on_missing="skip"` by default.
`.match(F.field).case(v).default()` gives stop-on-first-match pattern matching
and absorbs a preceding `if_record()` as its guard. Full method reference:
`.claude/rules/recordflow-dsl.md`.

## Evaluation context

When a record trigger fires, the engine builds
`record_context: dict[str, list[RecordRead]]` filtered to the **DICOM tree
slice** of the trigger — `ancestors(trigger) ∪ subtree(trigger)`. Sibling
branches are excluded.

| Trigger level | Records visible |
|---|---|
| `SERIES` | patient-level + that study + that series |
| `STUDY` | patient-level + that study + every series in it |
| `PATIENT` | the patient's entire subtree |

Because a list can hold several records of one type, `record('X')` defaults to
"exactly one expected" and raises `AmbiguousContextError` on more. Reduce with
`.any()` (at least one matches) or `.all()` (every one matches; empty ⇒ False).
`Field()` / `F.x` always resolves to the single trigger record.

Custom `.call(func)` callbacks receive the same dict as their `context` kwarg —
it maps to **lists**, so iterate when reading.

## Invalidation

`invalidate_records()` searches by `patient_id`, the broadest scope, so
invalidation crosses levels freely: a series-level change can invalidate
patient-level records and vice versa.

- **hard** — reset status to `pending`, append the reason to `context_info`,
  keep `user_id`. Always fires `on_status("pending")`.
- **soft** — append the reason only; never changes status, never fires triggers.

Two consequences that are easy to get wrong:

1. **Every action reachable from `on_status('pending')` must be idempotent.**
   Hard invalidation re-fires even when the record was already `pending`, so the
   same flow may run many times for one record. Guard duplicate `create_record`
   with RecordType constraints (`max_records`, `unique_by`) or an existence
   check inside a `.call()`.
2. **Mutual hard-invalidation loops are a configuration error.** The engine cuts
   cycles at runtime — a record already mid-cascade is still invalidated, but its
   flows are skipped with an `Invalidation cycle detected` ERROR log.

## Two inheritance axes

Do not confuse them:

| Mechanism | Source of `user_id` | Applied by |
|---|---|---|
| `inherit_user=True` in `add_record()` | the **triggering** record | the engine, sent as an explicit `user_id` |
| `RecordType.inherit_user_from_parent` | the **parent** record (`parent_record_id`) | `RecordService.create_record`, only when no explicit `user_id` arrived |

An explicit `user_id` in `add_record()` always wins. `parent_record_id` is never
auto-resolved — flows must state parent links explicitly.

## How triggers get dispatched

Never from routers. `RecordService` wraps the record mutations
(`update_status`, `assign_user`, `submit_data`, `update_data`,
`notify_file_change`, `bulk_update_status`, `notify_file_updates`) and awaits
the matching engine trigger; `StudyService` fires entity triggers
fire-and-forget via `engine.fire()`. The engine is injected by
`get_recordflow_engine(request)` and is `None` when disabled.

## Loading and visualisation

`load_and_register_flows()` clears the four registries (three above plus
`call_function_registry`) **once per load cycle**, imports every file, then
collects flows once. Flow files may import siblings in either direction using
the `clarinet_plan.` prefix or relative imports.

`clarinet/services/workflow_graph/` renders the whole thing: a pure
`build_graph(engine, pipelines, ...)` walks the registries and emits nodes and
edges, `apply_layout()` assigns coordinates with a cycle-tolerant Kahn layered
layout, and the frontend draws native SVG. An `Edge.firings` list that is empty
means "potential"; non-empty means it actually fired. Admin endpoints under
`/api/admin/workflow` also offer dry-run, fire and dispatch with a plan digest
so a stale plan cannot be replayed.
