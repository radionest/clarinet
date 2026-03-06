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