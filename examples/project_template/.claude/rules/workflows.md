---
paths:
  - "plan/workflows/**"
---

# Раздел `plan/workflows/`

Содержит файлы вида `*_flow.py` (по умолчанию `pipeline_flow.py`), путь зашит в `settings.toml` (`recordflow_paths`). Каждый файл — две вещи в одном:

1. **Pipeline-таски** — функции с декоратором `@pipeline_task`, выполняющиеся в воркерах TaskIQ.
2. **RecordFlow DSL** — декларативные правила, связывающие триггеры событий с действиями (создание записи, запуск таски, инвалидация).

## Структура файла `pipeline_flow.py`

Канонический порядок:

```python
from __future__ import annotations
# 1. Imports
from clarinet.services.pipeline import (
    PipelineMessage, SyncTaskContext, TaskContext, pipeline_task,
)
from clarinet.services.recordflow import Field, file, record, study
from clarinet.utils.logger import logger
from record_types import segmentation, master_model  # из ../definitions/

F = Field()

# 2. Pipeline task functions (все @pipeline_task вверху)
@pipeline_task()
def my_task(...): ...

# 3. Async callback functions для .call(...)
async def my_callback(record, context, client): ...

# 4. Flow declarations (по одной в скобках)
(record("foo").on_finished().do_task(my_task))
(record("bar").on_finished().call(my_callback))
```

`record_types.py` импортируется напрямую — `plan/` добавляется в `sys.path` фреймворком при загрузке конфига.

---

## Часть A — Pipeline-таски (`@pipeline_task`)

### Когда pipeline task, а когда RecordFlow action

- **Pipeline task** — долгая или тяжёлая работа, которую нужно изолировать в воркер: загрузка из PACS, конвертация DICOM → NIfTI, обработка изображений (skimage, SimpleITK), GPU-инференс, вызовы внешних API.
- **RecordFlow action** (`create_record`, `update_record`, `invalidate_records`) — быстрая декларативная связка, выполняющаяся синхронно при срабатывании триггера.

Если шаг занимает <50ms и не делает I/O — это action. Если читает файлы, дёргает БД, считает что-то на массивах — это task.

### Декоратор

```python
@pipeline_task(queue="clarinet.dicom", auto_submit=False)
async def my_task(msg: PipelineMessage, ctx: TaskContext) -> None: ...
```

| Параметр | Назначение |
|---|---|
| `queue` | Очередь TaskIQ. По умолчанию `"default"`. Встроенные: `"clarinet.dicom"` (DICOM-задачи). Можно завести свою через `pipeline_default_timeout` etc. в settings. |
| `auto_submit` | Если `True` и таска возвращает `dict`, фреймворк автоматически вызовет `submit_record_data(msg.record_id, result)`. Удобно для коротких чистых функций. |

### Async vs sync

- **Async** — для I/O, HTTP, БД, всех вызовов `ctx.client.*` и `ctx.records.*`. Получает `TaskContext`.
- **Sync** — для CPU-bound работы (skimage, SimpleITK, vtk, blocking-библиотек). Получает `SyncTaskContext`. Фреймворк автоматически детектит sync-функцию и запускает её в треде, чтобы не блокировать event loop.

```python
@pipeline_task()
async def fetch_dicom(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Async — IO/HTTP/БД."""
    ...

@pipeline_task()
def process_volume(msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    """Sync — numpy/skimage/etc."""
    ...
```

### `PipelineMessage`

```python
class PipelineMessage:
    patient_id: str | None
    study_uid: str | None
    series_uid: str | None
    record_id: int | None
    pipeline_id: str | None  # имя pipeline (для многошаговых)
    step_index: int | None
    payload: dict[str, Any]  # любые kwargs из .do_task(task, foo=bar)
```

Какие поля заполнены — зависит от триггера в DSL: `record("X").on_finished().do_task(my_task)` передаст `record_id` + поля DICOM-иерархии этой записи.

### Контексты задачи

`TaskContext` (async) и `SyncTaskContext` (sync) дают доступ к:

#### `ctx.files` — `FileResolver`

```python
ctx.files.resolve(file_def) -> Path        # путь к файлу (создаёт parent dirs)
ctx.files.exists(file_def) -> bool         # существует ли файл
```

Принимает `FileDef`-объект (импортированный из `record_types.py`) или строку с именем.

#### `ctx.records` — `RecordQuery`

```python
await ctx.records.find(
    "first-check",
    patient_id=msg.patient_id,
    study_uid=msg.study_uid,
)  # -> list[RecordRead]
```

Sync-вариант — без `await`.

#### `ctx.client` — `ClarinetClient`

HTTP-клиент к собственному API проекта. Основные методы (полный список — в `clarinet/client.py`):

```python
await ctx.client.get_record(record_id)
await ctx.client.find_records(record_type_name="segment-ct-single", **filters)
await ctx.client.create_record(RecordCreate(...))
await ctx.client.submit_record_data(record_id, data, status="finished")
await ctx.client.update_record(record_id, **updates)
await ctx.client.invalidate_record(
    record_id, mode="hard", source_record_id=..., reason="..."
)
await ctx.client.get_study(study_uid)
await ctx.client.anonymize_patient(patient_id)
```

Sync-аналог — `SyncPipelineClient` через `ctx.client` в sync-задачах.

### Идемпотентность — обязательный контракт

Каждая таска должна быть **идемпотентной**: повторный вызов с тем же сообщением не должен ничего ломать. Стандартный паттерн:

