# План: Версионирование и синхронизация Record в Clarinet

## Цель

Расширить фреймворк Clarinet универсальными механизмами для:
1. Версионирования записей (Record) и связанных файлов
2. Связывания записей между собой (parent-child, master-modality)
3. Инвалидации зависимых записей при изменении родительской
4. Prefill-скриптов для создания записей с предзаполненными данными

**Важно**: Фреймворк должен быть универсальным. Конкретный дизайн исследования (CT/MRI/Master model) реализуется через конфигурацию RecordType, а не через изменение схемы БД.

---

## Архитектура решения

### 1. Новая модель: RecordVersion

Хранит версии записи с файлами и данными.

```python
# src/models/record.py

class RecordVersion(SQLModel, table=True):
    """Version of a record with associated files."""

    id: int | None = Field(default=None, primary_key=True)
    record_id: int = Field(foreign_key="record.id", index=True)
    version_number: int

    # File storage
    file_path: str | None = None  # Relative path to working_folder
    file_checksum: str | None = None  # MD5 for integrity

    # Version data (JSON) - хранит data этой версии
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Versioning chain
    parent_version_id: int | None = Field(default=None, foreign_key="recordversion.id")
    is_current: bool = Field(default=True, index=True)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by_user_id: UUID | None = None

    # Relationships
    record: "Record" = Relationship(back_populates="versions")
```

### 2. Новая модель: RecordLink

Связывает записи между собой для реализации иерархий.

```python
# src/models/record.py

class RecordLinkType(str, enum.Enum):
    """Types of links between records."""
    PARENT_CHILD = "parent_child"      # Иерархия (Master -> Modality)
    DEPENDS_ON = "depends_on"          # Зависимость (для инвалидации)
    DERIVES_FROM = "derives_from"      # Унаследовано от
    SUPERSEDES = "supersedes"          # Заменяет

class RecordLink(SQLModel, table=True):
    """Links between records for relationships and dependencies."""

    id: int | None = Field(default=None, primary_key=True)

    source_record_id: int = Field(foreign_key="record.id", index=True)
    target_record_id: int = Field(foreign_key="record.id", index=True)
    link_type: RecordLinkType

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Relationships
    source_record: "Record" = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[RecordLink.source_record_id]"}
    )
    target_record: "Record" = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[RecordLink.target_record_id]"}
    )
```

### 3. Расширение RecordType

Добавить поля для invalidation и parent type конфигурации.

```python
# Добавить в RecordType

# Invalidation configuration
invalidation_mode: str | None = Field(default=None)  # "hard", "soft", None
invalidates_on_change: bool = Field(default=False)  # При изменении этого типа инвалидировать зависимые

# Parent type for automatic linking
parent_record_type_name: str | None = Field(default=None, foreign_key="recordtype.name")
```

**Примечание**: Поле `prefill_script` не нужно - prefill handlers регистрируются через декоратор `@prefill_handler('record_type_name')`, где имя handler'а совпадает с именем RecordType.

### 4. Расширение Record

```python
# Добавить в Record

# Invalidation status
invalidation_status: str | None = Field(default=None)  # "valid", "needs_review", "superseded"
invalidated_at: datetime | None = None
invalidated_by_record_id: int | None = Field(default=None, foreign_key="record.id")

# Relationships
versions: list["RecordVersion"] = Relationship(back_populates="record")
outgoing_links: list["RecordLink"] = Relationship(
    sa_relationship_kwargs={"foreign_keys": "[RecordLink.source_record_id]"}
)
incoming_links: list["RecordLink"] = Relationship(
    sa_relationship_kwargs={"foreign_keys": "[RecordLink.target_record_id]"}
)
```

---

## Сервисы

### 5. RecordVersionService

```python
# src/services/record_version.py

class RecordVersionService:
    """Manages record versions."""

    async def create_version(
        self,
        record: Record,
        data: dict,
        file_path: str | None = None,
        user_id: UUID | None = None
    ) -> RecordVersion:
        """Create new version, mark previous as non-current."""

    async def get_current_version(self, record_id: int) -> RecordVersion | None:
        """Get current version of record."""

    async def get_version_history(self, record_id: int) -> list[RecordVersion]:
        """Get all versions ordered by version_number."""

    async def compare_versions(
        self,
        version_a: RecordVersion,
        version_b: RecordVersion
    ) -> dict:
        """Compare two versions, return diff."""
```

### 6. RecordLinkService

