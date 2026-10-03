---
paths:
  - "clarinet/services/slicer/context*.py"
  - "clarinet/services/slicer/service.py"
  - "tasks/**/slicer_hydrators.py"
  - "plan/**/slicer_hydrators.py"
---

# Slicer — Context Builder & Hydration Reference

## Context Builder (`context.py`)

`build_slicer_context(record: RecordRead) -> dict[str, Any]` assembles the context dict in layers:

1. **Standard vars** (auto, by DICOM level):
   - `record_id` — always
   - `working_folder` — always
   - `study_uid` — for STUDY and SERIES level
   - `series_uid` — for SERIES level only
2. **File paths from file_registry** (auto): each `FileDefinition.name` -> resolved absolute path via `Files`
3. **`output_file`** (auto): first OUTPUT file from file_registry — convenience alias for scripts
4. **Custom `slicer_script_args`** (template-resolved with all vars above)
5. **Custom `slicer_result_validator_args`** (same)

`build_slicer_context_async(record, session)` wraps the sync function and runs any `slicer_context_hydrators` registered on the record type.

Uses `Files` from `clarinet/files/` (100% sync, no DB / pipeline / broker imports).

### Script variable naming convention

Scripts use **FileDefinition names** as variable names (e.g. `segmentation_single`, `master_model`, `master_projection`).
The generic `output_file` alias points to the first OUTPUT file — useful for scripts shared across record types.

### Helper: `build_template_vars(record)`

Provides the placeholder set for custom Slicer-arg resolution (UX layer
— falls back to raw UIDs when anonymization has not yet propagated):
`patient_id`, `patient_anon_name`, `study_uid`, `study_anon_uid`,
`series_uid`, `series_anon_uid`, `user_id`, `clarinet_storage_path`.

Backend callers that want strict semantics resolve paths through
`Files(record)` directly — it raises `AnonPathError` for
non-anonymized records.

## Context Hydration (`context_hydration.py`)

Decorator-based registry for async context enrichment. Mirrors `clarinet/services/schema_hydration.py`.

### Components

- `SlicerHydrationContext(frozen dataclass)` — holds `StudyRepository` and `RecordRepository`; created via `.from_session(session)`
- `@slicer_context_hydrator("name")` — registers an async function that returns `dict[str, Any]` to merge into context
- `hydrate_slicer_context(context, record, session, names)` — runs named hydrators sequentially, merges results
- `load_custom_slicer_hydrators(folder)` — loads `slicer_hydrators.py` (the `config_context_hydrators_file` default) from the tasks folder at startup as the `clarinet_plan.slicer_hydrators` submodule; raises `ConfigLoadError` on a broken file (loading contract: `.claude/rules/custom-code-loading.md`)

### RecordType field

`RecordType.slicer_context_hydrators: list[str] | None` (JSON column) — list of hydrator names to run.
Set in `RecordDef` config: `slicer_context_hydrators=["patient_first_study"]`.
Names are validated at startup: `reconcile_config` fail-fasts with `ConfigurationError`
on any name missing from the registry (hydrators load before reconcile). Boundary:
config-defined RecordTypes only — types mutated via the API (TOML mode) and orphaned
DB rows are caught only by the runtime ERROR log in `hydrate_slicer_context`.

### Writing a hydrator

```python
from clarinet.services.slicer.context_hydration import (
    SlicerHydrationContext, slicer_context_hydrator,
)

@slicer_context_hydrator("patient_first_study")
async def hydrate_patient_first_study(record, context, ctx):
    studies = await ctx.study_repo.find_by_patient(record.patient_id)
    if not studies:
        return {}
    first = sorted(studies, key=lambda s: s.date or "")[0]
    return {"best_study_uid": first.anon_uid or first.study_uid}
```

## exec scope in `_build_script` (`service.py`)

Slicer reuses a single exec-namespace across all HTTP calls (see the `_current_helper` guard in helper.py). `_build_script` assembles the script as follows:

1. **helper.py** executes at module level — definitions (`SlicerHelper`, `PacsHelper`, `_get_pacs_helper`, ...) live in the module globals; each helper function's `__globals__` points there too.
2. **Context variables** are injected into module globals (`globals()[key] = value` inside `_run()`): this is the only way helper functions that read `globals()` (e.g. `_get_pacs_helper()`) can see them. Injecting into `_ns` would be invisible to helpers — their `__globals__` is fixed to the module namespace.
3. **The user script** executes via `exec(code, _ns)`, where `_ns = dict(globals())` — a flat per-call copy (a single dict serving as both globals and locals):
   - no local-vs-global distinction → patterns like `slicer = SlicerHelper(...)` don't raise `UnboundLocalError`;
   - heavy VTK objects (~1-3 GB per volume) stay in `_ns` and get garbage-collected after the call, instead of accumulating in the reused namespace;
   - the copy is built AFTER injection — the script sees the context variables.
4. **Cleanup in `finally`**: injected keys are removed from module globals after exec (including when the script raises). Without this, one call's context (record UIDs, file_registry paths, PACS parameters) would leak into every subsequent script and into manual Slicer console sessions; the documented `PacsHelper.from_slicer()` fallback remains reachable.

Only `globals()['__execResult']` is exposed outward — the result channel that Slicer reads after the script runs. This key **is removed from the `_ns` copy before exec** (`_ns.pop('__execResult', None)`), but NOT in `finally`: the line that publishes the result writes it into module globals so Slicer can read it *after* the script finishes — cleaning it up in `finally` would erase the channel too early. Without the pop, the next call's `_ns = dict(globals())` would inherit the previous result, and a script that never assigns `__execResult` would return the stale dict instead of `{}` (the contract is `{}` when the script assigns nothing).

Unit tests for this mechanism: `tests/test_slicer_build_script.py` — the generated script runs against a plain dict with no live Slicer (thanks to the `_Dummy` stubs in helper.py). The file deliberately lives at the `tests/` root: `tests/integration/test_slicer_service.py` is gated by `_check_slicer` and is skipped entirely in CI.
