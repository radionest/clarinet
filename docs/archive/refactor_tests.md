# Refactor Test Isolation Strategy

## Проблема

### Текущая архитектура: два пути создания AnonymizationService

**Путь 1: Foreground (HTTP request) — работает**

```
HTTP request → FastAPI DI → get_anonymization_service()
  → SessionDep (get_async_session) → repos → AnonymizationService
```

В тестах `SessionDep` подменяется через `app.dependency_overrides[get_async_session]`,
и сервис получает `test_session` (in-memory SQLite). Все данные, созданные в тесте, видны.

**Путь 2: Background (`_create_anonymization_service`) — проблемный**

```
_dispatch_background_anonymization → _create_anonymization_service()
  → db_manager.get_async_session_context() → repos → AnonymizationService
```

Этот путь полностью обходит FastAPI DI. Он напрямую импортирует синглтон `db_manager`
и `settings`, создаёт свою сессию.

### Почему background-путь обходит DI

Docstring в `tasks.py`:

> "Both construct an AnonymizationService with a fresh DB session, avoiding the
> closed-session bug that occurs when DI-scoped services are used in background tasks."

FastAPI DI — request-scoped. Когда endpoint возвращает `{"status": "started"}`,
request lifecycle завершается, DI-сессия закрывается. Background task, работающий
после этого, получит `Session is closed`.

### Почему данные невидимы между сессиями

1. **`db_manager`** — синглтон (`db_manager.py:178`), создаёт engine лениво
   из `settings.database_url`. Тесты не могут его подменить через `dependency_overrides`.

2. **In-memory SQLite изолирован per-connection** — тестовый `test_engine`
   (`sqlite+aiosqlite:///:memory:`) и engine `db_manager` — это две разных БД.

3. **`settings` тоже синглтон** — `AnonymizationService` читает `settings.anon_uid_salt`,
   `settings.storage_path` напрямую. Тесты вынуждены патчить через
   `patch("src.services.anonymization_service.settings")`.

4. **Даже с общим engine** `StaticPool` не спасает — это "переиспользовать соединение
   внутри **этого** engine", а не "расшарить между разными engine".

### Текущая стратегия: Fresh DB per test

`test_engine` — function-scoped (пересоздаётся на каждый тест):

```
test_engine (function) → create_async_engine("sqlite:///:memory:") → CREATE ALL TABLES
test_session (function) → sessionmaker(test_engine) → yield → rollback
```

Каждый тест получает чистую БД. Rollback в teardown фактически избыточен —
БД уничтожается вместе с engine. Изоляция надёжная, но:
- `db_manager` создаёт свой engine → другая БД
- Дорого: `CREATE ALL TABLES` на каждый тест

### Текущий workaround в тестах

Тесты обходят проблему, не тестируя реальный background-путь:

- `test_anonymize_study_background` — патчит `_dispatch_background_anonymization` целиком
- `test_anonymization_task.py` — патчит `db_manager` и `settings` через `unittest.mock`
- Foreground-тесты — патчат `settings` в двух местах (7-8 строк boilerplate на тест)

---

## Решение: Nested Transaction (Savepoint)

Классический паттерн из документации SQLAlchemy, адаптированный под двойную сессию.

### Принцип

```
Connection ─── BEGIN (outer) ─────────────────────── ROLLBACK (teardown)
                  │
                  ├── test_session ── SAVEPOINT sp1 ── ... ── RELEASE sp1
                  │
                  └── db_manager session ── SAVEPOINT sp2 ── ... ── RELEASE sp2
```

Всё происходит внутри одной транзакции на одном соединении. `ROLLBACK` в конце
отменяет ВСЁ — и то что сделал тест, и то что сделал background task.

### Реализация

```python
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel


# 1. Engine — один на весь прогон (session-scoped)
@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # SQLite quirk: отключаем автоматический BEGIN от pysqlite
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        dbapi_conn.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine
    await engine.dispose()


# 2. Connection + outer transaction — per test
@pytest_asyncio.fixture
async def test_connection(test_engine):
    async with test_engine.connect() as conn:
        trans = await conn.begin()          # BEGIN outer
        yield conn
        await trans.rollback()              # ROLLBACK всего


# 3. test_session — работает через SAVEPOINT внутри outer transaction
@pytest_asyncio.fixture
async def test_session(test_connection):
    session = AsyncSession(
        bind=test_connection,
        join_transaction_mode="create_savepoint",  # ключевой параметр
        expire_on_commit=False,
    )
    yield session
    await session.close()


# 4. db_manager подменяется — его сессии тоже идут через тот же connection
@pytest_asyncio.fixture(autouse=True)
async def _patch_db_manager(test_connection):
    from src.utils.db_manager import db_manager

    @asynccontextmanager
    async def _test_session_context():
        session = AsyncSession(
            bind=test_connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            await session.close()

    original = db_manager.get_async_session_context
    db_manager.get_async_session_context = _test_session_context
    yield
    db_manager.get_async_session_context = original
```

