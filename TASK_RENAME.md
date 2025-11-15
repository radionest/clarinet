# План рефакторинга: Task → Record, TaskDesign → RecordType

## Проблема

Текущий нейминг `Task` и `TaskDesign` вводит в заблуждение:

- **Task** фактически является **записью данных** с нечеткой JSON-структурой, а не задачей в классическом понимании
- **TaskDesign** описывает **тип записи** с JSON-схемой валидации, а не дизайн задачи

### Что на самом деле делают эти модели:

**Task (должен быть Record):**
- Хранит JSON-данные (`result: TaskResult`) с произвольной структурой
- Привязан к медицинским сущностям (Patient, Study, Series)
- Содержит метаданные: статус, timestamps, user_id
- `result` — это фактически **данные записи**, а не результат выполнения

**TaskDesign (должен быть RecordType):**
- Определяет **тип записи** через name (PK)
- Содержит `result_schema` — JSON Schema для валидации данных
- Описывает параметры обработки (slicer_script, validators)
- Определяет ограничения доступа (role_name, max_users, min_users)
- Определяет уровень DICOM (PATIENT/STUDY/SERIES)

## Новая модель данных

```python
# RecordType - тип записи (аналог TaskDesign)
class RecordType:
    name: str                          # PK, название типа записи
    description: str | None
    label: str | None

    # JSON Schema для валидации данных
    data_schema: RecordSchema | None   # было: result_schema

    # Параметры обработки
    slicer_script: str | None
    slicer_script_args: dict | None
    slicer_result_validator: str | None
    slicer_result_validator_args: dict | None

    # Ограничения доступа
    role_name: str | None              # FK → UserRole
    max_users: int | None
    min_users: int | None
    level: DicomQueryLevel             # PATIENT/STUDY/SERIES

    # Relationships
    records: list["Record"]            # было: tasks
    constraint_role: UserRole | None

# Record - запись данных (аналог Task)
class Record:
    id: int                            # PK

    # Тип записи
    record_type_name: str              # FK → RecordType.name (было: task_design_id)
    record_type: RecordType            # было: task_design

    # Данные записи (JSON с валидацией по RecordType.data_schema)
    data: RecordData | None            # было: result

    # Статус обработки
    status: RecordStatus               # было: TaskStatus
    info: str | None                   # было: info

    # Связи с медицинскими сущностями
    patient_id: str                    # FK → Patient
    patient: Patient
    study_uid: str | None              # FK → Study
    study: Study
    series_uid: str | None             # FK → Series
    series: Series | None

    # Пользователь и хранение
    user_id: UUID | None               # FK → User
    user: User | None

    # Timestamps
    created_at: datetime
    changed_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
```

## План рефакторинга

### Фаза 1: Подготовка (создание новых моделей)

#### 1.1. Создать новую модель RecordType
**Файл:** `src/models/record.py`

Создать классы:
- `RecordTypeBase` (базовая модель)
- `RecordType` (table=True)
- `RecordTypeCreate` (для API создания)
- `RecordTypeUpdate` (для API обновления)
- `RecordTypeFind` (для поиска)

**Изменения относительно TaskDesign:**
- `result_schema` → `data_schema`
- Обновить docstrings с новой семантикой

#### 1.2. Создать новую модель Record
**Файл:** `src/models/record.py` (продолжение)

Создать классы:
- `RecordStatus` (enum) — переименовать из TaskStatus
- `RecordBase` (базовая модель)
- `Record` (table=True)
- `RecordCreate` (для API создания)
- `RecordRead` (для API чтения с relationships)
- `RecordFind` (для поиска)
- `RecordFindData` (поиск по данным, аналог TaskFindResult)
- `RecordFindDataComparisonOperator` (enum)

**Изменения относительно Task:**
- `task_design_id` → `record_type_name`
- `task_design` → `record_type`
- `result` → `data`
- Все computed fields обновить под новую семантику
- Event listeners для timestamps

#### 1.3. Обновить типы
**Файл:** `src/types.py`

Добавить новые типы:
```python
# Record-related types
type RecordData = dict[str, Any]        # было: TaskResult
type RecordSchema = dict[str, Any]      # было: ResultSchema

# Deprecated (для обратной совместимости)
type TaskResult = RecordData
type ResultSchema = RecordSchema
```