```python
@pipeline_task()
def init_master_model(_msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    if ctx.files.exists(master_model):
        return  # уже сделано — выходим
    ...
    save_seg_nrrd(...)
```

Причины:
- Воркер может ретраить таску при сбое (`pipeline_retry_count`, `pipeline_retry_delay`).
- Cascade-инвалидация может пересоздать запись и снова поставить таску в очередь.
- Ручной перезапуск pipeline для отладки.

### Логирование

```python
from clarinet.utils.logger import logger
logger.info(f"Processing record {msg.record_id}")
logger.error(f"Failed to read {seg_path}: {exc}")
```

Только f-strings, никогда `print()`, никогда `import loguru`.

### Встроенные таски

- `convert_series_to_nifti` — конвертация DICOM-серии в NIfTI через C-GET. Очередь `clarinet.dicom`. Идемпотентна (проверяет `volume.nii.gz`).
- `_convert_series_impl(msg, ctx)` — внутренняя функция для прямого использования внутри пользовательских задач (если нужно сразу в одной таске и подгрузить NIfTI, и сделать что-то ещё).

Имя пользовательской таски не должно совпадать со встроенной — иначе `register_task()` поднимет `PipelineConfigError`.

### Минимальный пример

```python
@pipeline_task()
def init_master_model(_msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    """Создание мастер-модели по первой завершённой сегментации."""
    if ctx.files.exists(master_model):
        return

    seg_path = ctx.files.resolve(segmentation)
    if not seg_path.is_file():
        raise FileNotFoundError(f"Segmentation file not found: {seg_path}")

    master_path = ctx.files.resolve(master_model)
    Path(master_path).parent.mkdir(parents=True, exist_ok=True)
    # ... numpy/skimage processing ...
    logger.info(f"Created master model at {master_path}")
```

---

## Часть B — RecordFlow DSL

> Полная справка по DSL — `<clarinet>/clarinet/.claude/rules/recordflow-dsl.md`. Здесь — компактный обзор для повседневной работы.

### Триггеры

```python
study().on_creation()        # пришло новое исследование
series().on_creation()       # появилась новая серия
patient().on_creation()      # новый пациент

record("type-name").on_status("pending")    # запись перешла в данный статус
record("type-name").on_finished()            # alias для on_status("finished")
record("type-name").on_data_update()         # PATCH данных уже finished записи

file(file_def).on_update()    # файл изменился (для cascade-инвалидации)
```

`file(...)` принимает `FileDef`-объект или строку с `name`. Источник file-событий — `@pipeline_task` через middleware checksum-сравнения.

### Условия

```python
F = Field()

# Сравнение полей record.data
.if_record(F.is_good == True)
.if_record(F.confidence < 0.7, F.modality == "CT")  # AND-семантика
.if_record(F.x == y, on_missing="raise")             # default "skip" → False

# Pattern matching по полю
.match(F.study_type)
    .case("CT").create_record("segment-ct")
    .case("MRI").create_record("segment-mri")
    .default().create_record("segment-unknown")
```

`.match()` поглощает предыдущий `.if_record()` как guard. Stop-on-first-match. `.default()` срабатывает, только если ни один `case` не подошёл и guard истинен.

### Действия

```python
.create_record("type1", "type2", inherit_user=False)   # одну или несколько
.do_task(my_task, extra_payload_key="value")           # запустить @pipeline_task
.pipeline("named_pipeline", **payload)                 # запустить named pipeline
.call(async_callback)                                  # вызвать async-функцию
.invalidate_records("type1", "type2", mode="hard")     # каскадная инвалидация
.invalidate_all_records("type")                        # alias для одного типа
```

#### `.do_task` vs `.pipeline`

- `.do_task(func)` — для однотшаговой задачи. Фреймворк автоматически создаёт одношаговый pipeline `_task:func_name` и дедуплицирует.
- `.pipeline("name")` — для именованного многошагового pipeline (если вы построили `Pipeline("name").step(...).step(...)` в коде).

#### `.call(callback)` — когда DSL не хватает

Используется, когда нужен `parent_record_id` или сложная логика, не выражаемая в DSL:

```python
async def create_comparison_record(
    record: RecordRead,
    context: dict[str, Any],
    client: ClarinetClient,
) -> None:
    await client.create_record(
        RecordCreate(
            record_type_name="compare-with-projection",
            parent_record_id=record.id,  # привязка к триггеру как parent
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=record.series_uid,
        )
    )

(record("segment-ct-single").on_finished().call(create_comparison_record))
```

### Cross-record references

Сравнение полей **разных** записей (не self-referential):

```python
record("ai-analysis").on_finished().if_(
    record("ai-analysis").data.diagnosis != record("doctor-review").data.diagnosis
).create_record("expert-check")
```

`record("type").data.X` создаёт side-effect FlowRecord, который движок резолвит при оценке условия.

### Типичные паттерны

```python
# 1. На поступление исследования — создать первичный осмотр
(study().on_creation().create_record("first-check"))

# 2. После first-check — ветвление по типу исследования
(
    record("first-check").on_finished()
    .if_record(F.is_good == True)
    .match(F.study_type)
    .case("CT").create_record("segment-ct-single")
    .case("MRI").create_record("segment-mri-single")
)

# 3. Сегментация → автоматическое сравнение
(record("segment-ct-single").on_finished().call(create_comparison_record))
(record("compare-with-projection").on_status("pending").do_task(compare_w_projection))

# 4. Cascade-инвалидация при изменении мастер-модели
(file(master_model).on_update().invalidate_all_records("create-master-projection"))
```
