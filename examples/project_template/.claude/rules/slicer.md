---
paths:
  - "plan/hydrators/**"
  - "plan/scripts/**"
  - "plan/validators/**"
---

# Slicer-интеграция: hydrators / scripts / validators

Эти три раздела объединены, потому что они тесно связаны через **inject vars** — переменные, которые фреймворк передаёт в окружение Slicer-скрипта и валидатора. Hydrator вычисляет переменную; скрипт её использует; валидатор тоже её видит.

```
hydrators/  →  context_hydrators.py             # вычисляют inject vars
scripts/    →  *.py (исполняются в 3D Slicer)   # используют inject vars
validators/ →  *_validator.py                   # запускаются после скрипта, видят те же vars
```

---

## Часть A — Hydrators (`plan/hydrators/`)

Async-функции, которые перед запуском Slicer-скрипта обращаются к БД и возвращают словарь переменных, инжектируемых в Slicer. Один файл — `context_hydrators.py`, путь зашит в `settings.toml` (`config_context_hydrators_file`).

### Декоратор и сигнатура

```python
from typing import Any

from clarinet.models.record import RecordRead
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.slicer.context_hydration import (
    SlicerHydrationContext,
    slicer_context_hydrator,
)


@slicer_context_hydrator("best_series_from_first_check")
async def hydrate_best_series_from_first_check(
    record: RecordRead,
    _context: dict[str, Any],
    ctx: SlicerHydrationContext,
) -> dict[str, Any]:
    """Inject best_series_uid from the first-check record."""
    criteria = RecordSearchCriteria(
        record_type_name="first-check",
        study_uid=record.study_uid,
    )
    first_checks = await ctx.record_repo.find_by_criteria(criteria)
    if not first_checks:
        return {}

    best_series = (first_checks[0].data or {}).get("best_series")
    if not best_series:
        return {}

    return {"best_series_uid": best_series}
```

| Параметр | Что внутри |
|---|---|
| `record` | `RecordRead` — запись, для которой собирается контекст |
| `_context` | dict, накопленный предыдущими hydrator-ами + auto-injected переменные. Обычно не нужен (поэтому `_`); используется только в редких сценариях, когда один hydrator зависит от результата другого (например, для `working_folder`) |
| `ctx` | `SlicerHydrationContext` с доступом к репозиториям |

Имя в декораторе (`"best_series_from_first_check"`) — то, что указывается в `RecordDef.slicer_context_hydrators=[...]`. Должно совпадать символ в символ.

### `SlicerHydrationContext`

```python
ctx.study_repo.find_by_patient(patient_id)        # все study пациента
ctx.study_repo.get_with_series(study_uid)         # study + загруженные series
ctx.record_repo.get(record_id)                    # одна запись
ctx.record_repo.find_by_criteria(criteria)        # сложный поиск
```

`RecordSearchCriteria` поддерживает фильтры по `record_type_name`, `patient_id`, `study_uid`, `series_uid`, статусу и т. д. Полный список — `clarinet/repositories/record_repository.py`.

### Возврат

`dict[str, Any]`. Ключи становятся **именами переменных** в Slicer-скрипте и валидаторе. Если данных недостаточно, возвращайте `{}` (не падайте) — фреймворк просто не добавит этих переменных, и скрипт сможет проверить их через `if best_series_uid is not None`.

### Hydrator vs `slicer_script_args`

- **Hydrator** — динамическое значение, требующее запроса в БД (UID лучшей серии, путь к файлу другого пациента).
- **`slicer_script_args`** — статические константы, известные на момент описания `RecordDef` (цвета сегментов, режим редактора, brush size).

---

## Часть B — Slicer-скрипты (`plan/scripts/`)

Bare Python-скрипты, исполняющиеся в окружении 3D Slicer. Каждый файл — один тип задачи.

### Inject vars: что доступно в скрипте

**Авто-инжектируется фреймворком всегда**:

| Переменная | Когда |
|---|---|
| `working_folder` | `str` — абсолютный путь к рабочей папке записи (PATIENT/STUDY/SERIES) |
| `output_file` | `str` — путь к **первому** `FileRef` с `role="output"` из `RecordDef.files` |
| `study_uid` | `str` — DICOM Study UID (только для STUDY/SERIES-уровня) |
| `series_uid` | `str` — DICOM Series UID (только для SERIES-уровня) |
| `pacs_host`, `pacs_port`, `pacs_aet`, `pacs_login`, `pacs_password` | если PACS настроен в settings |

**Hydrator-инжектируемые**: то, что вернули функции из `RecordDef.slicer_context_hydrators`.

**Пользовательские константы**: то, что указано в `RecordDef.slicer_script_args`.

### Обязательный docstring

