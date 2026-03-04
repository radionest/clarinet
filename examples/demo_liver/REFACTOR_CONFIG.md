# Рефакторинг формата конфигурации: Python вместо TOML

## Текущее состояние

- RecordType конфиги — TOML (один файл на тип) + sidecar JSON Schema
- File registry — TOML (`file_registry.toml`)
- Flow-определения — Python (`pipeline_flow.py`)
- Ссылки между ними — строки (`name = "master_model"`)

## Целевое состояние

Единый стек: **Python** для всей логики и конфигурации, **JSON** только для data_schema (стандарт JSON Schema).

```
tasks/
  files_catalog.py         # File-определения (Python)
  record_types.py          # RecordType-определения (Python)
  pipeline_flow.py         # Flow DSL (Python)
  first_check.schema.json  # JSON Schema (стандарт)
  compare_with_projection.schema.json
  second_review.schema.json
```

## File — каталог файлов проекта

```python
# tasks/files_catalog.py
from clarinet.config import File

master_model = File(
    pattern="master_model.seg.nii",
    multiple=False,
    level="PATIENT",
)

segmentation_single = File(
    pattern="segmentation_single_{user_id}.seg.nrrd",
    multiple=True,
    level="SERIES",
)

master_projection = File(
    pattern="master_projection.seg.nrrd",
    multiple=False,
    level="SERIES",
)

second_review_output = File(
    pattern="second_review_{user_id}.seg.nrrd",
    multiple=True,
    level="SERIES",
)
```

`File` — dataclass с полями: `pattern`, `multiple`, `level` (PATIENT/STUDY/SERIES).
Поле `level` определяет папку хранения и координатные гарантии.

## FileRef — привязка файла к RecordType

```python
@dataclass
class FileRef:
    file: File
    role: Literal["input", "output", "intermediate"]
    required: bool = True
```

Связывает объект File из каталога с ролью в конкретном RecordType.
Ссылка на объект, не на строку — опечатка = ошибка импорта.

## RecordType — определения типов записей

```python
# tasks/record_types.py
from clarinet.config import RecordType, FileRef
from files_catalog import (
    master_model, segmentation_single, segmentation_with_archive,
    master_projection, second_review_output,
)

first_check = RecordType(
    name="first_check",
    description="Initial assessment of every study",
    label="First check",
    level="STUDY",
    min_records=1,
    max_records=1,
    # data_schema загружается автоматически из first_check.schema.json
)

segment_CT_single = RecordType(
    name="segment_CT_single",
    description="CT lesion segmentation — single study",
    label="CT segment (single)",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_CT",
    slicer_script="segment.py",
    files=[
        FileRef(segmentation_single, role="output"),
    ],
)

segment_CT_with_archive = RecordType(
    name="segment_CT_with_archive",
    description="CT lesion segmentation — with archive CT studies",
    label="CT segment (with archive)",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_CT",
    lifecycle_open="add_previous_ct_studies_to_viewer.py",
    slicer_script="segment.py",
    files=[
        FileRef(segmentation_with_archive, role="output"),
    ],
)

segment_MRI_single = RecordType(
    name="segment_MRI_single",
    description="MRI lesion segmentation",
    label="MRI segment",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_MRI",
    slicer_script="segment.py",
    files=[
        FileRef(segmentation_single, role="output"),
    ],
)

create_master_projection = RecordType(
    name="create_master_projection",
    description="Projection of master model onto series coordinate space",
    label="Create projection",
    level="SERIES",
    min_records=1,
    max_records=1,
    role="expert",
    slicer_script="create_projection.py",
    files=[
        FileRef(master_model, role="input"),
        FileRef(master_projection, role="output"),
    ],
)

compare_with_projection = RecordType(
    name="compare_with_projection",
    description="Automatic comparison of segmentation with master model projection",
    label="Compare with projection",
    level="SERIES",
    min_records=1,
    max_records=1,
    role="auto",
    files=[
        FileRef(master_projection, role="input"),
        FileRef(segmentation_single, role="input"),
    ],
)

second_review = RecordType(
    name="second_review",
    description="Doctor classifies lesions missed in initial segmentation",
    label="Second review",
    level="SERIES",
    min_records=1,
    max_records=1,
    slicer_script="second_review.py",
    files=[
        FileRef(master_projection, role="input"),
        FileRef(second_review_output, role="output"),
    ],
)

update_master_model = RecordType(
    name="update_master_model",
    description="Expert adds new ROIs to master model",
    label="Update master model",
    level="PATIENT",
    min_records=1,
    max_records=1,
    role="expert",
    slicer_script="update_master_model.py",
    files=[
        FileRef(master_model, role="output"),
    ],
)
```

## Flow DSL — ссылки на объекты вместо строк

```python
# tasks/pipeline_flow.py
from clarinet.flow import record, study, file, task, Field as F
from files_catalog import master_model
from record_types import (
    first_check, segment_CT_single, segment_CT_with_archive,
    create_master_projection, compare_with_projection,
    update_master_model, second_review,
)

# Можно ссылаться на объект RecordType вместо строки:
record(segment_CT_single).on_finished().do_task(init_master_model)

# Или на строку (обратная совместимость):
record("segment_CT_single").on_finished().do_task(init_master_model)
```

## Преимущества

| Аспект | TOML (было) | Python (стало) |
|---|---|---|
| Автодополнение в IDE | нет | да |
| Проверка типов (mypy) | нет | да |
| Ссылки между файлами и RecordType | строки | объекты |
| Рефакторинг (переименование) | ручной поиск | IDE rename |
| Обнаружение опечаток | runtime | import time / статический анализ |
| Единый парсер | tomllib + json | importlib (уже используется для flow) |
| Редактирование не-программистами | просто | сложнее |

## Что остаётся в JSON

Только `data_schema` — JSON Schema файлы (`*.schema.json`). Причины:
- Стандартный формат, поддерживается инструментами валидации
- Может генерироваться/редактироваться GUI-инструментами
- Используется на frontend для генерации форм (formosh)
- Sidecar-конвенция: `{record_type_name}.schema.json` рядом с определением

## Загрузка при bootstrap

```python
# Псевдокод bootstrap
import importlib.util

# 1. Загрузить files_catalog.py -> получить все File-объекты
# 2. Загрузить record_types.py -> получить все RecordType-объекты
# 3. Для каждого RecordType найти sidecar schema по name
# 4. Резолвить FileRef -> FileDefinition для БД
# 5. Создать/обновить RecordType в БД
```

Механизм загрузки — тот же `importlib.util.spec_from_file_location()`, что уже используется для flow-файлов. Путь к папке tasks берётся из `settings.recordflow_paths`.

## Обратная совместимость

- TOML-конфиги продолжают работать через существующий config_loader
- Python-конфиги — новый путь, приоритетнее TOML при наличии обоих
- Миграция постепенная: проект может использовать TOML, Python или mix
