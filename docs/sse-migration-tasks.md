# SSE migration — детальный план реализации

Исполняемая декомпозиция [sse-migration-plan.md](sse-migration-plan.md) (там — обоснования; здесь — только действия). Конверсия WS→SSE: **backend-движок событий (`services/events/`, bus, capture, RBAC, audit) переносится из WS-плана дословно** — он транспортно-независим. Меняются: эндпоинт (`websocket()` → `StreamingResponse`), auth (свой helper → штатный `CurrentUserDep`), фронт-транспорт (`WebSocket` → `EventSource`), nginx. `file:line` сверены с `main@bae1d23` (2026-06-12); если код сместился — ориентируйся на имена функций.

## Правила выполнения

1. Работать в worktree от **main** (`EnterWorktree`), не от ветки этого документа.
2. Фазы 1→4 — строго по порядку, **один коммит на фазу** (conventional commits, English). Фаза 5 заблокирована до мержа PR #330/#332. Фаза 6 (nginx) — в любой момент после фазы 2.
3. После каждой фазы — блок «Проверка фазы» обязателен. Тесты гонять один раз с выводом в файл: `./scripts/run_tests.sh -k "sse" -q > /tmp/test-sse.txt 2>&1`.
4. Wire-протокол ниже — единственный источник истины. JSON полезной нагрузки **тот же, что в WS-плане**; меняется только SSE-обёртка (`data: ...\n\n`).
5. Новые публичные функции — короткий docstring (English, "why" + gotchas). Никаких `print` — `from clarinet.utils.logger import logger`.

## Wire-протокол (server→client, SSE; v1: клиент ничего не шлёт)

Каждый кадр — SSE-`data:` строка с JSON. Первой строкой потока — `retry: 3000` (интервал нативного reconnect). Keepalive — `data:`-кадр `ping` (НЕ SSE-комментарий: комментарии до `onmessage` не доходят, watchdog их не увидит).

```
retry: 3000

data: {"type": "entity", "entity": "record", "action": "created", "id": "123", "record_type_name": "ct_seg", "user_id": "<uuid>|null"}

data: {"type": "entity", "entity": "patient|study|series|record_type|user", "action": "created|updated|deleted", "id": "<string>"}

data: {"type": "task_progress", "task": "preload", "task_id": "preload_ab12...", "payload": {"status": "fetching", "received": 10, ...}}

data: {"type": "task_progress", "task": "quarto_render", "task_id": "<render_id>", "payload": {"name": ..., "render_id": ..., "status": ..., "ready": {...}, "error": ...}}

data: {"type": "auth_expired"}

data: {"type": "ping"}
```

- `id` — **всегда строка** (`Record.id: int` → `str(id)`); ключи кэша фронта уже строковые.
- `record_type_name`/`user_id` присутствуют только у `entity=record`.
- `payload` у `task_progress` — байт-в-байт тот же dict, что отдаёт polling-эндпоинт.
- **Нет close-кодов** (SSE их не имеет). Семантика: `auth_expired`-кадр → клиент `EventSource.close()` + Logout (без reconnect); закрытие потока сервером без кадра → `EventSource` авто-reconnect → resync; невалидная cookie на (ре)коннекте → HTTP 401 → `EventSource` CLOSED (стоп).

## Осознанные отступления от sse-migration-plan.md

