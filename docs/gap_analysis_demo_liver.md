# Gap Analysis: demo_liver DSL vs Current Clarinet Implementation

## Context

`examples/demo_liver/` describes a target DSL for a liver metastasis segmentation study. This analysis identifies what functionality is **missing** in the current Clarinet codebase to fully support this DSL. The analysis is organized from most impactful to least.

---

## 1. Record Model: `parent_record_id` (NOT EXISTS)

**Target DSL** (README.md): Records like `compare_with_projection`, `second_review`, `update_master_model` reference a parent record via `parent_record_id` FK.

**Current state**: `Record` model (`src/models/record.py`) has no `parent_record_id` field.

**What's needed**: FK field `parent_record_id` on `Record` pointing to `Record.id`, one-to-many relationship. Alembic migration. API support in `RecordCreate`, `RecordRead`, `RecordFind`. RecordFlow engine must pass parent context when creating child records.

---

## 2. RecordFlow DSL: `file().on_update()` Trigger (NOT EXISTS)

**Target DSL** (`pipeline_flow.py:178`):
```python
file(master_model).on_update().invalidate_all_records("create_master_projection")
```

**Current state**: `on_file_change()` exists but triggers on a **record's** file checksum change (`POST /records/{id}/check-files`). There is no `file()` factory that binds to a **project-level file catalog entry** and triggers when that file changes anywhere in the patient's storage.

**What's needed**: New `file()` factory function + `on_update()` trigger. `FILE_REGISTRY` in the DSL registry. Engine dispatch for file-level events. This is the biggest architectural gap -- requires event propagation from file system or checksum monitoring.

---

## 3. RecordFlow DSL: `F` (Field) Proxy for Conditions (NOT EXISTS)

**Target DSL** (`pipeline_flow.py:85`):
```python
.if_record(F.is_good == True, F.study_type == "CT")
```

**Current state**: Conditions use `record('type').data.field == val` which references **another** record's data. No `F` proxy exists for conditions on the **triggering** record's own data. No `.if_record()` method.

**What's needed**:
- `F` / `Field` class in `src/services/recordflow/` -- self-referential proxy
- `.if_record(*conditions)` method on `FlowRecord` -- multi-condition shorthand (AND semantics)
- Engine evaluation: resolve `F` fields against the triggering record's data

---

## 4. RecordFlow DSL: `.do_task(fn)` -- Unified Task Dispatch (NOT EXISTS)

**Target DSL** (`pipeline_flow.py:137`):
```python
record(seg_type).on_finished().do_task(init_master_model)
```

**Current state**: Two separate methods: `.call(func)` (sync/async function in-process) and `.pipeline('name')` (distributed task queue). The target `.do_task(fn)` takes a `@task`-decorated function and dispatches it to the pipeline queue.

**What's needed**: `.do_task(fn)` method on `FlowRecord` that creates a `PipelineAction` from a task-decorated function. Requires integration between RecordFlow actions and the pipeline task registry.

---

## 5. RecordFlow DSL: `.on_finished()` Shorthand (NOT EXISTS)

**Target DSL**: Used throughout `pipeline_flow.py`.

**Current state**: Only `.on_status('finished')` exists.

**What's needed**: Simple alias method on `FlowRecord`:
```python
def on_finished(self) -> FlowRecord:
    return self.on_status("finished")
```

Trivial to implement. Also consider `.on_pending()` etc.

---

## 6. RecordFlow DSL: `.create_record("a", "b")` -- Multi-type Creation (NOT EXISTS)

**Target DSL** (`pipeline_flow.py:86`):
```python
.create_record("segment_CT_single", "segment_CT_with_archive")
```

**Current state**: `.add_record(record_type_name)` accepts only ONE type name.

**What's needed**: New `.create_record(*record_type_names, **kwargs)` that creates multiple `CreateRecordAction`s. Can coexist with `.add_record()` as an alias or replacement.

---

## 8. Settings: `extra_roles` (NOT EXISTS)

**Target DSL** (`settings.toml:1`):
```toml
extra_roles = ["doctor_CT", "doctor_MRI", "doctor_PDCT", "doctor_CT-AG"]
```

**Current state**: `src/settings.py` has no `extra_roles` setting. `UserRole` model exists with standard roles, but dynamic project-specific roles aren't supported.

**What's needed**: `extra_roles: list[str]` in settings. Bootstrap logic to auto-create `UserRole` entries from this list. RecordType config uses `role = "doctor_CT"` which maps to `role_name` FK.

---

## 9. Model Rename: `min_users`/`max_users` -> `min_records`/`max_records` (RENAME)

**Target DSL**: All TOML configs use `min_records`/`max_records`.

**Current state**: `RecordTypeBase` has `min_users`/`max_users` fields (`src/models/record_type.py:52-53`).