```python
# src/services/record_link.py

class RecordLinkService:
    """Manages record relationships."""

    async def link_records(
        self,
        source: Record,
        target: Record,
        link_type: RecordLinkType,
        metadata: dict | None = None
    ) -> RecordLink:
        """Create link between records."""

    async def get_linked_records(
        self,
        record: Record,
        link_type: RecordLinkType | None = None,
        direction: str = "outgoing"  # "outgoing", "incoming", "both"
    ) -> list[Record]:
        """Get records linked to this one."""

    async def get_dependents(self, record: Record) -> list[Record]:
        """Get records that depend on this one (for invalidation)."""
```

### 7. InvalidationService

```python
# src/services/invalidation.py

class InvalidationService:
    """Manages record invalidation on changes."""

    async def invalidate_dependents(
        self,
        source_record: Record,
        mode: str = "hard"  # "hard" = pending, "soft" = needs_review
    ) -> list[Record]:
        """
        Invalidate all records that depend on source_record.

        For "hard" mode: set status to pending, clear user assignment
        For "soft" mode: set invalidation_status to needs_review

        Returns list of invalidated records.
        """

    async def check_invalidation(self, record: Record) -> bool:
        """Check if record needs re-review based on parent changes."""
```

### 8. PrefillService (Registry Pattern)

```python
# src/services/prefill.py

from typing import Callable, Awaitable
from dataclasses import dataclass

# Global registry для prefill handlers
_prefill_handlers: dict[str, Callable[["PrefillContext"], Awaitable[dict]]] = {}

def prefill_handler(record_type_name: str):
    """
    Декоратор для регистрации prefill handler по имени RecordType.

    Использование:
        @prefill_handler("ct_screen_analysis")
        async def prefill_ct_screen(ctx: PrefillContext) -> dict:
            # Копируем lesions из parent (master model)
            return {"lesions": ctx.parent_record.data.get("lesions", [])}
    """
    def decorator(func: Callable[["PrefillContext"], Awaitable[dict]]):
        _prefill_handlers[record_type_name] = func
        return func
    return decorator

@dataclass
class PrefillContext:
    """Context passed to prefill handlers."""
    record: Record
    record_type: RecordType
    parent_record: Record | None  # Родительская запись (например, master model)
    linked_records: list[Record]  # Связанные записи
    session: AsyncSession
    trigger_record: Record | None = None  # Запись, которая вызвала prefill (при invalidation)

class PrefillService:
    """Executes prefill handlers for new records."""

    async def execute_prefill(self, record: Record, context: PrefillContext) -> dict:
        """
        Выполняет prefill handler если он зарегистрирован для данного RecordType.
        Handler определяется по имени RecordType.
        """
        handler = _prefill_handlers.get(record.record_type_name)
        if handler:
            return await handler(context)
        return {}

    def has_handler(self, record_type_name: str) -> bool:
        """Проверяет, есть ли зарегистрированный handler для типа."""
        return record_type_name in _prefill_handlers
```

**Пример регистрации handler'ов (в пользовательском коде, не в фреймворке):**

```python
# liver_study/prefill_handlers.py

from clarinet.services.prefill import prefill_handler, PrefillContext

@prefill_handler("ct_screen_analysis")
async def prefill_ct_screen(ctx: PrefillContext) -> dict:
    """Копирует lesions из master model в CT screen analysis."""
    if ctx.parent_record:
        lesions = ctx.parent_record.data.get("lesions", [])
        # Очищаем CT-специфичные поля, оставляем только id и label
        return {
            "lesions": [
                {"id": l["id"], "label": l["label"], "ct_characteristics": {}}
                for l in lesions
            ]
        }
    return {"lesions": []}

@prefill_handler("mri_preop_analysis")
async def prefill_mri_preop(ctx: PrefillContext) -> dict:
    """Копирует lesions из master model в MRI preop analysis."""
    if ctx.parent_record:
        lesions = ctx.parent_record.data.get("lesions", [])
        return {
            "lesions": [
                {"id": l["id"], "label": l["label"], "mri_characteristics": {}}
                for l in lesions
            ]
        }
    return {"lesions": []}
```

---

## TaskFlow (портирование в Clarinet)

Портировать TaskFlow из klara в clarinet как часть фреймворка.

### 9. Структура портированного TaskFlow