| # | Что | Почему |
|---|---|---|
| 1 | Auth — штатный `CurrentUserDep`, **без** `accept()/close(code)`-танца | SSE — обычный GET; невалидная cookie → 401 до старта стрима → `EventSource` CLOSED без reconnect-цикла (проблема WS-отступления #1 не существует) |
| 2 | Quarto: поллинг страницы **остаётся без изменений**, push — opportunistic-ускорение | при `pipeline_enabled=True` рендер пишет `status.json` из TaskIQ-воркера (другой процесс) — in-process bus его не видит (`quarto_report_service.py:215-285`) |
| 3 | Preload-fallback: поллинг-таймер не выключается, а **пропускает tick**, если последний push свежее интервала | убирает координацию main↔preload; запросы исчезают при живом потоке и сами возобновляются при разрыве |
| 4 | Explicit emit для `delete_patient`/`delete_study` — **только если тест покажет, что UoW не видит детей** | `cascade_delete=True` на relationships (ORM-каскад) загружает детей при flush → `session.deleted`; см. 2.7 |
| 5 | Reconnect — **нативный `EventSource`**, не ручной backoff | браузер реконнектится сам (интервал = серверный `retry: 3000`); `sse.gleam` теряет `attempt`/`ReconnectTick`/backoff, остаётся только watchdog (анти-зомби) |
| 6 | RBAC-набор типов — новый лёгкий запрос имён по ролям, не `get_available_type_counts` | тот метод считает только `pending`-записи — тип без pending выпал бы из фильтра |
| 7 | Resync (`InvalidateAll` + re-init) — только при **повторном** подключении (`has_connected_once`) | при первом connect страница только что инициализирована; re-init дал бы двойную загрузку |
| 8 | `Errored(2)` (CLOSED) → стоп без Logout; Logout только по `auth_expired`-кадру | CLOSED не различает 401/503/404 — слать Logout на временный 503 при рестарте нельзя |

---

## Фаза 1 — настройки

> pyproject **не трогаем** — `StreamingResponse` это ядро Starlette, новых зависимостей нет (в WS-плане здесь добавлялся `websockets`).

### 1.1 `clarinet/settings.py`
В класс `Settings` (env-префикс `CLARINET_`), после session-блока (после `session_cache_ttl_seconds`):
```python
# SSE push (single-process in-memory bus; see services/events/bus.py)
sse_enabled: bool = True
sse_revalidate_seconds: int = 300  # session re-check interval per connection
sse_send_queue_size: int = 256  # per-connection send queue; overflow -> close stream (slow consumer)
```

### 1.2 `clarinet/api/routers/info.py`
В dict ответа `get_project_info` (строки 17–21, рядом с `viewers`) добавить ключ:
```python
"sse_enabled": settings.sse_enabled,
```

### 1.3 Frontend: `src/api/info.gleam`
- В тип `ProjectInfo` добавить поле `sse_enabled: Bool` (последним).
- В `project_info_decoder()`: `use sse_enabled <- decode.optional_field("sse_enabled", False, decode.bool)` — **default `False`** (старый бэкенд без поля ⇒ не подключаемся). Помни: default — **второй** аргумент.

### Проверка фазы 1
```bash
make check
uv run pytest tests/ -k "info" -q > /tmp/test-sse-p1.txt 2>&1
make frontend-check
```
Коммит: `feat(sse): add sse_* settings and sse_enabled in /api/info`

---

## Фаза 2 — бэкенд: events-модуль, роутер, capture

Новый пакет `clarinet/services/events/` — четыре файла: `__init__.py`, `models.py`, `bus.py`, `capture.py`. Зависит только от `models`, `settings`, `utils.logger` — импортируется из репозиториев/сервисов без циклов. **`models.py`/`capture.py` идентичны WS-плану**; `bus.py` отличается только именем dataclass (`WsConnection`→`SseConnection`).

### 2.1 `clarinet/services/events/models.py`
```python
class EntityEvent(BaseModel):
    entity: Literal["record", "patient", "study", "series", "record_type", "user"]
    action: Literal["created", "updated", "deleted"]
    id: str
    record_type_name: str | None = None  # record only
    user_id: UUID | None = None  # record only: assigned user

    def to_wire(self) -> str: ...  # json.dumps по протоколу; user_id -> str | None

class TaskProgressEvent(BaseModel):
    task: Literal["preload", "quarto_render"]
    task_id: str
    payload: dict[str, Any]
    user_id: UUID | None = None  # адресат; в кадр НЕ сериализуется

    def to_wire(self) -> str: ...  # {"type":"task_progress","task":...,"task_id":...,"payload":...}
```
`type Event = EntityEvent | TaskProgressEvent` (PEP 695 alias). `to_wire()` отдаёт **голый JSON** (без `data: `/`\n\n`) — SSE-обёртку добавляет роутер при `yield`.

### 2.2 `clarinet/services/events/bus.py`
```python
@dataclass
class SseConnection:
    user_id: UUID
    is_admin: bool                 # is_superuser OR "admin" role
    allowed_types: set[str]        # RecordType.name по ролям юзера; обновляется соединением
    queue: asyncio.Queue[str | None]  # wire-JSON; None = sentinel "закрой поток"

class EventBus:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None: ...
    def register(self, conn: SseConnection) -> None: ...
    def unregister(self, conn: SseConnection) -> None: ...
    def publish(self, event: Event) -> None: ...           # sync, только из event loop
    def publish_threadsafe(self, event: Event) -> None: ...  # loop.call_soon_threadsafe(self.publish, event)
    def shutdown(self) -> None: ...  # всем очередям put_nowait(None), очистить set
```
`publish`: сериализовать `event.to_wire()` **один раз**, затем для каждого conn: `if _allow(conn, event): conn.queue.put_nowait(frame)`. На `asyncio.QueueFull` — slow consumer: дренировать очередь, `put_nowait(None)`, `unregister(conn)`.

`_allow(conn, event)` — таблица (порядок сверху вниз):

| Событие | Кому |
|---|---|
| `TaskProgressEvent.user_id is not None` | только `conn.user_id == event.user_id` |
| `TaskProgressEvent.user_id is None` (quarto) | только `conn.is_admin` |
| `EntityEvent`, `conn.is_admin` | всё |
| `entity in {"patient","study","series","user"}` | только admin |
| `entity == "record_type"` | всем аутентифицированным |
| `entity == "record"` | `event.record_type_name in conn.allowed_types` **or** `event.user_id == conn.user_id` |

Module-level доступ (в worker-процессе bus никогда не ставится ⇒ всё no-op):
```python
_current_bus: EventBus | None = None
def set_event_bus(bus: EventBus | None) -> None: ...
def get_event_bus() -> EventBus | None: ...
```
Docstring модуля: один uvicorn-процесс (`cli/main.py:249` без `workers=`) — in-process bus достаточен; multi-worker потребует внешний fan-out (RabbitMQ) — вне рамок. **+ строка про лимит 6 соединений HTTP/1.1 → HTTP/2 (см. фазу 6).**

### 2.3 `clarinet/services/events/capture.py`
> Идентично WS-плану (слушатель один). Единственная косметика — `_INFO_KEY = "clarinet_sse_events"`, env-флаг strict `CLARINET_SSE_AUDIT_STRICT`.

Слушатели на **класс** `sqlalchemy.orm.Session` (sync внутри AsyncSession), однократно:
```python
_WATCHED: dict[type, ...] = {Record, Patient, Study, Series, RecordType, User → extractor}
_INFO_KEY = "clarinet_sse_events"
_registered = False

def register_capture_listeners() -> None:  # идемпотентно (module flag)
    event.listen(Session, "after_begin", _on_begin)
    event.listen(Session, "after_flush", _on_flush)
    event.listen(Session, "after_commit", _on_commit)
    event.listen(Session, "after_rollback", _on_rollback)

def emit_entity(entity: str, action: str, ids: Iterable[str]) -> None:
    """Explicit publish for UoW-invisible mutations (Core bulk DML). No-op without bus."""
```
Поведение:
- `_on_begin(session, transaction, connection)`: `if transaction.nested: return`; `session.info.pop(_INFO_KEY, None)` — чистый лист на корневую транзакцию.
- `_on_flush(session, flush_context)`: `session.new`→`created`, `session.deleted`→`deleted`, `session.dirty`→`updated` только при `session.is_modified(obj, include_collections=False)`. **Только column-атрибуты** (relationship ⇒ `MissingGreenlet`): Record → `str(obj.id)`, `obj.record_type_name`, `obj.user_id`; Patient → `obj.id`; Study → `obj.study_uid`; Series → `obj.series_uid`; RecordType → `obj.name`; User → `str(obj.id)`. Копить в `session.info.setdefault(_INFO_KEY, [])`.
- `_on_commit(session)`: целиком в `try/except Exception: logger.warning(...)`. Буфер → **дедуп** по `(entity, id)` (приоритет `deleted > created > updated`), `bus = get_event_bus()`; если не None — `bus.publish(e)`. Буфер очистить **всегда**.
- `_on_rollback(session)`: `session.info.pop(_INFO_KEY, None)`.

Savepoint-контракт (`PatientRepository.create`, `begin_nested`): откат savepoint **не** чистит буфер; дедуп на commit схлопывает повторный flush того же Patient ⇒ ровно одно `patient/created`. Полный rollback чистит. Тест 2.9.

### 2.4 `clarinet/repositories/record_type_repository.py` — набор типов для RBAC
```python
async def get_names_for_roles(self, role_names: set[str]) -> set[str]:
    """RecordType names visible to the given roles (RBAC filter for SSE)."""
    if not role_names:
        return set()
    result = await self.session.execute(
        select(col(RecordType.name)).where(col(RecordType.role_name).in_(list(role_names)))
    )
    return set(result.scalars().all())
```
Имена ролей — `get_user_role_names(user)` (`dependencies.py:446`; roles eagerly loaded после auth).

### 2.5 `clarinet/api/routers/sse.py` — новый роутер
**Ключевое отличие от WS: auth штатной зависимостью `CurrentUserDep`, никакого `authenticate_websocket`.** Ответ — `StreamingResponse(media_type="text/event-stream")`.

```python
import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from clarinet.api.auth_config import DatabaseStrategy
from clarinet.api.dependencies import CurrentUserDep, get_user_role_names
from clarinet.models import User
from clarinet.repositories.record_type_repository import RecordTypeRepository
from clarinet.services.events.bus import SseConnection, get_event_bus
from clarinet.settings import settings
from clarinet.utils.db_manager import db_manager

router = APIRouter()
PING_INTERVAL = 30.0


async def _load_allowed_types(user: User) -> set[str]:
    async with db_manager.get_async_session_context() as session:
        return await RecordTypeRepository(session).get_names_for_roles(get_user_role_names(user))


async def _revalidate(token: str | None) -> bool:
    """Re-check the session token mid-stream with a fresh short-lived session."""
    if not token:
        return False
    async with db_manager.get_async_session_context() as session:
        user = await DatabaseStrategy(session).read_token(token, None)  # type: ignore[arg-type]
    return user is not None


@router.get("/events")
async def events_stream(request: Request, user: CurrentUserDep) -> StreamingResponse:
    """SSE stream of entity/task-progress events. Cookie auth via CurrentUserDep.

    HTTP/1.1 caps a browser at ~6 connections per host; each open stream holds a
    slot for its lifetime. Serve over HTTP/2 (nginx) to lift the cap.
    """
    bus = get_event_bus()
    if bus is None:  # sse_enabled=False or lifespan not initialised
        raise HTTPException(status_code=503, detail="SSE unavailable")
    token = request.cookies.get(settings.cookie_name)
    conn = SseConnection(
        user_id=user.id,
        is_admin=user.is_superuser or "admin" in get_user_role_names(user),
        allowed_types=await _load_allowed_types(user),
        queue=asyncio.Queue(maxsize=settings.sse_send_queue_size),
    )

    async def gen() -> AsyncIterator[str]:
        bus.register(conn)
        yield "retry: 3000\n\n"
        next_ping = monotonic() + PING_INTERVAL
        next_reval = monotonic() + settings.sse_revalidate_seconds
        try:
            while True:
                now = monotonic()
                if now >= next_ping:
                    yield 'data: {"type": "ping"}\n\n'
                    next_ping = now + PING_INTERVAL
                if now >= next_reval:
                    if not await _revalidate(token):
                        yield 'data: {"type": "auth_expired"}\n\n'
                        return
                    next_reval = now + settings.sse_revalidate_seconds
                timeout = max(0.0, min(next_ping, next_reval) - monotonic())
                try:
                    frame = await asyncio.wait_for(conn.queue.get(), timeout=timeout)
                except TimeoutError:
                    continue
                if frame is None:  # slow-consumer sentinel
                    return
                yield f"data: {frame}\n\n"
                if '"record_type"' in frame:  # new types become visible immediately
                    conn.allowed_types = await _load_allowed_types(user)
        finally:
            bus.unregister(conn)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: do not buffer this response
            "Connection": "keep-alive",
        },
    )
```
Факты: `DatabaseStrategy.__init__(session, request=None)` (`auth_config.py:129`); `read_token` (`:174`) проверяет expiry/idle/IP, возвращает User или None, имеет TTL-кэш. `db_manager.get_async_session_context` (`db_manager.py:144-165`, пример — `routers/health.py:42`). `CurrentUserDep` (`dependencies.py:51`). **Disconnect** клиента → Starlette отменяет генератор → `finally` снимает `unregister` (receive-loop из WS-плана не нужен).

### 2.6 `clarinet/api/app.py` — wiring
> Идентично WS-плану с заменой имён `ws`→`sse`.

- Lifespan (`lifespan`, строки 175–453): **перед** `logger.info("Application startup complete")`:
  ```python
  from clarinet.services.events.bus import EventBus, set_event_bus
  from clarinet.services.events.capture import register_capture_listeners

  app.state.event_bus = EventBus(asyncio.get_running_loop())
  set_event_bus(app.state.event_bus)
  register_capture_listeners()
  ```
  В `finally` (перед `await db_manager.close()`):
  ```python
  if getattr(app.state, "event_bus", None) is not None:
      app.state.event_bus.shutdown()
  set_event_bus(None)
  ```
  Слушатели capture остаются зарегистрированными между lifespans (идемпотентный register, bus=None ⇒ no-op) — re-creatable shutdown pattern (`clarinet/CLAUDE.md`).
- `create_app` (include_router, рядом с `health.router`):
  ```python
  if settings.sse_enabled:
      from clarinet.api.routers import sse
      app.include_router(sse.router, prefix="/api", tags=["SSE"])
  ```
  Образец условного монтирования — dicomweb (строки 550–552).

### 2.7 Явные emit для UoW-невидимых мутаций
> Идентично WS-плану; маркер `# sse-capture: explicit emit, UoW-invisible (Core bulk DML)`.

1. **`RecordRepository.delete_records`** (`record_repository.py:1104-1129`, Core `sa_delete`): после `await self.session.commit()` в ветке `commit=True` → `emit_entity("record", "deleted", [str(i) for i in record_ids])`.
2. **`RecordService.delete_record_cascade`** (`record_service.py:566`) коммитит сам → тот же `emit_entity` со списком удалённых id (возврат `tuple[list[int], int]`).
3. **`delete_patient`/`delete_study`** (`study_service.py:382-410`): сначала тест `test_delete_patient_emits_child_deleted` (2.9). Relationships `cascade_delete=True` (`patient.py:99,108`, `study.py:48,49,89`, `record.py:231-234`) — ORM грузит детей в `session.deleted`, capture видит сам. **Тест зелёный → ничего не делать.** Красный → собрать id детей лёгкими select'ами перед `repo.delete(...)` и `emit_entity` после commit.
4. `Record.parent_record_id` `ON DELETE SET NULL` (`record.py:202-206`) — осознанно пропущено (minor), зафиксировать комментарием.
5. Raw SQL `report_repository.py:83` — READ ONLY, событий не требует.

### 2.8 `tests/utils/urls.py`
Добавить: `SSE_URL = "/api/events"`.

### 2.9 Тесты фазы 2

Фикстура (`tests/test_sse_events.py`) — идентична WS-плану (RecordingBus):
```python
class RecordingBus:
    def __init__(self): self.events = []
    def publish(self, event): self.events.append(event)
    def publish_threadsafe(self, event): self.events.append(event)

@pytest.fixture
def sse_bus():
    register_capture_listeners()
    bus = RecordingBus()
    set_event_bus(bus)  # type: ignore[arg-type]
    yield bus
    set_event_bus(None)
```

| Тест | Файл | Суть |
|---|---|---|
| `test_record_insert_emits_created` | `tests/test_sse_events.py` | `seed_record` → `record/created` с верными `id`(str), `record_type_name`, `user_id` |
| `test_record_update_emits_updated` | — | `repo.update_fields(id, {"context_info": "x"})` → `record/updated` |
| `test_rollback_emits_nothing` | — | add + flush + rollback → `bus.events == []` |
| `test_savepoint_retry_emits_single_patient_created` | — | занять следующий `auto_id` прямой вставкой, затем `PatientRepository.create()` → ровно одно `patient/created` |
| `test_delete_records_bulk_emits_deleted` | — | `delete_records([ids])` → `record/deleted` по каждому id |
| `test_delete_patient_emits_child_deleted` | — | патиент+study+series+record; `StudyService.delete_patient` → `deleted` для всех (развилка 2.7.3) |
| `test_dedup_created_then_updated_single_event` | — | create + update в одной транзакции → одно `created` |
| `test_allow_rbac_matrix` | `tests/test_sse_bus.py` | unit на `_allow`: admin всё; обычный — своё `user_id` и свой тип; чужой тип отфильтрован; `patient/*` только админу; `record_type` всем; `TaskProgress(user_id=X)` только X |
| `test_slow_consumer_gets_sentinel` | — | queue maxsize=2, publish×3 → очередь содержит `None`, conn выписан |
| `test_sse_rejects_without_cookie` | `tests/integration/test_sse_endpoint.py` | `TestClient(app).get(SSE_URL)` **без cookie → `status_code == 401`** (обычный HTTP, не WS-handshake) |
| `test_sse_handshake_with_cookie` | — | с валидной cookie: `with client.stream("GET", SSE_URL) as r: assert r.status_code == 200; assert "text/event-stream" in r.headers["content-type"]` — **тело не итерировать** (блокирует), сразу выйти |

Интеграционный happy-path (`test_sse_receives_record_event`, опционально): SSE — обычный HTTP-стрим, поэтому проще WS. Рецепт как в `tests/test_app_startup.py`: file-based SQLite через monkeypatch `CLARINET_DATABASE_URL` + `with TestClient(app) as tc:` (lifespan: таблицы + admin), `tc.post("/api/auth/login", ...)`, затем **в фоновом потоке** мутация `tc.post(...)`, а в основном — `with tc.stream("GET", SSE_URL) as r:` читать `r.iter_lines()` до первого `data: {entity}` (пропуская `retry:`/`ping`). Если упрётся в инфраструктуру (блокирующий стрим + cross-loop) более ~часа — оставить только 401/handshake-тесты, happy-path → ручная проверка фазы 3.

### Проверка фазы 2
```bash
./scripts/run_tests.sh -k "sse" -q > /tmp/test-sse-p2.txt 2>&1
make check
timeout 300 make test-unit > /tmp/test-sse-p2-unit.txt 2>&1
```
Коммит: `feat(sse): event bus, UoW capture, cookie-auth /api/events stream`

---

## Фаза 3 — фронтенд-ядро

Все пути — от `clarinet/frontend/`. Контракты модулей: `.claude/rules/frontend-page-contract.md`. Таймеры — `plinth/javascript/global` (`set_timeout/clear_timeout`, `TimerID`).

### 3.1 `src/utils/event_source.gleam` + `src/utils/event_source.ffi.mjs`
Низкоуровневый транспорт. Паттерн FFI — как `utils/viewer_window.gleam`(:7-17); паттерн effect — как modem (`build/packages/modem/src/modem.gleam:90-98`).

```gleam
// event_source.gleam
import gleam/bool
import lustre
import lustre/effect.{type Effect}

pub type EventSource

pub type Event {
  Opened(EventSource)
  MessageReceived(String)
  Errored(ready_state: Int)  // 0 = CONNECTING (auto-reconnecting), 2 = CLOSED (permanent)
}

pub fn connect(path: String, to_msg: fn(Event) -> msg) -> Effect(msg) {
  use dispatch <- effect.from
  use <- bool.guard(!lustre.is_browser(), Nil)
  do_connect(
    path,
    fn(es) { dispatch(to_msg(Opened(es))) },
    fn(text) { dispatch(to_msg(MessageReceived(text))) },
    fn(state) { dispatch(to_msg(Errored(state))) },
  )
}

pub fn close(source: EventSource) -> Effect(msg) {
  use _dispatch <- effect.from
  do_close(source)
}

@external(javascript, "./event_source.ffi.mjs", "connect")
fn do_connect(path: String, on_open: fn(EventSource) -> Nil, on_message: fn(String) -> Nil, on_error: fn(Int) -> Nil) -> Nil

@external(javascript, "./event_source.ffi.mjs", "closeSource")
fn do_close(source: EventSource) -> Nil
```
```js
// event_source.ffi.mjs
export function connect(path, onOpen, onMessage, onError) {
  const url = new URL(path, window.location.href);
  const es = new EventSource(url, { withCredentials: true });
  es.onopen = () => onOpen(es);
  es.onmessage = (e) => onMessage(e.data);           // only unnamed data: frames; comments/retry handled internally
  es.onerror = () => onError(es.readyState);          // 0 CONNECTING, 2 CLOSED
}

export function closeSource(es) {
  es.onerror = null;  // deliberate close: don't surface Errored (иначе reconnect-логика дёрнется)
  try { es.close(); } catch (_) {}
}
```
Бинарных кадров у SSE нет; `send`/close-by-code не реализуем (нет клиент→сервер канала). Один `connect`-эффект диспатчит многократно. **Нативный reconnect**: `EventSource` сам переподключается после разрыва, интервал = серверный `retry`; ручного backoff нет.

### 3.2 `src/api/sse_events.gleam` — декодер wire-формата
> Идентичен `ws_events.gleam` из WS-плана (JSON тот же).

```gleam
pub type Action { Created Updated Deleted }

pub type EntityEvent {
  EntityEvent(entity: String, action: Action, id: String,
    record_type_name: Option(String), user_id: Option(String))
}

pub type SseEvent {
  Entity(EntityEvent)
  TaskProgress(task: String, task_id: String, payload: dynamic.Dynamic)
  AuthExpired
  Ping
}

pub fn decode_frame(text: String) -> Result(SseEvent, Nil)
```
Реализация: `json.parse(text, frame_decoder())`; `decode.field("type", decode.string)` + `case`-ветвление: `"entity"` → поля (`record_type_name`/`user_id` через `decode.optional_field(key, None, decode.optional(decode.string))`), `"task_progress"` → `payload` как `decode.dynamic`, `"auth_expired"`/`"ping"` → константы; неизвестный type → `decode.failure(Ping, "SseEvent")`. Action: `"created"|"updated"|"deleted"`, иначе failure.

### 3.3 `src/sse.gleam` — координатор соединения
**Проще ws.gleam — нативный reconnect.** Нет `attempt`, `reconnect_timer`, `ReconnectTick`, `backoff_ms`.
```gleam
pub type State { Idle Connecting Active(event_source.EventSource) }

pub type Model {
  Model(state: State, has_connected_once: Bool, watchdog: Option(global.TimerID))
}

pub type Msg {
  Connect
  Event(event_source.Event)
  WatchdogTick
  Stop
}

pub type OutMsg {
  SseConnected(reconnected: Bool)
  SseEntityEvent(sse_events.EntityEvent)
  SseTaskProgress(task: String, task_id: String, payload: dynamic.Dynamic)
  SseAuthExpired
}

pub fn init() -> Model  // Idle, has_connected_once False, watchdog None
pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg), List(OutMsg))
```
Переходы:

| Msg | Условие | Действие |
|---|---|---|
| `Connect` | state == Idle | → Connecting; effect `event_source.connect(config.base_path() <> "/api/events", Event)` |
| `Event(Opened(es))` | — | → `Active(es)`; OutMsg `[SseConnected(reconnected: model.has_connected_once)]`; `has_connected_once = True`; arm watchdog (`set_timeout(90_000, WatchdogTick)`) |
| `Event(MessageReceived(text))` | — | re-arm watchdog (clear + set 90s); `decode_frame`: `Entity(e)` → `[SseEntityEvent(e)]`; `TaskProgress(..)` → `[SseTaskProgress(..)]`; `AuthExpired` → clear watchdog + `close(es)` → Idle + `[SseAuthExpired]`; `Ping`/`Error` → `[]` (ошибку декода — `logger.warn`) |
| `Event(Errored(2))` | — | CLOSED (401/503/404): clear watchdog → Idle; `[]` (**без reconnect, без Logout** — см. отступление #8) |
| `Event(Errored(_))` | — | CONNECTING (браузер реконнектится сам): → Connecting; `[]` |
| `WatchdogTick` | state == Active | зомби: `close(es)` → Idle; effect `dispatch(Connect)` (ручной reconnect новым источником) |
| `Stop` | — | clear watchdog; если Active — `close(es)`; → `init()` |

Нет ручного backoff: транзиентные разрывы лечит сам `EventSource` (серверный `retry: 3000`); ручной reconnect — только из watchdog.

### 3.4 `src/store.gleam`
- `Model`: добавить `sse: sse.Model` и `sse_enabled: Bool`.
- `init()`: `sse: sse.init(), sse_enabled: False`.
- `Msg`: добавить `SseMsg(sse.Msg)` рядом с `PreloadMsg`.
- `reset_for_logout`: строится от `init()` — sse сбросится сам; **сохранить** `sse_enabled: model.sse_enabled`.

### 3.5 `src/main.gleam`
1. Хелпер:
   ```gleam
   fn ensure_sse(model: store.Model) -> Effect(store.Msg) {
     case model.user, model.sse_enabled, model.sse.state {
       Some(_), True, sse.Idle -> dispatch_effect(store.SseMsg(sse.Connect))
       _, _, _ -> effect.none()
     }
   }
   ```
2. Точки вызова `ensure_sse` (батчить к существующим эффектам):
   - `store.CheckSessionResult(Ok(user))`;
   - `store.ProjectInfoLoaded(Ok(info))` — сюда же `sse_enabled: info.sse_enabled` в Model;
   - `apply_out_msgs` ветка `shared.SetUser(user)`.
3. Делегирование `store.SseMsg(m)`: `sse.update(model.sse, m)` → записать model, `effect.map(_, store.SseMsg)`, транслировать OutMsg:

   | sse.OutMsg | Действие main |
   |---|---|
   | `SseConnected(False)` | ничего (страница только что загружена) |
   | `SseConnected(True)` | resync: `cache.InvalidateAllRecordBucketsMsg` + `cache.InvalidateFilterOptions` + `init_page_for_route(model, model.route)` |
   | `SseEntityEvent(e)` | `store.CacheMsg(cache.SseEntityEvent(e))` |
   | `SseTaskProgress("preload", id, payload)` | `store.PreloadMsg(preload.ProgressPush(id, payload))` |
   | `SseTaskProgress("quarto_render", _, payload)` | если открыта `AdminQuartoReportsPage` → `quarto_reports.RenderPushed(payload)`; иначе игнор |
   | `SseAuthExpired` | `store.Logout`, **только если** `model.user != None` |
4. Ветка `store.Logout`: добавить в batch эффект `dispatch store.SseMsg(sse.Stop)`.

### 3.6 `src/cache.gleam` — событийный кэш
> **Идентичен WS-плану**; переименованы Msg-армы `WsEntityEvent`→`SseEntityEvent`, `WsRecordRefetched`→`SseRecordRefetched`.

- `Model`: добавить `sse_debounce: Option(global.TimerID)`; обновить `cache.init()`.
- `Msg`: добавить:
  ```gleam
  SseEntityEvent(event: sse_events.EntityEvent)
  SseRecordRefetched(id: String, result: Result(Record, ApiError))
  RefetchStaleBuckets
  ```
- `SseEntityEvent(e)` по `e.entity`/`e.action`:

  | entity | действие |
  |---|---|
  | `"record"`, ≠ Deleted | если `dict.has_key(model.records, e.id)` → effect `records.get_record(e.id)` → `SseRecordRefetched(e.id, result)`. **Не** `LoadRecordDetail` (у него auto-assign side effects). Всегда: пометить бакеты + дебаунс |
  | `"record"`, Deleted | `dict.delete(model.records, e.id)`; убрать из items всех бакетов; пометить + дебаунс |
  | `"patient"` | Deleted → `dict.delete`; иначе если в dict → `LoadPatientDetail(e.id)` |
  | `"study"` | Deleted → `dict.delete`; иначе если dict непуст → `LoadStudies` |
  | `"series"` | Deleted → `dict.delete`; иначе если в dict → `LoadSeriesDetail(e.id)` |
  | `"record_type"` | Deleted → `dict.delete`; иначе если dict непуст → `LoadRecordTypes`; всегда → `InvalidateFilterOptions` + пометить + дебаунс |
  | `"user"` | если dict непуст → `LoadUsers` |

- «Пометить бакеты + дебаунс»: все бакеты `Live(_)`/`LoadingMore(_)` → `bucket.mark_stale` (`cache/bucket.gleam:83-89`); если `sse_debounce == None` → `set_timeout(750, RefetchStaleBuckets)`.
- `RefetchStaleBuckets`: `sse_debounce = None`; для каждого `Stale(loaded_at)`: `now - loaded_at > 600_000` → удалить из dict; иначе **тихий refetch** (тот же fetch-эффект, что `FetchBucketMsg(key)`, `cursor=None`, результат в `BucketLoaded(key, _)`, но **без** статуса `Loading` — stale-while-revalidate). Вынести `fetch_bucket_effect(key)`. Хвостом: `InvalidateFilterOptions`-refetch + `LoadRecordTypeStats`, если `Some`.
- `SseRecordRefetched(id, Ok(record))`: `put_record` + `upsert_record_in_buckets`.
- `SseRecordRefetched(id, Error(_))` (403/404): `dict.delete(model.records, id)` + убрать из items бакетов.
- TTL 60s и LRU cap не трогать (страховка); `upsert_record_in_buckets` — для локальных мутаций.

### 3.7 Тесты фазы 3 (`test/`, образец — `test/preload_test.gleam`)
- `test/sse_events_test.gleam`: декодер — кадр record с user_id, entity без опциональных полей, task_progress, auth_expired, ping, мусор → Error.
- `test/sse_test.gleam`: переходы `sse.update` — `Opened` даёт `SseConnected(reconnected)` корректно (has_connected_once False→первый, True→reconnect); `MessageReceived(auth_expired)` → `[SseAuthExpired]` + Idle; `Errored(2)` → Idle без OutMsg; `Errored(0)` → Connecting; `WatchdogTick` из Active реконнектит; `Stop` из Active.
- `test/cache_sse_test.gleam`: `SseEntityEvent(record deleted)` удаляет из records и bucket items; mark_stale только Live/LoadingMore; `SseRecordRefetched(Error)` удаляет запись.

### Проверка фазы 3
```bash
make frontend-check
cd clarinet/frontend && gleam test > /tmp/test-sse-p3.txt 2>&1
make frontend-build
```
Ручная: `make run-dev`, два окна → смена статуса видна без перезагрузки (≤1–2 с, после дебаунса); рестарт API → `EventSource` сам переподключается (Network: новый event-stream коннект ~через 3 с) и список обновляется; logout → поток закрыт, реконнектов нет.
Коммит: `feat(frontend): SSE transport, event-driven cache invalidation`

---

## Фаза 4 — прогресс задач (preload + quarto)

> Бэкенд-publish и фронт-consume **идентичны WS-плану** (bus один и тот же). Ниже — те же шаги.

### 4.1 Бэкенд: preload
`clarinet/services/dicomweb/service.py`:
- `start_preload` (строки 286–298): сигнатура → `async def start_preload(self, study_uids: list[str], user_id: UUID | None = None) -> str`; сохранить `self._preload_owners[task_id] = user_id` (новый dict; чистить в `_preload_worker` через `pop`).
- Роутер `clarinet/api/routers/dicomweb.py:224-235`: `_user: CurrentUserDep` → `user: CurrentUserDep`; `service.start_preload(body.study_uids, user.id)`.
- `_preload_worker` (строки 300–349): локальный хелпер `_publish` с throttle (не чаще 1 кадра / 500 мс, терминальные — всегда) → `bus.publish_threadsafe(TaskProgressEvent(task="preload", task_id=task_id, payload=dict(progress), user_id=user_id))`. Вызывать после каждого `progress.update(...)` (throttled), финальные `ready`/`error` — `force=True`. `publish_threadsafe` обязателен: `on_progress` дёргается из потоков pynetdicom.

### 4.2 Frontend: `src/preload.gleam`
- `Model`: + `last_push_ms: Int` (0 в `init`).
- `Msg`: + `ProgressPush(task_id: String, payload: dynamic.Dynamic)`.
- `ProgressPush`: если `task_id` совпадает с активным — `last_push_ms = now`, дальше тот же код, что `ProgressUpdate(task_id, Ok(payload))` (вынести общий обработчик).
- `PollTick(task_id)` (строка 179): первой строкой guard `now - model.last_push_ms < 2500` → пропустить fetch. Push жив → HTTP-поллинг молчит; поток упал → поллинг возобновляется.
- `now` в ms — тот же helper, что bucket `loaded_at_ms`.

### 4.3 Бэкенд: quarto
`clarinet/services/quarto_render.py::write_status` (строки 69–95): после записи файла:
```python
bus = get_event_bus()
if bus is not None:
    bus.publish_threadsafe(TaskProgressEvent(
        task="quarto_render", task_id=render_id, payload=payload))  # user_id=None -> admins only
```
`write_status` всегда через `asyncio.to_thread` (call-sites: `quarto_report_service.py:130,151`, `quarto_render.py:150,172,184,198`) → `publish_threadsafe`. В TaskIQ-воркере bus отсутствует → no-op; пуш работает для PENDING/dispatch-FAILED и для всего цикла в in-process режиме. Комментарий об ограничении — у publish.

### 4.4 Frontend: `src/pages/admin/quarto_reports.gleam`
- `Msg`: + `RenderPushed(payload: dynamic.Dynamic)`.
- Декодировать `render_id`/`status`/`error` (декодер есть, строки 216–225) → обновить `RenderEntry` тем же кодом, что `RenderPolled` (вынести общую функцию). Неизвестный `render_id` → игнор.
- Поллинг-цепочку (строки 151–214, интервал 3000, cap 200) **не менять** — push лишь ускоряет.

### Проверка фазы 4
```bash
./scripts/run_tests.sh -k "preload or quarto or sse" -q > /tmp/test-sse-p4.txt 2>&1
make check && make frontend-check && make frontend-build
```
Ручная: preload OHIF — прогресс растёт без запросов к `/preload/progress/` (Network); обрыв (kill+restart API) — прогресс продолжает поллингом. Quarto (`pipeline_enabled=False`): статус без поллинг-задержки 3 с.
Коммит: `feat(sse): push task progress for dicomweb preload and quarto renders`

---

## Фаза 5 — audit-обогащение (ЗАБЛОКИРОВАНА до мержа PR #330/#332)

> Идентично WS-плану (capture транспортно-независим). Единственная замена — env-флаг `CLARINET_SSE_AUDIT_STRICT`.

Перед стартом: убедиться, что в main есть `clarinet/models/record_event.py` (#332) и `clarinet/models/pipeline_task_run.py` (#330); ребейзнуть. **Сверить поля моделей с кодом.**

Снапшот (2026-06-12): `RecordEvent`: `record_id: int|None` (FK SET NULL), `record_key: int|None` (денормализованный id), `kind` (`created|status_changed|data_submitted|data_updated|assigned|unassigned|failed|invalidated|context_info_updated|files_cleared|deleted`), `actor_id: UUID|None`, `from_status/to_status`, `old_value/new_value`, `occurred_at`. Пишется `RecordService` (та же транзакция). `PipelineTaskRun`: PK `id` = TaskIQ task_id, `task_name`, `queue`, `record_id`, `status` (`running|succeeded|failed|retrying`), `result`. Пишется `AuditMiddleware` через HTTP API (коммиты в API-процессе ⇒ UoW-capture видит).

Работы в `capture.py`:
1. **Audit-обогащение record-событий.** В `_on_flush` ловить вставки `RecordEvent` в `session.new` → отдельный список `audit_events` (`record_key`, `kind`, `actor_id`). В `_on_commit`: маппинг `kind → action`, `actor_id → user_id`; дедуп по `(entity="record", id)`: audit есть → uow-дубль выбрасывается. У audit-события нет `record_type_name` — брать из uow-пары по `record_key`; нет пары (cascade-delete со снапшотом) → из `old_value`-снапшота или `None` (фильтр пропустит только владельцу/админам — деградация задокументирована).
2. **`task_progress` для pipeline-задач.** `PipelineTaskRun` в `session.new`/`dirty` → `TaskProgressEvent(task="pipeline", ...)` админам. Расширить `Literal` в `models.py` и протокол. Фронт-консьюмер — отдельной задачей.
3. **Детектор рассинхрона.** uow-событие `record` без audit-пары и с аудируемой колонкой (`status/user_id/data/context_info`/создание/удаление; `started_at/finished_at/checksum/anon_*` не считаются — изменённые колонки через `inspect(obj).attrs[col].history`): прод — `logger.warning` в try/except; тесты — `CLARINET_SSE_AUDIT_STRICT=1` копит `orphan_events`, фикстура ассертит пустоту.
4. **Тесты** (`tests/test_sse_audit.py`): A (fallback) — мутация аудируемой колонки мимо RecordService (`repo.update_fields`) → record-событие приходит; B (детектор) → WARNING/strict непуст; C (дедуп) — через RecordService → одно событие с `user_id=actor_id`, без дубля.

Коммит: `feat(sse): audit-enriched record events, pipeline task progress, drift detector`

---

## Фаза 6 — nginx (за обратным прокси)

`deploy/nginx/clarinet.conf` — один `server` на `listen 443 ssl` с единственным `location __PATH_PREFIX__` (proxy на uvicorn). Изменения:

### 6.1 HTTP/2 — снимает лимит 6 вкладок
В `server { listen 443 ssl default_server; ... }` (строка 11) добавить:
```nginx
    http2 on;          # nginx >= 1.25.1; для старых: listen 443 ssl http2 default_server;
```
HTTP/2 мультиплексирует ~100 потоков в одном TCP-соединении → лимит «~6 соединений на хост» (которым каждый открытый SSE занимает слот навсегда) исчезает. Уже на TLS (cookie `Secure` в проде) — изменение однострочное. **Без HTTP/2: 7-я+ вкладка с открытым SSE виснет.**

### 6.2 Буферизация — закрыта заголовком приложения
SSE-эндпоинт уже ставит `X-Accel-Buffering: no` (фаза 2.5) → nginx не буферизует этот ответ даже при глобальном `proxy_buffering on`. **Доп. правок конфига не требуется** (вложенный `location` для `/api/events` с плейсхолдером `__PATH_PREFIX__` хрупок — не делаем).

### 6.3 Что НЕ трогаем
- `proxy_read_timeout 300s` (строка 36) — ping раз в 30 с держит поток непустым, идл не наступает.
- `Connection "upgrade"` (строка 33) — WS-специфично, для SSE безвредно. Оставить (WS-совместимость) либо сменить на `Connection ''`, если WS не планируется.

### Проверка фазы 6
- Открыть 7+ вкладок приложения: на HTTP/1.1 7-я виснет (лимит), после `http2 on;` — все живут.
- `curl -N -H "Cookie: clarinet_session=<...>" https://<host>__PATH_PREFIX__api/events` → кадры идут сразу, без буферизации (видно `data: {"type":"ping"}` через ≤30 с).
Коммит: `feat(deploy): enable http2 for SSE multiplexing`

---

## Сквозная верификация (после фаз 2–4)

1. `./scripts/run_tests.sh -k "sse" -q > /tmp/test-sse-final.txt 2>&1`.
2. `make check` (после — `Read` файлов заново перед `Edit`: ruff format мог переписать).
3. `timeout 300 make test-unit > /tmp/test-sse-unit.txt 2>&1`.
4. `make frontend-check && (cd clarinet/frontend && gleam test) && make frontend-build`.
5. Ручной сценарий фаз 3–4 (два окна, рестарт сервера, preload, quarto).
6. nginx: HTTP/2 + curl-проверка стрима (фаза 6).
7. E2E (Playwright, два контекста) в `deploy/test/e2e/` — опционально, отдельным PR-куском.

## Жёсткие запреты (нарушение = баг)

- В `after_flush` — никаких relationship-доступов (`MissingGreenlet`), только column-атрибуты.
- Не держать `AsyncSession` на время жизни SSE-потока — только короткоживущие `db_manager.get_async_session_context()` на connect/ревалидацию/refresh типов.
- `_on_commit` не должен ронять бизнес-операцию — всё тело в try/except.
- Не добавлять полей данных в `EntityEvent` — события «тонкие», клиент дотягивает REST'ом под своим RBAC.
- `asyncio.gather` с запросами на одной shared-сессии запрещён (`clarinet/CLAUDE.md`).
- Новый bulk/каскадный путь мутации без `emit_entity` + маркера `# sse-capture: ...` — регресс.
- В генераторе SSE — `yield`-ить только строки с `\n\n` на конце; keepalive — `data:`-кадр `ping`, НЕ SSE-комментарий (комментарий до `onmessage`/watchdog не доходит).
- `Errored(2)` (CLOSED) не диспатчит Logout — только `auth_expired`-кадр (CLOSED не отличает 401 от 503/404).
