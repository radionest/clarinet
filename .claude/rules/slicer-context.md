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

## exec scope в `_build_script` (`service.py`)

Slicer переиспользует один exec-namespace для всех HTTP-вызовов (см. guard `_current_helper` в helper.py). `_build_script` собирает скрипт так:

1. **helper.py** исполняется на module-level — определения (`SlicerHelper`, `PacsHelper`, `_get_pacs_helper`, ...) живут в module globals; `__globals__` каждой helper-функции указывает туда же.
2. **Context-переменные** инъецируются в module globals (`globals()[key] = value` внутри `_run()`): только так их видят helper-функции, читающие `globals()` (например `_get_pacs_helper()`). Инъекция в `_ns` для хелперов невидима — их `__globals__` фиксирован на module namespace.
3. **Пользовательский скрипт** исполняется через `exec(code, _ns)`, где `_ns = dict(globals())` — плоская per-call копия (один dict = и globals, и locals):
   - нет local-vs-global различия → паттерны `slicer = SlicerHelper(...)` не дают `UnboundLocalError`;
   - тяжёлые VTK-объекты (~1-3 GB на том) остаются в `_ns` и собираются GC после вызова, не накапливаясь в переиспользуемом namespace;
   - копия строится ПОСЛЕ инъекции — скрипт видит context-переменные.
4. **Cleanup в `finally`**: инъецированные ключи удаляются из module globals после exec (в т.ч. при исключении в скрипте). Без этого context одного вызова (UID'ы записи, пути file_registry, PACS-параметры) утекал бы во все последующие скрипты и в ручные сессии консоли Слайсера; документированный fallback `PacsHelper.from_slicer()` остаётся достижимым.

Наружу пробрасывается только `globals()['__execResult']` — канал результата, который читает Slicer после выполнения скрипта. Этот ключ **удаляется из копии `_ns` перед exec** (`_ns.pop('__execResult', None)`), но НЕ в `finally`: пробрасывающая строка пишет результат в module globals, чтобы Slicer прочитал его *после* завершения скрипта — чистка в `finally` стёрла бы канал раньше. Без pop следующий вызов через `_ns = dict(globals())` унаследовал бы прошлый результат, и скрипт без присваивания `__execResult` вернул бы устаревший dict вместо `{}` (контракт «`{}`, если скрипт ничего не присвоил»).

Юнит-тесты механики: `tests/test_slicer_build_script.py` — generated script исполняется в обычном dict без живого Слайсера (благодаря `_Dummy`-стабам helper.py). Файл намеренно в корне `tests/`: модуль `tests/integration/test_slicer_service.py` гейтится `_check_slicer` и в CI скипается целиком.