#### 1.4. Обновить базовые модели
**Файл:** `src/models/base.py`

Переименовать:
```python
class RecordStatus(str, enum.Enum):  # было: TaskStatus
    pending = "pending"
    inwork = "inwork"
    finished = "finished"
    failed = "failed"
    pause = "pause"

# Алиас для совместимости (с предупреждением)
TaskStatus = RecordStatus  # deprecated
```

### Фаза 2: Создание миграции базы данных

#### 2.1. Создать Alembic миграцию
**Команда:**
```bash
alembic revision -m "Rename Task to Record and TaskDesign to RecordType"
```

**Файл:** `alembic/versions/XXXX_rename_task_to_record.py`

**Содержимое миграции:**

```python
def upgrade() -> None:
    # Переименовать таблицы
    op.rename_table('taskdesign', 'recordtype')
    op.rename_table('task', 'record')

    # Переименовать столбцы в recordtype
    op.alter_column('recordtype', 'result_schema', new_column_name='data_schema')

    # Переименовать столбцы в record
    op.alter_column('record', 'task_design_id', new_column_name='record_type_name')
    op.alter_column('record', 'result', new_column_name='data')

    # Обновить foreign keys
    op.drop_constraint('record_task_design_id_fkey', 'record', type_='foreignkey')
    op.create_foreign_key(
        'record_record_type_name_fkey',
        'record', 'recordtype',
        ['record_type_name'], ['name']
    )

    # Обновить foreign keys в других таблицах
    # study.tasks → study.records
    # series.tasks → series.records
    # patient.tasks → patient.records
    # user.tasks → user.records

def downgrade() -> None:
    # Обратная операция для rollback
    # ... (зеркальное отражение upgrade)
```

### Фаза 3: Рефакторинг кодовой базы

#### 3.1. Обновить связанные модели

**Файл:** `src/models/patient.py`
```python
# Было:
tasks: list["Task"] = Relationship(back_populates="patient")

# Стало:
records: list["Record"] = Relationship(back_populates="patient")
```

**Файл:** `src/models/study.py`
```python
# Было:
tasks: list["Task"] = Relationship(back_populates="study")

# Стало:
records: list["Record"] = Relationship(back_populates="study")

# В SeriesFind:
records: list["RecordFind"] = Field(default_factory=list)  # было: tasks
```

**Файл:** `src/models/user.py`
```python
# Было:
tasks: list["Task"] = Relationship(back_populates="user")
allowed_task_designs: list["TaskDesign"] = Relationship(back_populates="constraint_role")

# Стало:
records: list["Record"] = Relationship(back_populates="user")
allowed_record_types: list["RecordType"] = Relationship(back_populates="constraint_role")
```

#### 3.2. Обновить src/models/__init__.py

**Файл:** `src/models/__init__.py`

```python
# Новые импорты
from .record import (
    Record,
    RecordBase,
    RecordCreate,
    RecordRead,
    RecordFind,
    RecordFindData,
    RecordFindDataComparisonOperator,
    RecordType,
    RecordTypeBase,
    RecordTypeCreate,
    RecordTypeUpdate,
    RecordTypeFind,
    RecordStatus,
)

# Deprecated алиасы (с warnings)
import warnings

def _deprecated_import(old_name: str, new_name: str):
    warnings.warn(
        f"{old_name} is deprecated, use {new_name} instead",
        DeprecationWarning,
        stacklevel=2
    )

# Алиасы для обратной совместимости
Task = Record
TaskBase = RecordBase
TaskCreate = RecordCreate
TaskRead = RecordRead
TaskFind = RecordFind
TaskFindResult = RecordFindData
TaskFindResultComparisonOperator = RecordFindDataComparisonOperator
TaskDesign = RecordType
TaskDesignBase = RecordTypeBase
TaskDesignCreate = RecordTypeCreate
TaskDesignOptional = RecordTypeUpdate
TaskDesignFind = RecordTypeFind
TaskStatus = RecordStatus
```

#### 3.3. Создать RecordRepository

**Файл:** `src/repositories/record_repository.py`

