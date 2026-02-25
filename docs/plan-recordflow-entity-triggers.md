# RecordFlow: Entity Creation Triggers

Расширение RecordFlow триггерами на создание сущностей (Series, Study, Patient) для автоматического создания записей.

## Мотивация

- При создании серий или study — автоматически создавать записи
- При создании study — автоматически создавать `quality_assessment` записи
- Универсальный механизм, не привязанный к конкретному эндпоинту

## DSL

Новые factory-функции `series()`, `study()`, `patient()` по аналогии с `record()`:

```python
from src.services.recordflow import record, series, study, patient

# Когда серия добавляется в БД — создать series_markup
series().on_created().add_record("series_markup")

# Когда исследование создаётся — создать quality_assessment
study().on_created().add_record("quality_assessment")

# С кастомной функцией
series().on_created().call(my_custom_function)
```

Сравнение с существующим DSL:

```python
# Существующий (триггер на статус записи)
record("doctor_review").on_status("finished").add_record("ai_analysis")

# Новый (триггер на создание сущности)
series().on_created().add_record("series_markup")
```

## Архитектура

```
DSL (record_flow.py)              Engine                     Routers
────────────────────              ──────                     ───────
series().on_created()  ────────►  entity_flows["series"]
  .add_record("series_markup")    handle_entity_created() ◄── POST /series
                                    → create_record()      ◄── POST /dicom/import-study
```

## Детали реализации

### 1. `src/services/recordflow/flow_record.py`

Добавить `entity_trigger` поле в `FlowRecord`:

```python
class FlowRecord:
    def __init__(self, record_name: str):
        self.record_name = record_name
        self.status_trigger: str | None = None
        self.entity_trigger: str | None = None  # NEW: "series", "study", "patient"
        self.conditions: list[FlowCondition] = []
        self.actions: list[dict] = []
        self._current_condition: FlowCondition | None = None

    def on_created(self) -> FlowRecord:
        """Trigger when the entity is created.

        Used with series()/study()/patient() factory functions.
        Returns self for method chaining.
        """
        return self
```

Отдельный реестр и factory-функции:

```python
# Отдельный реестр для entity flows (record() переиспользует FlowRecord по имени,
# а series() всегда создаёт новый)
ENTITY_REGISTRY: list[FlowRecord] = []


def series() -> FlowRecord:
    """Create entity flow triggered on series creation."""
    flow = FlowRecord("__series__")
    flow.entity_trigger = "series"
    ENTITY_REGISTRY.append(flow)
    return flow


def study() -> FlowRecord:
    """Create entity flow triggered on study creation."""
    flow = FlowRecord("__study__")
    flow.entity_trigger = "study"
    ENTITY_REGISTRY.append(flow)
    return flow


def patient() -> FlowRecord:
    """Create entity flow triggered on patient creation."""
    flow = FlowRecord("__patient__")
    flow.entity_trigger = "patient"
    ENTITY_REGISTRY.append(flow)
    return flow
```

Обновить `__repr__()` и `validate()` для entity flows.

### 2. `src/services/recordflow/engine.py`

Добавить хранилище, регистрацию и обработчик entity flows:

