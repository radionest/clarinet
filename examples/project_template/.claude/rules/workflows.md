---
paths:
  - "plan/workflows/**"
---

# The `plan/workflows/` section

Contains files shaped like `*_flow.py` (`pipeline_flow.py` by default), whose path is set in `settings.toml` (`recordflow_paths`). Each file is two things in one:

1. **Pipeline tasks** — functions decorated with `@pipeline_task`, running in TaskIQ workers.
2. **RecordFlow DSL** — declarative rules linking event triggers to actions (creating a record, running a task, invalidation).

## Structure of `pipeline_flow.py`

Canonical order:

```python
from __future__ import annotations
# 1. Imports
from clarinet.services.pipeline import (
    PipelineMessage, SyncTaskContext, TaskContext, pipeline_task,
)
from clarinet.services.recordflow import Field, file, record, study
from clarinet.utils.logger import logger
from clarinet_plan.definitions.record_types import segmentation, master_model

F = Field()

# 2. Pipeline task functions (all @pipeline_task at the top)
@pipeline_task()
def my_task(...): ...

# 3. Async callback functions for .call(...)
async def my_callback(record, context, client): ...

# 4. Flow declarations (one per set of parentheses)
(record("foo").on_finished().do_task(my_task))
(record("bar").on_finished().call(my_callback))
```

`plan/` is available as the `clarinet_plan` package (single root — `config_tasks_path`); import record types via `clarinet_plan.definitions.record_types`. Relative imports are allowed within `workflows/` (`from .tasks import ...`). `sys.path` is not used. Cross-flow imports work regardless of file order.

---

## Part A — Pipeline tasks (`@pipeline_task`)

### Pipeline task vs. RecordFlow action

- **Pipeline task** — long or heavy work that needs to be isolated in a worker: loading from PACS, DICOM → NIfTI conversion, image processing (skimage, SimpleITK), GPU inference, calls to external APIs.
- **RecordFlow action** (`create_record`, `update_record`, `invalidate_records`) — a fast declarative hookup that runs synchronously when the trigger fires.

If a step takes <50ms and does no I/O, it's an action. If it reads files, hits the DB, or crunches arrays, it's a task.

### Decorator

```python
@pipeline_task(queue="clarinet.dicom", auto_submit=False)
async def my_task(msg: PipelineMessage, ctx: TaskContext) -> None: ...
```

| Parameter | Purpose |
|---|---|
| `queue` | The TaskIQ queue. Defaults to `"default"`. Built-in: `"clarinet.dicom"` (DICOM tasks). You can set up your own via `pipeline_default_timeout` etc. in settings. |
| `auto_submit` | If `True` and the task returns a `dict`, the framework automatically calls `submit_record_data(msg.record_id, result)`. Convenient for short, pure functions. |

### Async vs sync

- **Async** — for I/O, HTTP, DB, and all `ctx.client.*` / `ctx.records.*` calls. Receives `TaskContext`.
- **Sync** — for CPU-bound work (skimage, SimpleITK, vtk, blocking libraries). Receives `SyncTaskContext`. The framework automatically detects a sync function and runs it in a thread so it doesn't block the event loop.

```python
@pipeline_task()
async def fetch_dicom(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Async — I/O/HTTP/DB."""
    ...

@pipeline_task()
def process_volume(msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    """Sync — numpy/skimage/etc."""
    ...
```

### `PipelineMessage`

```python
class PipelineMessage:
    patient_id: str | None
    study_uid: str | None
    series_uid: str | None
    record_id: int | None
    pipeline_id: str | None  # pipeline name (for multi-step pipelines)
    step_index: int | None
    payload: dict[str, Any]  # any kwargs from .do_task(task, foo=bar)
```

Which fields are populated depends on the DSL trigger: `record("X").on_finished().do_task(my_task)` will pass `record_id` plus that record's DICOM-hierarchy fields.

### Task contexts

`TaskContext` (async) and `SyncTaskContext` (sync) give access to:

#### `ctx.files` — `Files`

```python
ctx.files.resolve(file_def) -> Path        # absolute path to the file
ctx.files.exists(file_def) -> bool         # does the file exist
ctx.files.glob(file_def) -> list[Path]     # all files in a glob collection (multiple=True)
ctx.files.dir() -> Path                    # the record's working folder (at its own level)
```

`resolve`/`exists`/`glob` accept either a `FileDef` object (imported from `record_types.py`) or a string `name`.