В начале каждого скрипта — docstring с перечислением context vars. Это **контракт** между скриптом и фреймворком: агенту проще ориентироваться, валидатор получает те же vars, проверка соответствия RecordDef становится явной.

```python
"""Slicer script — lesion segmentation on a single study.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    study_uid: DICOM Study UID (auto, STUDY-level).
    output_file: Path to the first OUTPUT file definition (auto).
    best_series_uid: From hydrator best_series_from_first_check (may be None).
    pacs_*: PACS connection parameters (auto).
"""
```

### `SlicerHelper`

Основной helper для PACS / segmentation / layout / alignment. Полный API + VTK pitfalls — `<clarinet>/clarinet/.claude/rules/slicer-helper-api.md`. Базовый набор:

```python
s = SlicerHelper(working_folder)

# Загрузка из PACS
s.load_study_from_pacs(study_uid)
s.load_series_from_pacs(study_uid, series_uid)

# Сегментация
seg = (
    s.create_segmentation("Segmentation")
    .add_segment("mts", (1.0, 0.0, 0.0))     # red
    .add_segment("benign", (0.0, 1.0, 0.0))  # green
)
seg = s.load_segmentation(output_file, "Segmentation")
s.copy_segments(src_seg, dst_seg, empty=True)
s.sync_segments(src_seg, dst_seg, empty=True)

# UI
s.setup_editor(seg, effect="Paint", brush_size=5.0)
s.set_layout("axial")
s.set_dual_layout(vol_a, vol_b, seg_a=..., seg_b=..., linked=False)
s.annotate("Segment all lesions")
s.add_view_shortcuts()

# Выравнивание
align_tf = s.align_by_center(target, model, moving_segmentation=projection)
s.refine_alignment_by_centroids(projection, master_seg, align_tf)
```

### Идемпотентность

Скрипт может быть открыт повторно (например, врач хочет дописать сегментацию). Поэтому стандартный паттерн — проверка существования output:

```python
import os

if os.path.isfile(output_file):
    seg = s.load_segmentation(output_file, "Segmentation")
else:
    seg = s.create_segmentation("Segmentation").add_segment("mts", (1.0, 0.0, 0.0))
```

### Типичные паттерны

**Single-volume segmentation**: загрузить study (или одну серию), создать/загрузить сегментацию, настроить редактор, запустить.

**Dual-volume comparison** (сравнение текущего исследования с референсным): `set_dual_layout(linked=False)` для независимой навигации в двух вьюпортах. Левый — модель, правый — целевая серия + проекция.

### Линт-комментарии

Так как переменные инжектируются в глобальный namespace, mypy/ruff не знают об их существовании. На каждой строке использования добавляйте:

```python
s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821
```

`name-defined` отключает mypy, `F821` — pyflakes (undefined name).

---

## Часть C — Validators (`plan/validators/`)

Bare Python-скрипты, исполняющиеся в Slicer **после** того, как пользователь нажал "сохранить". Доступны те же globals, что и в скрипте: `slicer`, hydrator vars, `output_file`. Дополнительно — built-in helper `export_segmentation`.

### Базовый паттерн

```python
"""Validator — check segment names and export the Segmentation node."""

node = slicer.util.getNode("Segmentation")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

expected = {"mts", "unclear", "benign"}
current = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    current.add(seg.GetSegment(sid).GetName())

if current != expected:
    raise ValueError(f"Expected segments {expected}, got {current}")

export_segmentation("Segmentation", output_file)  # type: ignore[name-defined]  # noqa: F821
```

Структура:

1. `node = slicer.util.getNode("Name")` — взять MRML-узел.
2. Валидация: имена сегментов, типы, соответствие предыдущему состоянию.
3. `raise ValueError(...)` при проблеме — пользователь увидит ошибку и не сможет финализировать запись.
4. `export_segmentation("Name", output_file)` — записать сегментацию в `.seg.nrrd` через built-in helper.

### Типичные проверки

**Required segment set** — все нужные сегменты присутствуют:
```python
expected = {"mts", "unclear", "benign"}
if current != expected:
    raise ValueError(f"Expected {expected}, got {current}")
```

**Auto-numbering** — для master-моделей с числовыми именами сегментов: дописать недостающие номера в пустые сегменты, проверить уникальность.

**Immutability** — если файл уже существует, проверить, что ни один существующий сегмент не исчез и не переименовался (защита от случайного разрушения исторических данных):
```python
import os, nrrd
if os.path.isfile(output_file):
    _, header = nrrd.read(output_file)
    prev_names = {header[f"Segment{i}_Name"] for i in range(...)}
    missing = prev_names - set(current_names)
    if missing:
        raise ValueError(f"Cannot remove segments: {missing}")
```

### Именование

`{task_name}_validator.py` (например, `segment_validator.py` для скрипта `segment.py`). Связь через `RecordDef(slicer_result_validator="validators/segment_validator.py")`.