```python
class RecordFlowEngine:
    def __init__(self, clarinet_client: ClarinetClient):
        self.clarinet_client = clarinet_client
        self.flows: dict[str, list[FlowRecord]] = {}
        self.entity_flows: dict[str, list[FlowRecord]] = {}  # NEW

    def register_flow(self, flow: FlowRecord) -> None:
        """Register a flow. Routes entity flows to entity_flows dict."""
        if flow.entity_trigger:
            entity_type = flow.entity_trigger
            if entity_type not in self.entity_flows:
                self.entity_flows[entity_type] = []
            self.entity_flows[entity_type].append(flow)
            logger.info(f"Registered entity flow: on {entity_type} created")
            return
        # ... existing record flow registration unchanged ...

    async def handle_entity_created(
        self,
        entity_type: str,        # "series", "study", "patient"
        patient_id: str,
        study_uid: str | None = None,
        series_uid: str | None = None,
    ) -> None:
        """Handle entity creation and execute matching flows.

        Called by routers after creating Series, Study, or Patient.
        Executes all registered entity flows for the given entity type.

        Args:
            entity_type: Type of entity created.
            patient_id: Patient ID.
            study_uid: Study UID (for study/series events).
            series_uid: Series UID (for series events).
        """
        if entity_type not in self.entity_flows:
            return

        logger.debug(f"Processing entity flows for {entity_type} creation")

        for flow in self.entity_flows[entity_type]:
            # Execute unconditional actions
            for action in flow.actions:
                await self._execute_entity_action(
                    action, patient_id, study_uid, series_uid
                )
            # Execute conditional actions (conditions without record context)
            for condition in flow.conditions:
                if not condition.condition or condition.is_else:
                    for action in condition.actions:
                        await self._execute_entity_action(
                            action, patient_id, study_uid, series_uid
                        )

    async def _execute_entity_action(
        self,
        action: dict[str, Any],
        patient_id: str,
        study_uid: str | None,
        series_uid: str | None,
    ) -> None:
        """Execute a single action triggered by entity creation.

        Args:
            action: Action dict (type, record_type_name/function, params).
            patient_id: Patient ID from the entity.
            study_uid: Study UID from the entity.
            series_uid: Series UID from the entity.
        """
        action_type = action.get("type")

        try:
            if action_type == "create_record":
                from src.models import RecordCreate

                record_type_name = action["record_type_name"]
                params = action.get("params", {})

                create_params: dict[str, Any] = {
                    "record_type_name": record_type_name,
                    "patient_id": patient_id,
                }
                if study_uid:
                    create_params["study_uid"] = study_uid
                if series_uid:
                    create_params["series_uid"] = series_uid
                if "context_info" in params:
                    create_params["context_info"] = params["context_info"]

                record_create = RecordCreate(**create_params)
                result = await self.clarinet_client.create_record(record_create)
                logger.info(
                    f"Entity flow: created '{record_type_name}' (id={result.id})"
                )

            elif action_type == "call_function":
                import asyncio

                func: Callable = action["function"]
                args: tuple = action.get("args", ())
                kwargs: dict = action.get("kwargs", {}).copy()

                kwargs.setdefault("patient_id", patient_id)
                kwargs.setdefault("study_uid", study_uid)
                kwargs.setdefault("series_uid", series_uid)
                kwargs.setdefault("client", self.clarinet_client)

                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result

        except Exception as e:
            logger.error(f"Error executing entity action {action_type}: {e}")
```

### 3. `src/services/recordflow/flow_loader.py`

Обновить `load_flows_from_file()` — очищать `ENTITY_REGISTRY`, добавить factory-функции в namespace:

```python
from .flow_record import (
    ENTITY_REGISTRY,
    RECORD_REGISTRY,
    FlowRecord,
    patient,
    record,
    series,
    study,
)


def load_flows_from_file(file_path: Path) -> list[FlowRecord]:
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()  # NEW

    namespace = {
        "record": record,
        "series": series,     # NEW
        "study": study,       # NEW
        "patient": patient,   # NEW
        "FlowRecord": FlowRecord,
        "__file__": str(file_path),
        "__name__": "__main__",
    }

    exec(compiled, namespace)

    flows = list(RECORD_REGISTRY) + list(ENTITY_REGISTRY)  # NEW: include entity flows
    for flow in flows:
        logger.info(f"Loaded flow: {flow}")
    return flows
```

### 4. `src/services/recordflow/flow_builder.py`

Re-export новых factory-функций:

```python
from .flow_record import FlowRecord, patient, record, series, study

__all__ = ["FlowRecord", "flow", "patient", "record", "series", "study"]

flow = record
```

### 5. `src/services/recordflow/__init__.py`

Добавить в экспорт:

```python
from .flow_record import ENTITY_REGISTRY, series, study, patient

__all__ = [
    ...
    "ENTITY_REGISTRY",
    "series",
    "study",
    "patient",
]
```

### 6. `src/api/routers/study.py` — Хуки в эндпоинты

Добавить `Request` и `BackgroundTasks` в эндпоинты создания сущностей:

**add_series** (POST /series):
```python
from fastapi import BackgroundTasks, Request

@router.post("/series", response_model=Series, status_code=status.HTTP_201_CREATED)
async def add_series(
    series: SeriesCreate,
    service: StudyServiceDep,
    request: Request,                    # NEW
    background_tasks: BackgroundTasks,   # NEW
) -> Series:
    series_data = series.model_dump()
    result = await service.create_series(series_data)

    # Notify RecordFlow engine
    engine = getattr(request.app.state, "recordflow_engine", None)
    if engine:
        study = await service.get_study(result.study_uid)
        background_tasks.add_task(
            engine.handle_entity_created,
            "series", study.patient.id, result.study_uid, result.series_uid,
        )
    return result
```