```
src/services/taskflow/
├── __init__.py          # Экспорт публичного API
├── engine.py            # RecordFlowEngine - исполнитель workflow
├── flow_record.py       # DSL для определения workflow (аналог flow_task.py)
├── flow_result.py       # Сравнение результатов записей
├── flow_condition.py    # Условная логика
├── flow_builder.py      # Вспомогательные функции
└── flow_loader.py       # Загрузка flows из файлов
```

### 10. RecordFlowEngine (адаптация для Record)

```python
# src/services/taskflow/engine.py

class RecordFlowEngine:
    """Engine for executing record workflows on status changes."""

    def __init__(self, session_factory: Callable[[], AsyncSession]):
        self.session_factory = session_factory
        self.flows: dict[str, list[FlowRecord]] = {}
        self.invalidation_service: InvalidationService
        self.prefill_service: PrefillService

    async def handle_record_status_change(
        self,
        record: Record,
        old_status: RecordStatus | None = None
    ) -> None:
        """Main method to handle record status changes and trigger flows."""
        record_type_name = record.record_type_name

        if record_type_name not in self.flows:
            return

        context = await self._get_record_context(record)

        for flow in self.flows[record_type_name]:
            if flow.status_trigger is None or flow.status_trigger == record.status:
                await self._execute_flow(flow, record, context)

    async def _execute_action(self, action: dict, record: Record, context: dict) -> None:
        action_type = action.get("type")

        match action_type:
            case "create_record":
                await self._create_record(action, record, context)
            case "update_record":
                await self._update_record(action, record, context)
            case "invalidate_dependents":
                await self._invalidate_dependents(action, record, context)
            case "call_function":
                await self._call_function(action, record, context)

    async def _invalidate_dependents(self, action: dict, record: Record, context: dict) -> None:
        """Инвалидировать все зависимые записи."""
        mode = action.get("mode", "hard")
        await self.invalidation_service.invalidate_dependents(record, mode=mode)
```

### 11. FlowRecord DSL

```python
# src/services/taskflow/flow_record.py

def record(record_type_name: str) -> "FlowRecord":
    """Entry point for defining record workflows."""
    return FlowRecord(record_type_name)

class FlowRecord:
    """DSL for defining record-based workflows."""

    def __init__(self, record_type_name: str):
        self.record_type_name = record_type_name
        self.status_trigger: str | None = None
        self.conditions: list[FlowCondition] = []
        self.actions: list[dict] = []

    def on_status(self, status: str | RecordStatus) -> "FlowRecord":
        """Trigger workflow on specific status."""
        self.status_trigger = str(status)
        return self

    def if_(self, condition: ComparisonResult) -> "FlowRecord":
        """Add condition."""
        self._current_condition = FlowCondition(condition)
        self.conditions.append(self._current_condition)
        return self

    def invalidate_dependents(self, mode: str = "hard") -> "FlowRecord":
        """Invalidate all records that depend on this one."""
        action = {"type": "invalidate_dependents", "mode": mode}
        self._add_action(action)
        return self

    def create_record(self, record_type_name: str, **kwargs) -> "FlowRecord":
        """Create new record (will use prefill if handler registered)."""
        action = {"type": "create_record", "record_type_name": record_type_name, "params": kwargs}
        self._add_action(action)
        return self

    def update_record(self, record_type_name: str, **kwargs) -> "FlowRecord":
        """Update existing record."""
        action = {"type": "update_record", "record_type_name": record_type_name, "params": kwargs}
        self._add_action(action)
        return self

    def call(self, func: Callable, *args, **kwargs) -> "FlowRecord":
        """Call custom function."""
        action = {"type": "call_function", "func": func, "args": args, "kwargs": kwargs}
        self._add_action(action)
        return self

    @property
    def result(self) -> "FlowResult":
        """Access record data for comparisons."""
        return FlowResult(self.record_type_name)
```

### 12. Интеграция в API

```python
# src/api/app.py

from src.services.taskflow import RecordFlowEngine

# При старте приложения
app.state.record_flow_engine = RecordFlowEngine(get_async_session)
app.state.record_flow_engine.load_flows_from_directory("flows/")

# src/api/routers/record.py

@router.post("/{record_id}/data")
async def submit_record_data(
    record_id: int,
    data: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
):
    record = await get_record(record_id, session)
    old_status = record.status

    record.data = data
    record.status = RecordStatus.finished
    await session.commit()

    # Trigger workflow в background
    flow_engine: RecordFlowEngine = request.app.state.record_flow_engine
    if flow_engine:
        background_tasks.add_task(
            flow_engine.handle_record_status_change,
            record,
            old_status
        )

    return record
```

---