`ctx.files` is a `Files` instance bound to the task's **own** record
(`msg.record_id`). To get file paths for **another** record you already have
in hand (a parent, a re-fetched copy, another patient's record), use
`ctx.files_for(record)`:

```python
parents = await ctx.records.find("study-root", study_uid=msg.study_uid)
if parents:
    parent_files = ctx.files_for(parents[0])  # -> Files
    mask = parent_files.resolve("liver_mask")
```

`ctx.files_for` exists on both `TaskContext` (async) and `SyncTaskContext`
(sync) — it's a wrapper around the `Files(record)` constructor **without**
`parent`. The framework itself builds `ctx.files` as
`Files(record, parent=parent)`, so placeholders that fall back to the parent
(`{user_id}`, `{origin_type}`, merged `{data.FIELD}`) may resolve differently
via `ctx.files_for` — if you need parent-fallback, build the facade yourself:
`Files(record, parent=parent_record)`. The same facade is also available
outside a task (standalone scripts without `ctx`):

```python
from clarinet.files import Files

files = Files(record)  # resolver for a record (file-registry + working dirs)
```

`Files(...)` accepts `RecordRead` / `SeriesRead` / `StudyRead` / `PatientRead`.
The file-registry (and hence resolving by string name, `resolve("mask")`) is
only available on `RecordRead`; `resolve(file_def)` with the `FileDef` object
itself, and `.dir()`, work for any entity whose level is known. To find a file
**by criteria** (rather than from an already-fetched record), use
`ctx.records.file_path(...)`.

`Files(record)` runs in strict mode: for a not-yet-anonymized record,
`AnonPathError` is raised already **at construction time** (in the
constructor, not in `.resolve()`) — you need to wrap the `Files(record)` call
itself in try/except. If you need access to the non-anonymized layout
(UX previews, migration scripts), build the facade in lenient mode —
`Files(record, fallback=True)` or `Files.for_reader(record)` (tries strict
mode first, and on `AnonPathError` rebuilds with a raw-UID fallback).

#### `ctx.records` — `RecordQuery`

```python
await ctx.records.find(
    "first-check",
    patient_id=msg.patient_id,
    study_uid=msg.study_uid,
)  # -> list[RecordRead]
```

The sync variant — without `await`.

#### `ctx.client` — `ClarinetClient`

An HTTP client to the project's own API. Main methods (full list — in `clarinet/client.py`):

```python
await ctx.client.get_record(record_id)
await ctx.client.find_records(record_type_name="segment-ct-single", **filters)
# find_records only returns the first page; for per-patient aggregation /
# queries without a series/study filter, use iter_records (paginates all pages):
records = [r async for r in ctx.client.iter_records(patient_id=...)]
await ctx.client.create_record(RecordCreate(...))
await ctx.client.submit_record_data(record_id, data, status="finished")
await ctx.client.update_record(record_id, **updates)
await ctx.client.invalidate_record(
    record_id, mode="hard", source_record_id=..., reason="..."
)
await ctx.client.get_study(study_uid)
await ctx.client.anonymize_patient(patient_id)
```

The sync counterpart — `SyncPipelineClient` via `ctx.client` in sync tasks.

### Idempotency — a mandatory contract

Every task must be **idempotent**: calling it again with the same message must not break anything. Standard pattern:

```python
@pipeline_task()
def init_master_model(_msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    if ctx.files.exists(master_model):
        return  # already done — exit
    ...
    save_seg_nrrd(...)
```

Reasons:
- A worker may retry a task on failure (`pipeline_retry_count`, `pipeline_retry_delay`).
- Cascade invalidation can recreate a record and re-queue the task.
- Manual pipeline restarts for debugging.

### Logging

```python
from clarinet.utils.logger import logger
logger.info(f"Processing record {msg.record_id}")
logger.error(f"Failed to read {seg_path}: {exc}")
```

Only f-strings, never `print()`, never `import loguru`.

### Built-in tasks

- `convert_series_to_nifti` — converts a DICOM series to NIfTI via C-GET. Queue `clarinet.dicom`. Idempotent (checks `volume.nii.gz`).
- `_convert_series_impl(msg, ctx)` — the internal function for direct use inside custom tasks (if you need to both load NIfTI and do something else in a single task).
- `anonymize_study_pipeline` — Record-aware DICOM anonymization: PACS → anonymize → distribute → submit to the Record. Queue `clarinet.dicom`. Requires `msg.record_id`. See `anonymization.md`.
- `prefetch_dicom_web` — prefetches a study into the DICOMweb disk cache via C-GET. Queue `clarinet.dicom`. Requires `msg.study_uid`. Idempotent.

A custom task's name must not collide with a built-in one — otherwise `register_task()` raises `PipelineConfigError`. The collision is on the **bare function name**: task names are `{namespace}:{function_name}`, not module-qualified, so a `plan/` task re-using a built-in's function name is rejected as soon as anything imports that built-in. See `anonymization.md` for the trap people hit most.

### Minimal example

```python
@pipeline_task()
def init_master_model(_msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    """Create the master model from the first completed segmentation."""
    if ctx.files.exists(master_model):
        return

    seg_path = ctx.files.resolve(segmentation)
    if not seg_path.is_file():
        raise FileNotFoundError(f"Segmentation file not found: {seg_path}")

    master_path = ctx.files.resolve(master_model)
    Path(master_path).parent.mkdir(parents=True, exist_ok=True)
    # ... numpy/skimage processing ...
    logger.info(f"Created master model at {master_path}")
```

---

## Part B — RecordFlow DSL

> Full DSL reference — `<clarinet>/clarinet/.claude/rules/recordflow-dsl.md`. Here's a compact overview for everyday use.

### Triggers

```python
study().on_creation()        # a new study arrived
series().on_creation()       # a new series appeared
patient().on_creation()      # a new patient

record("type-name").on_status("pending")    # a record transitioned to this status
record("type-name").on_finished()            # alias for on_status("finished")
record("type-name").on_data_update()         # PATCH of data on an already-finished record

file(file_def).on_update()    # the file changed (for cascade invalidation)
```

`file(...)` accepts either a `FileDef` object or a string `name`. The source of file events is `@pipeline_task`, via checksum-comparison middleware.

`on_status("pending")` also fires on hard invalidation of a record that was already `pending` — re-invalidating it restarts the flow. All actions in such flows (`.do_task`, `.call`, and especially `.add_record`) must be idempotent. Mutual hard invalidations (A↔B, or records of the same type invalidating each other) are a configuration error: the engine breaks the cycle and logs an ERROR, `Invalidation cycle detected`.

### Conditions

```python
F = Field()

# Comparing record.data fields
.if_record(F.is_good == True)
.if_record(F.confidence < 0.7, F.modality == "CT")  # AND semantics
.if_record(F.x == y, on_missing="raise")             # default "skip" → False

# Pattern matching on a field
.match(F.study_type)
    .case("CT").create_record("segment-ct")
    .case("MRI").create_record("segment-mri")
    .default().create_record("segment-unknown")
```

`.match()` absorbs the preceding `.if_record()` as a guard. Stop-on-first-match. `.default()` fires only if no `case` matched and the guard is true.

### Actions

```python
.create_record("type1", "type2", inherit_user=False)   # one or several
.do_task(my_task, extra_payload_key="value")           # run a @pipeline_task
.pipeline("named_pipeline", **payload)                 # run a named pipeline
.call(async_callback)                                  # call an async function
.invalidate_records("type1", "type2", mode="hard")     # cascading invalidation
.invalidate_all_records("type")                        # alias for a single type
```

#### `.do_task` vs `.pipeline`

- `.do_task(func)` — for a single-step job. The framework automatically creates a one-step pipeline `_task:func_name` and deduplicates it.
- `.pipeline("name")` — for a named multi-step pipeline (if you've built `Pipeline("name").step(...).step(...)` in code).

#### `.call(callback)` — when the DSL isn't enough

Used when you need `parent_record_id` or logic too complex to express in the DSL:

```python
async def create_comparison_record(
    record: RecordRead,
    context: dict[str, Any],
    client: ClarinetClient,
) -> None:
    await client.create_record(
        RecordCreate(
            record_type_name="compare-with-projection",
            parent_record_id=record.id,  # link to the trigger as parent
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=record.series_uid,
        )
    )

(record("segment-ct-single").on_finished().call(create_comparison_record))
```

### Cross-record references

Comparing fields across **different** records (not self-referential):

```python
record("ai-analysis").on_finished().if_(
    record("ai-analysis").data.diagnosis != record("doctor-review").data.diagnosis
).create_record("expert-check")
```

`record("type").data.X` creates a side-effect FlowRecord that the engine resolves when evaluating the condition.

### Common patterns

```python
# 1. When a study arrives — create the initial check
(study().on_creation().create_record("first-check"))

# 2. After first-check — branch by study type
(
    record("first-check").on_finished()
    .if_record(F.is_good == True)
    .match(F.study_type)
    .case("CT").create_record("segment-ct-single")
    .case("MRI").create_record("segment-mri-single")
)

# 3. Segmentation → automatic comparison
(record("segment-ct-single").on_finished().call(create_comparison_record))
(record("compare-with-projection").on_status("pending").do_task(compare_w_projection))

# 4. Cascade invalidation when the master model changes
(file(master_model).on_update().invalidate_all_records("create-master-projection"))
```