**What's needed**: Rename fields + Alembic migration + update all references (API, frontend, config loader, reconciler, tests).

---

## 10. Public API: `from clarinet.flow import ...` (NOT EXISTS)

**Target DSL** (`pipeline_flow.py:9`):
```python
from clarinet.flow import Field as F, file, record, study, task
```

**Current state**: Internal imports `from src.services.recordflow import record, series, study`. No `clarinet.flow` public package.

**What's needed**: `clarinet/flow.py` (or `src/flow.py`) re-exporting DSL primitives. `task` decorator re-export from pipeline. Package-level `__init__.py` adjustments.

---

## 11. Frontend: Multiple StudyInstanceUIDs Support (PARTIAL)

**Current state** (`src/frontend/src/utils/viewer.gleam:11`): Builds URL with single UID:
```gleam
let base = "/ohif/viewer?StudyInstanceUIDs=" <> study_uid
```

**What's needed**: Accept `list[str]` of UIDs, join with `&StudyInstanceUIDs=`. OHIF natively supports this. Requires `lifecycle_open` (item 7) to populate the additional UIDs.

---

## 12. RecordFlow DSL: `.on_status("pending")` for Auto-records (EXISTS but needs verification)

**Target DSL** (`pipeline_flow.py:152`):
```python
record("compare_with_projection").on_status("pending").do_task(compare_w_projection)
```

**Current state**: `.on_status('pending')` should work -- engine fires on any status change. But the flow needs `role="auto"` to mean "no user assignment, filled by pipeline". The `RecordFlowEngine` must properly trigger on initial record creation -> status=pending.

**What may be needed**: Verify engine triggers on initial creation status. Ensure auto-role records aren't shown in user task queues.

---

## 13. Pipeline DSL: `@task` Decorator Naming (MINOR ALIGNMENT)

**Target DSL** (`pipeline_flow.py:18`):
```python
from clarinet.flow import task

@task
def init_master_model(msg, ctx): ...
```

**Current state**: `@pipeline_task()` decorator in `src/services/pipeline/task.py`. The target DSL uses a simpler `@task` name.

**What's needed**: Alias or rename. Minor.

---

## 14. TaskContext API: `ctx.files.get()` / `ctx.files.get_path()` (NAMING MISMATCH)

**Target DSL** (`pipeline_flow.py:30-35`):
```python
ctx.files.get(segmentation_single, uid=msg["series_uid"])  # returns loaded file path?
ctx.files.get_path(master_model)                            # returns path
```

**Current state**: `FileResolver` has `.resolve(file_def)` -> Path, `.exists(file_def)` -> bool. No `.get()` or `.get_path()` methods.

**What's needed**: `.get()` could be alias for `.resolve()`. Or `.get()` could accept a catalog `File` object directly (not just `FileDefinitionRead`). Minor naming alignment.

---

## Summary Table

| # | Feature | Status | Effort |
|---|---------|--------|--------|
| 1 | `parent_record_id` FK | NOT EXISTS | Medium |
| 2 | `file().on_update()` trigger | NOT EXISTS | High |
| 3 | `F` (Field) proxy for conditions | NOT EXISTS | Medium |
| 4 | `.do_task(fn)` method | NOT EXISTS | Medium |
| 5 | `.on_finished()` shorthand | NOT EXISTS | Trivial |
| 6 | `.create_record("a", "b")` multi-type | NOT EXISTS | Low |
| 7 | `lifecycle_open` on RecordType | NOT EXISTS | High |
| 8 | `extra_roles` in settings | NOT EXISTS | Low |
| 9 | `min_users` -> `min_records` rename | RENAME | Low |
| 10 | `from clarinet.flow` public API | NOT EXISTS | Low |
| 11 | Multiple StudyInstanceUIDs (frontend) | PARTIAL | Low |
| 12 | Auto-records (`role=auto`) pipeline flow | VERIFY | Low |
| 13 | `@task` decorator alias | MINOR | Trivial |
| 14 | `ctx.files.get()`/`get_path()` naming | NAMING | Trivial |

### Already implemented:
- `RecordStatus.blocked` -- EXISTS
- `FileDefinition.level` (PATIENT/STUDY/SERIES) -- EXISTS
- `file_registry.toml` shared file definitions -- EXISTS
- `RecordTypeFileLink` M2M (role, required) -- EXISTS
- `FileResolver` with `resolve()`, `exists()`, `glob()` -- EXISTS
- `study().on_created().add_record()` -- EXISTS
- `record().on_status()`, `.if_()`, `.add_record()`, `.call()`, `.pipeline()` -- EXISTS
- `.invalidate_records()` with mode hard/soft -- EXISTS
- `slicer_script` on RecordType -- EXISTS
- `role_name` FK on RecordType -- EXISTS
- `TaskContext` + `@pipeline_task()` decorator -- EXISTS
