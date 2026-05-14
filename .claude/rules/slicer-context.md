---
paths:
  - "clarinet/services/slicer/context*.py"
  - "clarinet/services/slicer/service.py"
  - "tasks/**/context_hydrators.py"
---

# Slicer — Context Builder & Hydration Reference

## Context Builder (`context.py`)

`build_slicer_context(record: RecordRead) -> dict[str, Any]` assembles the context dict in layers:

1. **Standard vars** (auto, by DICOM level):
   - `record_id` — always
   - `working_folder` — always
   - `study_uid` — for STUDY and SERIES level
   - `series_uid` — for SERIES level only
2. **File paths from file_registry** (auto): each `FileDefinition.name` -> resolved absolute path via `FileResolver`
3. **`output_file`** (auto): first OUTPUT file from file_registry — convenience alias for scripts
4. **Custom `slicer_script_args`** (template-resolved with all vars above)
5. **Custom `slicer_result_validator_args`** (same)

`build_slicer_context_async(record, session)` wraps the sync function and runs any `slicer_context_hydrators` registered on the record type.

Uses `FileResolver` from `clarinet/services/common/file_resolver.py` (100% sync, no DB / pipeline / broker imports).

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

## exec scope в `_build_script` (`service.py`)

`_build_script` оборачивает context + пользовательский скрипт в `def _run()`, чтобы все переменные были function-local и GC'd после return (VTK-объекты ~1-3 GB на том).

### Проблема: `exec(code, globals, locals)` с раздельными dict

Slicer's web handler может вызвать `exec(code, g, l)` с раздельными globals/locals. В этом случае:
- Helper-определения (классы, функции) попадают в `l` (locals)
- `_run().__globals__` указывает на `g` (globals) — `_run()` не видит имена из `l`
- Паттерн `SlicerHelper = SlicerHelper(...)` в скрипте делает `SlicerHelper` локальной переменной `_run()`, вызывая `UnboundLocalError`
- Дефолтные значения в определениях классов (напр. `overwrite_mode: OverwriteMode = OverwriteMode.OVERWRITE_ALL`) вычисляются при определении — если `OverwriteMode` в `l`, а не в `g`, определение класса падает

### Почему `globals().update(locals())` не работает

Внутри `_run()` вызов `globals().update(locals())` копирует текущие locals в globals. Но определения классов из helper.py (напр. `SlicerHelper`) содержат дефолтные значения, которые вычисляются **при определении класса** — до того, как `globals().update(locals())` успеет скопировать зависимые имена.

### Текущий подход: `global` declarations

`_extract_top_level_names(source)` парсит helper.py через `ast` при инициализации `SlicerService.__init__` и кеширует список top-level имён в `self._helper_globals`.

`_build_script` генерирует `global SlicerHelper, PacsHelper, OverwriteMode, ...` в начале `_run()`. Это говорит Python, что эти имена — глобальные, и `_run()` читает/пишет их из `g` напрямую, обходя проблему раздельных dict'ов.
