---
paths:
  - "clarinet/services/slicer/context*.py"
  - "tasks/**/context_hydrators.py"
---

# Slicer — Context Builder & Hydration Reference

## Context Builder (`context.py`)

`build_slicer_context(record: RecordRead) -> dict[str, Any]` assembles the context dict in layers:

1. **Standard vars** (auto, by DICOM level):
   - `working_folder` — always
   - `study_uid` — for STUDY and SERIES level
   - `series_uid` — for SERIES level only
2. **File paths from file_registry** (auto): each `FileDefinition.name` -> resolved absolute path via `FileResolver`
3. **`output_file`** (auto): first OUTPUT file from file_registry — convenience alias for scripts
4. **Custom `slicer_script_args`** (template-resolved with all vars above)
5. **Custom `slicer_result_validator_args`** (same)

`build_slicer_context_async(record, session)` wraps the sync function and runs any `slicer_context_hydrators` registered on the record type.

Uses `FileResolver` from `clarinet/services/pipeline/context.py` (100% sync, no DB dependencies).

### Script variable naming convention

Scripts use **FileDefinition names** as variable names (e.g. `segmentation_single`, `master_model`, `master_projection`).
The generic `output_file` alias points to the first OUTPUT file — useful for scripts shared across record types.

### Helper: `build_template_vars(record)`

Provides the same set of placeholders as `RecordRead._format_path_strict()`:
`patient_id`, `patient_anon_name`, `study_uid`, `study_anon_uid`, `series_uid`, `series_anon_uid`, `user_id`, `clarinet_storage_path`.

## Context Hydration (`context_hydration.py`)

Decorator-based registry for async context enrichment. Mirrors `clarinet/services/schema_hydration.py`.

### Components

- `SlicerHydrationContext(frozen dataclass)` — holds `StudyRepository` and `RecordRepository`; created via `.from_session(session)`
- `@slicer_context_hydrator("name")` — registers an async function that returns `dict[str, Any]` to merge into context
- `hydrate_slicer_context(context, record, session, names)` — runs named hydrators sequentially, merges results
- `load_custom_slicer_hydrators(folder)` — loads `context_hydrators.py` from tasks folder at startup

### RecordType field

`RecordType.slicer_context_hydrators: list[str] | None` (JSON column) — list of hydrator names to run.
Set in `RecordDef` config: `slicer_context_hydrators=["patient_first_study"]`.

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