Скопировать логику из существующих репозиториев, если они есть, или создать новый:

```python
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models import Record, RecordType

class RecordRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, record_id: int) -> Record | None:
        return await self.session.get(Record, record_id)

    async def find_by_type(self, record_type_name: str) -> list[Record]:
        result = await self.session.execute(
            select(Record).where(Record.record_type_name == record_type_name)
        )
        return list(result.scalars().all())

    # ... другие методы
```

#### 3.4. Обновить API роутеры

**Файл:** `src/api/routers/task.py` → `src/api/routers/record.py`

**Основные изменения:**

1. **Переименовать эндпоинты:**
   - `/tasks/types` → `/record-types`
   - `/tasks` → `/records`
   - `/tasks/{task_id}` → `/records/{record_id}`
   - `/tasks/{task_id}/result` → `/records/{record_id}/data`

2. **Обновить импорты:**
```python
from src.models import (
    Record,
    RecordCreate,
    RecordRead,
    RecordFind,
    RecordFindData,
    RecordFindDataComparisonOperator,
    RecordType,
    RecordTypeCreate,
    RecordTypeUpdate,
    RecordTypeFind,
    RecordStatus,
)
```

3. **Обновить функции:**
```python
# Было:
@router.get("/types", response_model=list[TaskDesign])
async def get_all_task_designs(...) -> Sequence[TaskDesign]:

# Стало:
@router.get("/", response_model=list[RecordType])
async def get_all_record_types(...) -> Sequence[RecordType]:
```

4. **Обновить параметры:**
```python
# Было:
async def submit_task_result(task_id: int, result: TaskResult, ...)

# Стало:
async def submit_record_data(record_id: int, data: RecordData, ...)
```

**Отдельный роутер для RecordType:**

**Файл:** `src/api/routers/record_type.py`

Вынести эндпоинты для работы с типами записей:
- `GET /record-types` — получить все типы
- `POST /record-types` — создать новый тип
- `GET /record-types/{name}` — получить тип по имени
- `PATCH /record-types/{name}` — обновить тип
- `DELETE /record-types/{name}` — удалить тип
- `POST /record-types/find` — поиск типов

#### 3.5. Обновить главное приложение

**Файл:** `src/api/app.py`

```python
# Было:
from src.api.routers import task

app.include_router(task.router)

# Стало:
from src.api.routers import record, record_type

app.include_router(record.router)
app.include_router(record_type.router)
```

#### 3.6. Обновить утилиты

**Файл:** `src/utils/bootstrap.py`

```python
# Обновить импорты
from src.models import RecordType, RecordTypeCreate

# Переименовать функции
async def create_demo_record_types_from_json(
    input_folder: str,
    demo_suffix: str = "demo"
) -> None:
    # было: create_demo_task_designs_from_json
    ...

async def add_record_type(
    record_type: RecordTypeCreate,
    session: AsyncSession
) -> RecordType:
    # было: add_task_design
    ...

# Обновить filter_task_schemas → filter_record_schemas
def filter_record_schemas(
    record_files: list[str],
    filter_suffix: str = "demo"
) -> list[str]:
    ...
```

**Обновить демо-данные:**
- Папка `examples/test/` — переименовать файлы задач
- `*_task.json` → `*_record.json`
- `*_task.schema.json` → `*_record.schema.json`

### Фаза 4: Тесты

#### 4.1. Обновить тесты

**Файл:** `tests/integration/test_task_crud.py` → `tests/integration/test_record_crud.py`

```python
# Обновить все импорты
from src.models import Record, RecordType, RecordCreate, RecordStatus

# Переименовать тесты
async def test_create_record(async_client):  # было: test_create_task
    ...

async def test_get_record(async_client):
    ...

async def test_update_record_status(async_client):
    ...

async def test_submit_record_data(async_client):  # было: test_submit_task_result
    ...

async def test_create_record_type(async_client):  # было: test_create_task_design
    ...
```

**Файл:** `tests/integration/test_api_endpoints.py`

Обновить все тесты, использующие Task/TaskDesign.

**Файл:** `tests/conftest.py`