**add_study** (POST /studies):
```python
@router.post("/studies", response_model=Study, status_code=status.HTTP_201_CREATED)
async def add_study(
    study: StudyCreate,
    service: StudyServiceDep,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Study:
    study_data = study.model_dump()
    result = await service.create_study(study_data)

    engine = getattr(request.app.state, "recordflow_engine", None)
    if engine:
        background_tasks.add_task(
            engine.handle_entity_created,
            "study", study_data["patient_id"], result.study_uid,
        )
    return result
```

**add_patient** (POST /patients):
```python
@router.post("/patients", response_model=Patient, status_code=status.HTTP_201_CREATED)
async def add_patient(
    patient: PatientSave,
    service: StudyServiceDep,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Patient:
    patient_data = patient.model_dump()
    result = await service.create_patient(patient_data)

    engine = getattr(request.app.state, "recordflow_engine", None)
    if engine:
        background_tasks.add_task(
            engine.handle_entity_created, "patient", result.id,
        )
    return result
```

### 7. `src/api/routers/dicom.py` — Хук в импорт

В `import_study_from_pacs` после цикла создания серий:

```python
@router.post("/import-study", response_model=StudyRead)
async def import_study_from_pacs(
    request: PacsImportRequest,
    _user: SuperUserDep,
    client: DicomClientDep,
    pacs: PacsNodeDep,
    service: StudyServiceDep,
    http_request: Request,               # NEW
    background_tasks: BackgroundTasks,    # NEW
) -> object:
    # ... existing study + series creation ...

    # Notify RecordFlow for each created series
    engine = getattr(http_request.app.state, "recordflow_engine", None)
    if engine:
        for s in pacs_series:
            background_tasks.add_task(
                engine.handle_entity_created,
                "series",
                request.patient_id,
                request.study_instance_uid,
                s.series_instance_uid,
            )

    return await service.get_study(request.study_instance_uid)
```

### 8. `examples/demo/record_flow.py` — Flow-скрипт

```python
from src.services.recordflow import record, series

# ... existing flows ...

# Flow 4: Auto-create series_markup for every new series
series().on_created().add_record("series_markup")
```

## Контекст вызова `.call()` для entity flows

Функция вызванная через `.call()` получает kwargs:
- `patient_id: str`
- `study_uid: str | None`
- `series_uid: str | None`
- `client: ClarinetClient`

Пример:
```python
async def setup_slicer_workspace(patient_id, study_uid, series_uid, client):
    """Custom logic after series_markup is created."""
    ...

series().on_created().call(setup_slicer_workspace)
```

## Ограничения (v1)

- Conditions (`if_/or_/and_`) на entity flows работают только без record context (нет данных записи для сравнения)
- `update_record` action не поддерживается для entity flows (нечего обновлять)
- Infinite loop protection: entity flow создаёт record через API, но entity flows не триггерятся на создание записей (только на сущности)

## Тестирование

1. Unit-тест: `series().on_created().add_record("X")` — flow создаётся и регистрируется
2. Unit-тест: `engine.handle_entity_created("series", ...)` — вызывает действия
3. Integration-тест: POST /series → engine триггерится → record создаётся
4. Integration-тест: POST /dicom/import-study → engine триггерится для каждой серии
5. `make lint && make typecheck`

## Файлы

| Файл | Действие |
|------|----------|
| `src/services/recordflow/flow_record.py` | `entity_trigger`, `on_created()`, `ENTITY_REGISTRY`, factories |
| `src/services/recordflow/engine.py` | `entity_flows`, `handle_entity_created()`, `_execute_entity_action()` |
| `src/services/recordflow/flow_loader.py` | Очистка `ENTITY_REGISTRY`, factories в namespace |
| `src/services/recordflow/flow_builder.py` | Re-export `series`, `study`, `patient` |
| `src/services/recordflow/__init__.py` | Export новых символов |
| `src/api/routers/study.py` | Хуки в `add_series`, `add_study`, `add_patient` |
| `src/api/routers/dicom.py` | Хук в `import_study_from_pacs` |
| `examples/demo/record_flow.py` | `series().on_created().add_record("series_markup")` |
| `src/services/recordflow/CLAUDE.md` | Обновить документацию |
