---
paths:
  - "plan/definitions/**"
---

# Раздел `plan/definitions/`

Единственное место в проекте, где объявляются `FileDef` и `RecordDef`. Один файл — `record_types.py`, путь к нему зашит в `settings.toml` (`config_record_types_file`).

```python
from clarinet.flow import FileDef, FileRef, RecordDef
```

Эти три класса полностью описывают типы файлов и шагов workflow. Вся логика поведения (триггеры, действия, валидация) живёт в других разделах.

## `FileDef` — описание файла

```python
master_model = FileDef(
    pattern="master_model.seg.nrrd",
    level="PATIENT",
    description="Master model — one ROI per lesion with unique number",
)
```

| Поле | Тип | Назначение |
|---|---|---|
| `pattern` | `str` | Имя файла или шаблон с плейсхолдерами |
| `level` | `"PATIENT"` / `"STUDY"` / `"SERIES"` | Уровень DICOM-иерархии, в working folder которого лежит файл |
| `multiple` | `bool` | `True` — glob-коллекция, `False` — один файл |
| `description` | `str` | Документация для агента и UI |
| `name` | `str` | Автогенерируется из имени переменной (`master_model`); можно задать явно |

### Плейсхолдеры в `pattern`

| Плейсхолдер | Значение |
|---|---|
| `{patient_id}`, `{study_uid}`, `{series_uid}` | Идентификаторы из DICOM-иерархии (анонимизированные) |
| `{user_id}` | ID пользователя, создавшего запись (для файлов «по врачу») |
| `{origin_type}` | `record.record_type_name` — позволяет именовать файлы по типу записи-источника |
| `{data.FIELD}` | Поле из `record.data` |

Подробности резолвинга паттернов — в `<clarinet>/clarinet/.claude/rules/file-registry.md`.

### Семантика `level`

- **PATIENT** — файл лежит в `<storage>/<patient_id>/`. Доступен из любых записей пациента (мастер-модели, общие референсы).
- **STUDY** — файл в `<storage>/<patient_id>/<study_uid>/`. Используется для study-level артефактов (сегментации одного study).
- **SERIES** — файл в `<storage>/<patient_id>/<study_uid>/<series_uid>/`. Используется для series-level артефактов (NIfTI-volumes, проекции).

Уровень файла должен быть **не глубже** уровня записи, которая на него ссылается. PATIENT-файл доступен всем; SERIES-файл — только SERIES-записям.

## `FileRef` — привязка файла к RecordDef

```python
FileRef(segmentation, "output")     # Позиционно
FileRef(segmentation, role="input") # Именованно
```

| Поле | Тип | Назначение |
|---|---|---|
| `file` | `FileDef` | Ссылка на FileDef-объект, объявленный выше в этом же файле |
| `role` | `"input"` / `"output"` / `"intermediate"` | Семантика: входной (требуется до выполнения) / выходной (создаётся) / промежуточный |
| `required` | `bool` (default `True`) | Должен ли файл существовать к моменту финализации записи |

`output_file` в Slicer-скрипте — это путь к **первому** `FileRef` с `role="output"` из списка `RecordDef.files`.

## `RecordDef` — описание типа записи

```python
segment_ct = RecordDef(
    name="segment-ct-single",
    description="CT lesion segmentation — single study only",
    label="CT segment (single)",
    level="STUDY",
    role="doctor_CT",
    min_records=2,
    max_records=4,
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    data_schema="schemas/segment-ct.schema.json",  # необязательно
)
```

### Обязательные поля

| Поле | Описание |
|---|---|
| `name` | kebab-case, 5-30 символов. Идентификатор в DSL и URL. |
| `level` | `"PATIENT"` / `"STUDY"` / `"SERIES"` |

### Опциональные поля

| Поле | Описание |
|---|---|
| `description` | Подробное описание для агента и UI |
| `label` | Короткое название для UI |
| `role` (alias `role_name`) | Кто исполняет: `"doctor"`, `"auto"`, `"expert"`, или кастомная роль из `extra_roles` |
| `min_records`, `max_records` | Сколько записей этого типа должно/может существовать на свой parent |
| `files` | `list[FileRef(...)]` — связь с `FileDef` |
| `data_schema` | Путь `"schemas/X.schema.json"` или inline `dict`. Путь — относительно `config_tasks_path` (то есть `plan/`) |
| `slicer_script` | Путь к Slicer-скрипту: `"scripts/segment.py"` |
| `slicer_result_validator` | Путь к валидатору: `"validators/segment_validator.py"` |
| `slicer_context_hydrators` | `list[str]` — имена hydrator-функций, инжектирующих переменные в Slicer |
| `slicer_script_args` | `dict[str, Any]` — статические константы, доступные в Slicer-скрипте |

## Связи между разделами

`RecordDef` ссылается на файлы из других разделов проекта по конвенции:

| Поле | Куда ссылается |
|---|---|
| `slicer_script="scripts/X.py"` | Файл в `plan/scripts/` |
| `slicer_result_validator="validators/X.py"` | Файл в `plan/validators/` |
| `slicer_context_hydrators=["name"]` | Декоратор `@slicer_context_hydrator("name")` в `plan/hydrators/context_hydrators.py` |
| `data_schema="schemas/X.schema.json"` | Файл в `plan/schemas/` |
| `files=[FileRef(file_def, ...)]` | `FileDef`, объявленный выше в `record_types.py` |
| `role="custom_role"` | Должна быть в `extra_roles` в `settings.toml` |

Все пути относительны `config_tasks_path` (`plan/`), а не текущего файла `record_types.py`.

## Типичные ошибки

- **Кастомная роль не в `settings.toml`**. `RecordDef(role="surgeon")` без `extra_roles = [..., "surgeon"]` упадёт при загрузке конфига.
- **Путь к схеме относительно definitions/**. `data_schema="../schemas/X.schema.json"` неверно — путь от `plan/`, не от `plan/definitions/`. Правильно: `"schemas/X.schema.json"`.
- **Slicer-поля без файлов**. Указали `slicer_script="scripts/foo.py"`, но файла нет — конфиг загрузится, но запуск задачи сломается.
- **`level` файла глубже уровня записи**. SERIES-файл нельзя указать в input для STUDY-записи (она не знает, какую серию читать).
- **Имя hydrator-а не совпадает с декоратором**. В `RecordDef` указано `slicer_context_hydrators=["best_series"]`, но в коде — `@slicer_context_hydrator("best_series_from_first_check")`. Имена должны быть строго одинаковыми.

## Полный аннотированный пример

```python
from clarinet.flow import FileDef, FileRef, RecordDef

# --- File definitions ---

segmentation = FileDef(
    pattern="segmentation_{user_id}.seg.nrrd",  # имя зависит от пользователя
    level="STUDY",                                # лежит в study-папке
    description="Doctor lesion segmentation",
)

# --- Record types ---

first_check = RecordDef(
    name="first-check",                            # kebab-case
    description="Initial study assessment",
    label="First check",
    level="STUDY",
    role="doctor",                                 # стандартная роль
    min_records=2,                                 # каждое исследование смотрят 2 врача
    max_records=2,
    data_schema="schemas/first-check.schema.json", # относительно plan/
)

segment_ct = RecordDef(
    name="segment-ct-single",
    label="CT segment",
    level="STUDY",
    role="doctor_CT",                              # должна быть в extra_roles
    min_records=2,
    max_records=4,
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],       # output_file = путь к этому файлу
)
```