Обновить фикстуры:
```python
@pytest.fixture
async def sample_record_type():  # было: sample_task_design
    ...

@pytest.fixture
async def sample_record():  # было: sample_task
    ...
```

**Файл:** `tests/utils/test_helpers.py`

Обновить вспомогательные функции для тестов.

#### 4.2. Обновить файл TODO_TESTS.md

Заменить все упоминания Task на Record.

### Фаза 5: Клиенты

#### 5.1. Python клиент

**Файл:** `src/client.py`

```python
# Обновить импорты
from src.models import Record, RecordType, RecordCreate

# Переименовать методы
class ClarinetClient:
    async def get_records(self) -> list[Record]:  # было: get_tasks
        response = await self.get("/records")
        ...

    async def create_record(self, record: RecordCreate) -> Record:
        response = await self.post("/records", json=record.model_dump())
        ...

    async def get_record_types(self) -> list[RecordType]:
        response = await self.get("/record-types")
        ...

    async def submit_record_data(self, record_id: int, data: dict):
        response = await self.post(f"/records/{record_id}/data", json=data)
        ...
```

**Файл:** `tests/test_client.py`

Обновить все тесты клиента.

#### 5.2. Frontend (Gleam)

**Файл:** `src/frontend/src/api/models.gleam`

```gleam
// Было:
pub type Task {
  Task(
    id: Int,
    task_design_id: String,
    result: Option(Dict(String, Dynamic)),
    status: TaskStatus,
    ...
  )
}

pub type TaskDesign {
  TaskDesign(
    name: String,
    result_schema: Option(Dict(String, Dynamic)),
    ...
  )
}

// Стало:
pub type Record {
  Record(
    id: Int,
    record_type_name: String,
    data: Option(Dict(String, Dynamic)),
    status: RecordStatus,
    ...
  )
}

pub type RecordType {
  RecordType(
    name: String,
    data_schema: Option(Dict(String, Dynamic)),
    ...
  )
}
```

**Файл:** `src/frontend/src/api/types.gleam`

Обновить все типы, связанные с Task.

**Файлы маршрутизации:**
- `src/frontend/src/router.gleam` — обновить роуты
- `src/frontend/src/pages/tasks/` → `src/frontend/src/pages/records/`
- Переименовать все файлы и модули

**Обновить store:**
- `src/frontend/src/store.gleam` — модель состояния

**API клиент:**
- `src/frontend/src/api/` — все функции запросов

### Фаза 6: Документация

#### 6.1. Обновить CLAUDE.md

**Файл:** `CLAUDE.md`

Обновить разделы:
- Project Structure — изменить описание моделей
- Database Models — описать Record и RecordType
- API Routers — обновить примеры
- Pre-commit checklist — упомянуть миграции

**Пример нового описания:**

```markdown
### Record Management

Clarinet использует систему **Record** (записей) для хранения структурированных данных:

- **RecordType** — тип записи с JSON-схемой валидации
- **Record** — сама запись с данными (JSON), привязанная к медицинским сущностям

Каждая запись имеет:
- `data` — JSON-данные, валидируемые по `RecordType.data_schema`
- `status` — статус обработки (pending, inwork, finished, failed, pause)
- Связи с Patient, Study, Series
- Метаданные обработки (user, timestamps)

Это НЕ задачи в классическом смысле, а именно **записи медицинских данных**.
```

#### 6.2. Создать миграционный гайд

**Файл:** `MIGRATION_TASK_TO_RECORD.md`

Документ для пользователей фреймворка:

```markdown
# Миграция с Task на Record

## Что изменилось

В версии X.X.X модели `Task` и `TaskDesign` были переименованы в `Record` и `RecordType`.

## Breaking Changes

### Модели
- `Task` → `Record`
- `TaskDesign` → `RecordType`
- `TaskStatus` → `RecordStatus`

### Поля
- `Record.result` → `Record.data`
- `Record.task_design_id` → `Record.record_type_name`
- `Record.task_design` → `Record.record_type`
- `RecordType.result_schema` → `RecordType.data_schema`

### API Endpoints
- `/tasks` → `/records`
- `/tasks/types` → `/record-types`
- `/tasks/{id}/result` → `/records/{id}/data`

### Database Tables
- `task` → `record`
- `taskdesign` → `recordtype`

## Как мигрировать

### 1. База данных
```bash
alembic upgrade head
```

### 2. Код
Обновить импорты:
```python
# Было:
from src.models import Task, TaskDesign, TaskCreate

