# Gap Analysis: demo_liver DSL vs Current Clarinet Implementation

## Context

`examples/demo_liver/` describes a target DSL for a liver metastasis segmentation study. This analysis identifies what functionality is **missing** in the current Clarinet codebase to fully support this DSL. The analysis is organized from most impactful to least.

---

---

## 2. RecordFlow DSL: `file().on_update()` Trigger (NOT EXISTS)

**Target DSL** (`pipeline_flow.py:178`):
```python
file(master_model).on_update().invalidate_all_records("create_master_projection")
```

**Current state**: `on_file_change()` exists but triggers on a **record's** file checksum change (`POST /records/{id}/check-files`). There is no `file()` factory that binds to a **project-level file catalog entry** and triggers when that file changes anywhere in the patient's storage.

**What's needed**: New `file()` factory function + `on_update()` trigger. `FILE_REGISTRY` in the DSL registry. Engine dispatch for file-level events. This is the biggest architectural gap -- requires event propagation from file system or checksum monitoring.

---

---

## 4. RecordFlow DSL: `.do_task(fn)` -- Unified Task Dispatch (NOT EXISTS)

**Target DSL** (`pipeline_flow.py:137`):
```python
record(seg_type).on_finished().do_task(init_master_model)
```

**Current state**: Two separate methods: `.call(func)` (sync/async function in-process) and `.pipeline('name')` (distributed task queue). The target `.do_task(fn)` takes a `@task`-decorated function and dispatches it to the pipeline queue.

**What's needed**: `.do_task(fn)` method on `FlowRecord` that creates a `PipelineAction` from a task-decorated function. Requires integration between RecordFlow actions and the pipeline task registry.

---

---
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

--