## API Endpoints

### 11. Новые endpoints

```python
# src/api/routers/record.py

# Versions
GET    /records/{id}/versions           # Список версий
GET    /records/{id}/versions/{v_id}    # Конкретная версия
POST   /records/{id}/versions           # Создать новую версию
GET    /records/{id}/versions/compare   # Сравнить версии

# Links
GET    /records/{id}/links              # Связи записи
POST   /records/{id}/links              # Создать связь
DELETE /records/{id}/links/{link_id}    # Удалить связь
GET    /records/{id}/dependents         # Зависимые записи

# Invalidation
POST   /records/{id}/invalidate         # Инвалидировать зависимые
POST   /records/{id}/revalidate         # Отметить как валидную

# RecordType
GET    /records/types/{name}/prefill-schema  # Получить схему prefill
```


---

## Пример использования: Liver Study

### Конфигурация RecordTypes

```python
# Пример конфигурации для исследования печени (не часть фреймворка)

# Master model
master_liver_type = RecordType(
    name="master_liver_model",
    level=DicomQueryLevel.STUDY,
    invalidates_on_change=True,  # При изменении инвалидирует зависимые
    data_schema={
        "type": "object",
        "properties": {
            "lesions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string"},
                        "segment": {"type": "string"},
                        "coordinates": {"type": "object"}
                    },
                    "required": ["id", "label"]
                }
            }
        }
    },
    slicer_script="liver_master_model"
)

# CT Screen Analysis
ct_screen_type = RecordType(
    name="ct_screen_analysis",
    level=DicomQueryLevel.SERIES,
    parent_record_type_name="master_liver_model",  # Зависит от master
    invalidation_mode="hard",  # При инвалидации -> pending
    data_schema={...}
)
# Prefill handler регистрируется через декоратор @prefill_handler("ct_screen_analysis")

# Аналогично для CT preop, MRI preop
```

### RecordFlow Definition

```python
# liver_study/workflows.py
from clarinet.services.taskflow import record

# Workflow для liver study
record('master_liver_model')
    .on_status('finished')
    .invalidate_dependents(mode='hard')

record('ct_screen_analysis')
    .on_status('finished')
    .if_(record('ct_screen_analysis').result.all_lesions_reviewed == True)
    .call(check_all_modalities_complete)
```

---

## Файлы для модификации

### Clarinet (фреймворк)

**Модели:**
1. `src/models/record.py` - добавить RecordVersion, RecordLink, расширить Record и RecordType
2. `src/models/base.py` - добавить RecordLinkType, InvalidationStatus enums
3. `src/models/__init__.py` - экспортировать новые модели

**Сервисы:**
4. `src/services/record_version.py` - новый сервис (создать)
5. `src/services/record_link.py` - новый сервис (создать)
6. `src/services/invalidation.py` - новый сервис (создать)
7. `src/services/prefill.py` - новый сервис с декоратором (создать)

**TaskFlow (портировать из klara):**
8. `src/services/taskflow/__init__.py` - экспорт публичного API
9. `src/services/taskflow/engine.py` - RecordFlowEngine
10. `src/services/taskflow/flow_record.py` - DSL (аналог flow_task.py)
11. `src/services/taskflow/flow_result.py` - копировать из klara с адаптацией
12. `src/services/taskflow/flow_condition.py` - копировать из klara
13. `src/services/taskflow/flow_loader.py` - загрузка flows

**API:**
14. `src/api/routers/record.py` - добавить endpoints для versions, links, invalidation
15. `src/api/app.py` - инициализация RecordFlowEngine

**Миграции:**
16. `alembic/versions/xxx_add_versioning_and_links.py` - новая миграция

---

## Порядок реализации

### Фаза 1: Модели и миграция
1. Добавить enums в `base.py`
2. Добавить RecordVersion, RecordLink в `record.py`
3. Расширить Record и RecordType
4. Создать и применить миграцию

### Фаза 2: Базовые сервисы
5. RecordVersionService
6. RecordLinkService
7. Unit-тесты для сервисов

### Фаза 3: Invalidation и Prefill
8. InvalidationService
9. PrefillService с декоратором @prefill_handler
10. Интеграция с record router

### Фаза 4: TaskFlow
11. Портировать flow_result.py, flow_condition.py из klara
12. Создать flow_record.py (DSL)
13. Создать RecordFlowEngine
14. Интеграция в app.py

### Фаза 5: API и тесты
15. Новые endpoints
16. Integration тесты
17. Документация