### Что происходит при выполнении теста

```
1. test_connection: BEGIN                      ← outer transaction
2. test_session.add(patient); commit()         ← SAVEPOINT sp1; INSERT; RELEASE sp1
3. endpoint вызывает background task
4. _create_anonymization_service()
   → db_manager.get_async_session_context()    ← SAVEPOINT sp2 (на том же connection!)
   → SELECT patient...                         ← ВИДИТ данные из шага 2
   → UPDATE study.anon_uid; commit()           ← RELEASE sp2
5. teardown: ROLLBACK                          ← всё отменяется, БД чистая
```

### Почему background task видит данные

Оба сеанса работают на одном и том же `connection`. Savepoint — это не отдельная
транзакция, а вложенная точка сохранения. `RELEASE SAVEPOINT` (commit) делает данные
видимыми для всех сессий на этом соединении, но `ROLLBACK` внешней транзакции
в teardown отменяет всё.

---

## Подводные камни

### 1. SQLite + SAVEPOINT + aiosqlite

SQLite поддерживает SAVEPOINT, но pysqlite/aiosqlite по умолчанию эмитят `BEGIN`
неявно, что конфликтует с явным управлением транзакциями. Нужны event listeners
(см. реализацию выше — `_set_sqlite_pragma` и `_begin`).

Без этого SQLAlchemy не сможет управлять транзакциями вручную — SAVEPOINT будет падать.

### 2. `join_transaction_mode="create_savepoint"`

Параметр SQLAlchemy 2.0+. Говорит сессии: "не начинай свою транзакцию, а создавай
SAVEPOINT внутри существующей". Без него `AsyncSession(bind=connection)` попытается
начать вложенный BEGIN, что на SQLite невозможно.

### 3. `session.commit()` в тестах

С savepoint-подходом `commit()` внутри теста делает `RELEASE SAVEPOINT`, а не
настоящий `COMMIT`. Данные видны другим сессиям на том же connection, но внешний
`ROLLBACK` всё отменит. Два последовательных commit в одной сессии — нормально
(каждый создаёт/release свой savepoint).

### 4. `get_async_session_context` делает auto-commit

В `db_manager.py:135`:

```python
yield session
await session.commit()  # ← это станет RELEASE SAVEPOINT
```

Это нормально — savepoint release, не настоящий commit.

### 5. Совместимость с `fresh_session` fixture

`fresh_session` создаёт отдельный `AsyncSession`. Его тоже нужно привязать
к `test_connection` с `join_transaction_mode="create_savepoint"`, иначе он создаст
свой connection и не увидит данных.

### 6. Тесты, проверяющие rollback-поведение

Если тест проверяет, что `session.rollback()` откатывает данные — с savepoint-подходом
rollback откатит до savepoint, не до начала теста. Обычно не проблема, но стоит проверить.

### 7. Scope: session-scoped engine vs function-scoped connection

`test_engine` — session-scoped, `test_connection` — function-scoped. Это правильно:
engine живёт всю сессию, connection создаётся per test.

---

## Сравнение с альтернативами

| | Shared + Truncate | Savepoint (рекомендуемый) |
|---|---|---|
| Изоляция | Явный cleanup | Автоматический rollback |
| Скорость | Медленно (DELETE/CREATE) | Быстро (ROLLBACK) |
| Надёжность | Хрупкая (FK порядок) | Надёжная |
| Background tasks видят данные | Да | Да |
| Изменения в тестах | Минимум | Средне |
| Изменения в prod коде | 0 | 0 |
| SQLite quirks | Нет | Да (event listeners) |
| Сложность setup | Низкая | Средняя |

---

## План миграции

### Шаг 1: Обновить `tests/conftest.py`

- Заменить function-scoped `test_engine` на session-scoped с `StaticPool` + event listeners
- Добавить `test_connection` fixture (function-scoped)
- Обновить `test_session` на `join_transaction_mode="create_savepoint"`
- Добавить `_patch_db_manager` autouse fixture
- Обновить `fresh_session` аналогично

### Шаг 2: Обновить client fixtures

- `client`, `unauthenticated_client`, `fresh_client` — убедиться что
  `override_get_session` возвращает test_session (уже так).

### Шаг 3: Упростить тесты анонимизации

- Убрать `patch("src.services.anonymization_service.settings")` boilerplate
  (это отдельная задача — вынос settings в конфиг-объект AnonymizationService)
- Убрать `patch("src.api.routers.dicom._dispatch_background_anonymization")`
  из test_anonymize_study_background — теперь можно тестировать реальный путь

### Шаг 4: Прогнать все тесты

- `make test` — убедиться что существующие тесты проходят
- Особое внимание: тесты с `fresh_session`, e2e тесты, pipeline тесты

### Шаг 5: Опционально — config-объект для AnonymizationService

Отдельная задача: убрать прямое чтение `settings` из `AnonymizationService`
и `SeriesFilter`, заменив на injectable config dataclass.