# Стало:
from src.models import Record, RecordType, RecordCreate
```

### 3. Обратная совместимость
Старые импорты работают через алиасы (deprecated):
```python
from src.models import Task  # Warning: deprecated, use Record
```

## Timeline
- Version X.X.X: Новые имена, старые работают (deprecated)
- Version X+1.X: Удаление алиасов, breaking change
```

### Фаза 7: CI/CD и скрипты

#### 7.1. Обновить GitHub Actions

**Файл:** `.github/workflows/*.yml`

Проверить, нет ли в workflows упоминаний Task/TaskDesign в:
- Названиях джобов
- Переменных окружения
- Шагах тестирования

#### 7.2. Обновить Makefile

**Файл:** `Makefile`

Обновить targets, если они упоминают tasks:
```makefile
# Если есть команды типа:
# make seed-tasks → make seed-records
```

### Фаза 8: Финальная проверка

#### 8.1. Чеклист перед коммитом

- [ ] Все модели обновлены
- [ ] Миграция создана и протестирована (upgrade + downgrade)
- [ ] Все роутеры обновлены
- [ ] Репозитории обновлены
- [ ] Утилиты обновлены
- [ ] Тесты обновлены и проходят
- [ ] Frontend обновлен
- [ ] Python клиент обновлен
- [ ] Документация обновлена
- [ ] Pre-commit hooks проходят
- [ ] mypy проходит
- [ ] ruff format + ruff check проходят

#### 8.2. Тестирование миграции

```bash
# Создать тестовую БД с текущими данными
# Применить миграцию
alembic upgrade head

# Проверить структуру таблиц
psql -d clarinet -c "\d record"
psql -d clarinet -c "\d recordtype"

# Откатить
alembic downgrade -1

# Проверить, что данные на месте
psql -d clarinet -c "\d task"
```

#### 8.3. Запуск полного набора тестов

```bash
# Unit тесты
pytest tests/

# Integration тесты
pytest tests/integration/

# Coverage
pytest --cov=src tests/

# Type checking
mypy src/

# Linting
ruff check src/ tests/
ruff format src/ tests/
```

## Порядок выполнения (Summary)

1. ✅ **Фаза 1** — Создать новые модели Record/RecordType параллельно старым
2. ✅ **Фаза 2** — Создать и протестировать Alembic миграцию (с rollback!)
3. ✅ **Фаза 3** — Обновить все импорты и использования в backend
4. ✅ **Фаза 4** — Обновить все тесты
5. ✅ **Фаза 5** — Обновить клиенты (Python + Gleam frontend)
6. ✅ **Фаза 6** — Обновить документацию
7. ✅ **Фаза 7** — Проверить CI/CD и скрипты
8. ✅ **Фаза 8** — Финальная проверка и тестирование

## Риски и меры предосторожности

### Высокие риски:
1. **Потеря данных при миграции** — сделать backup БД перед миграцией
2. **Сломанные foreign keys** — тщательно проверить все связи
3. **Конфликты в frontend** — тестировать отдельно от backend

### Меры:
- Создать резервную копию БД перед миграцией
- Тестировать миграцию на копии production данных
- Поэтапный rollout: сначала backend, потом frontend
- Держать алиасы для совместимости минимум 1 версию

## Оценка времени

- Фаза 1: 2-3 часа
- Фаза 2: 1-2 часа (+ тестирование)
- Фаза 3: 3-4 часа
- Фаза 4: 2-3 часа
- Фаза 5: 2-3 часа (frontend сложнее)
- Фаза 6: 1 час
- Фаза 7-8: 1-2 часа

**Итого: ~15-20 часов** работы.

## Альтернативный подход (если есть production)

Если система уже в production и есть пользователи:

1. **Version N** — добавить новые модели, держать оба варианта
2. **Version N+1** — deprecated warnings на старые модели
3. **Version N+2** — удалить старые модели (breaking change)

Это даст пользователям время на миграцию.
