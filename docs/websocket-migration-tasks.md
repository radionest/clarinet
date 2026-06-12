# WebSocket migration — детальный план реализации

Исполняемая декомпозиция [websocket-migration-plan.md](websocket-migration-plan.md) (там — обоснования решений; здесь — только действия). Все `file:line` сверены с `main@bae1d23` (2026-06-12). Если код сместился — ориентируйся на имена функций, а не номера строк.

## Правила выполнения

1. Работать в worktree от **main** (`EnterWorktree`), не от ветки этого документа.
2. Фазы 1→4 — строго по порядку, **один коммит на фазу** (conventional commits, English). Фаза 5 заблокирована до мержа PR #330/#332 — перед началом проверь `git log --oneline main | grep -i audit` или наличие `clarinet/models/record_event.py`.
3. После каждой фазы — блок «Проверка фазы» обязателен. Тесты гонять один раз с выводом в файл: `./scripts/run_tests.sh -k "ws" -q > /tmp/test-ws.txt 2>&1`.
4. Wire-протокол ниже — единственный источник истины. Не менять формат кадров без правки этого раздела.
5. Новые публичные функции — короткий docstring (английский, "why" + gotchas). Никаких `print` — `from clarinet.utils.logger import logger`.

## Wire-протокол (server→client, текстовый JSON; v1: клиент ничего не шлёт)

```json
{"type": "entity", "entity": "record", "action": "created", "id": "123", "record_type_name": "ct_seg", "user_id": "<uuid>|null"}
{"type": "entity", "entity": "patient|study|series|record_type|user", "action": "created|updated|deleted", "id": "<string>"}
{"type": "task_progress", "task": "preload", "task_id": "preload_ab12...", "payload": {"status": "fetching", "received": 10, ...}}
{"type": "task_progress", "task": "quarto_render", "task_id": "<render_id>", "payload": {"name": ..., "render_id": ..., "status": ..., "ready": {...}, "error": ...}}
{"type": "auth_expired"}
{"type": "ping"}
```

- `id` — **всегда строка** (`Record.id: int` сериализуется `str(id)`); ключи кэша фронта уже строковые.
- `record_type_name`/`user_id` присутствуют только у `entity=record`.
- `payload` у `task_progress` — байт-в-байт тот же dict, что отдаёт соответствующий polling-эндпоинт.
- Закрытие: `4401` — auth (клиент делает Logout, без reconnect); `4408` — slow consumer (клиент реконнектится); прочие коды — reconnect с backoff.

## Осознанные отступления от websocket-migration-plan.md

| # | Что | Почему |
|---|---|---|
| 1 | `accept()` **до** проверки auth; при провале `close(code=4401)` | close-код недоставим до accept: отказ на handshake браузер видит как 1006 и уходит в бесконечный reconnect-цикл |
| 2 | Quarto: поллинг страницы **остаётся без изменений**, push — opportunistic-ускорение | при `pipeline_enabled=True` рендер пишет `status.json` из TaskIQ-воркера (другой процесс) — in-process bus его не видит (`quarto_report_service.py:215-285`) |
| 3 | Preload-fallback: поллинг-таймер не выключается, а **пропускает tick**, если последний push свежее интервала | убирает двустороннюю координацию main↔preload; запросы исчезают при живом WS и сами возобновляются при разрыве |
| 4 | Explicit emit для `delete_patient`/`delete_study` — **только если тест покажет, что UoW не видит детей** | `cascade_delete=True` на relationships (ORM-каскад, не passive) загружает детей при flush → они попадают в `session.deleted`; см. шаг 2.8 |
| 5 | `send()` в `utils/websocket.gleam` не реализуем | v1 клиент ничего не шлёт (YAGNI) |
| 6 | RBAC-набор типов — новый лёгкий запрос имён по ролям, не `get_available_type_counts` | тот метод считает только `pending`-записи (`record_repository.py:1702-1733`) — тип без pending-записей выпал бы из фильтра |
| 7 | Resync (`InvalidateAll` + re-init страницы) — только при **повторном** подключении | при первом connect после логина страница только что инициализирована; re-init дал бы двойную загрузку и потерю ввода |

---

## Фаза 1 — зависимости и настройки

### 1.1 `pyproject.toml`
Добавить в `[project] dependencies` рядом с `"uvicorn>=0.21.1",` (строка ~22):
```toml
"websockets>=12.0",
```
uvicorn подхватит ws-протокол автоматически, `cli/main.py:249` (`uvicorn.run`) не трогать. У websockets есть типы — mypy-override не нужен. После правки: `uv sync`.

### 1.2 `clarinet/settings.py`
В класс `Settings` (строка 90, env-префикс `CLARINET_`), после session-блока (после `session_cache_ttl_seconds`, строка ~286):
```python
# WebSocket push (single-process in-memory bus; see services/events/bus.py)
ws_enabled: bool = True
ws_revalidate_seconds: int = 300  # session re-check interval per connection
ws_send_queue_size: int = 256  # per-connection send queue; overflow -> close 4408
```

### 1.3 `clarinet/api/routers/info.py`
В dict ответа `get_project_info` (строки 18–22) добавить ключ:
```python
"ws_enabled": settings.ws_enabled,
```

### 1.4 Frontend: `src/api/info.gleam`
- В тип `ProjectInfo` (строки 12–18) добавить поле `ws_enabled: Bool` (последним).
- В `project_info_decoder()` (строки 26–31): `use ws_enabled <- decode.optional_field("ws_enabled", False, decode.bool)` — **default `False`** (старый бэкенд без поля ⇒ не подключаемся). Помни: default — **второй** аргумент.

### Проверка фазы 1
```bash
make check
uv run pytest tests/ -k "info" -q > /tmp/test-ws-p1.txt 2>&1
make frontend-check
```
Коммит: `feat(ws): add websockets dep, ws_* settings, ws_enabled in /api/info`

---

## Фаза 2 — бэкенд: events-модуль, auth, роутер, capture

Новый пакет `clarinet/services/events/` — четыре файла: `__init__.py`, `models.py`, `bus.py`, `capture.py`. Модуль ни от чего внутри clarinet не зависит, кроме `models`, `settings`, `utils.logger` — его можно импортировать из репозиториев и сервисов без циклов.

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
`type Event = EntityEvent | TaskProgressEvent` (PEP 695 alias).

### 2.2 `clarinet/services/events/bus.py`
```python
@dataclass
class WsConnection:
    user_id: UUID
    is_admin: bool                 # is_superuser OR "admin" role
    allowed_types: set[str]        # RecordType.name по ролям юзера; обновляется соединением
    queue: asyncio.Queue[str | None]  # wire-кадры; None = sentinel "закрой соединение"

class EventBus:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None: ...
    def register(self, conn: WsConnection) -> None: ...
    def unregister(self, conn: WsConnection) -> None: ...
    def publish(self, event: Event) -> None: ...           # sync, только из event loop
    def publish_threadsafe(self, event: Event) -> None: ...  # loop.call_soon_threadsafe(self.publish, event)
    def shutdown(self) -> None: ...  # всем очередям put_nowait(None), очистить set
```
`publish`: сериализовать `event.to_wire()` **один раз**, затем для каждого conn: `if _allow(conn, event): conn.queue.put_nowait(frame)`. На `asyncio.QueueFull` — slow consumer: дренировать очередь (`while not q.empty(): q.get_nowait()`), `put_nowait(None)`, `unregister(conn)`.

`_allow(conn, event)` — таблица (порядок проверок сверху вниз):

| Событие | Кому |
|---|---|
| `TaskProgressEvent.user_id is not None` | только `conn.user_id == event.user_id` |
| `TaskProgressEvent.user_id is None` (quarto) | только `conn.is_admin` |
| `EntityEvent`, `conn.is_admin` | всё |
| `entity in {"patient","study","series","user"}` | только admin (роутеры этих сущностей admin-only — `study.py` router-level `current_admin_user`) |
| `entity == "record_type"` | всем аутентифицированным |
| `entity == "record"` | `event.record_type_name in conn.allowed_types` **or** `event.user_id == conn.user_id` |

Module-level доступ для repos/сервисов и воркеров (в worker-процессе bus никогда не ставится ⇒ всё no-op):
```python
_current_bus: EventBus | None = None
def set_event_bus(bus: EventBus | None) -> None: ...
def get_event_bus() -> EventBus | None: ...
```
Docstring модуля: один uvicorn-процесс (`cli/main.py:249` без `workers=`) — in-process bus достаточен; multi-worker потребует внешний fan-out (RabbitMQ) — вне рамок.

### 2.3 `clarinet/services/events/capture.py`
Перехват ORM-мутаций. Слушатели вешаются на **класс** `sqlalchemy.orm.Session` (sync-сессия внутри AsyncSession) — однократно:

```python
_WATCHED: dict[type, Callable[[Any], EntityEvent_fields]] = {Record, Patient, Study, Series, RecordType, User → extractor}
_INFO_KEY = "clarinet_ws_events"
_registered = False

def register_capture_listeners() -> None:  # идемпотентно (module flag)
    event.listen(Session, "after_begin", _on_begin)
    event.listen(Session, "after_flush", _on_flush)
    event.listen(Session, "after_commit", _on_commit)
    event.listen(Session, "after_rollback", _on_rollback)

def emit_entity(entity: str, action: str, ids: Iterable[str]) -> None:
    """Explicit publish for UoW-invisible mutations (Core bulk DML). No-op without bus."""
```

Поведение слушателей:
- `_on_begin(session, transaction, connection)`: `if transaction.nested: return`; `session.info.pop(_INFO_KEY, None)` — чистый лист на новую корневую транзакцию (страхует от утечки событий после исключения между flush и commit).
- `_on_flush(session, flush_context)`: для `session.new` → `created`, `session.deleted` → `deleted`, `session.dirty` → `updated` только если `session.is_modified(obj, include_collections=False)`. Для каждого наблюдаемого объекта извлечь **только column-атрибуты** (обращение к relationship ⇒ `MissingGreenlet` — запрещено): Record → `str(obj.id)`, `obj.record_type_name`, `obj.user_id`; Patient → `obj.id`; Study → `obj.study_uid`; Series → `obj.series_uid`; RecordType → `obj.name`; User → `str(obj.id)`. Накапливать в `session.info.setdefault(_INFO_KEY, [])`.
- `_on_commit(session)`: целиком в `try/except Exception: logger.warning(...)` — COMMIT уже состоялся, сбой шины не должен всплыть. Взять буфер, **дедуп** по `(entity, id)` с приоритетом `deleted > created > updated`, `bus = get_event_bus()`; если bus не None — `bus.publish(e)` для каждого. Буфер очистить **всегда** (`session.info.pop`).
- `_on_rollback(session)`: `session.info.pop(_INFO_KEY, None)`.

Savepoint-контракт (`PatientRepository.create`, `patient_repository.py:33-59`, `begin_nested` на строке 42): откат savepoint **не** чистит буфер (нет такого события), но дедуп по `(entity, id)` на commit схлопывает повторный flush того же Patient ⇒ ровно одно `patient/created`. Полный rollback / новая транзакция чистят буфер. Закрепить тестом (см. 2.10).

### 2.4 `clarinet/api/auth_config.py` — `authenticate_websocket`
Добавить в конец файла (рядом с `get_database_strategy`, строки 410–415):
```python
async def authenticate_websocket(websocket: WebSocket) -> tuple[User, str] | None:
    """Validate the session cookie of a WS handshake. Returns (user, token) or None.

    Opens a short-lived session only for validation — never hold an AsyncSession
    for the lifetime of the connection.
    """
    token = websocket.cookies.get(settings.cookie_name)
    if not token:
        return None
    async with db_manager.get_async_session_context() as session:
        strategy = DatabaseStrategy(session)
        user = await strategy.read_token(token, None)  # type: ignore[arg-type]  # user_manager unused (deleted at line ~178)
    return (user, token) if user is not None else None
```
Факты: `DatabaseStrategy` — строка 110, конструктор принимает session (129–132); `read_token` (строка 174) сам проверяет expiry/idle/IP и возвращает User с eagerly-loaded ролями или None; его TTL-кэш (`TTLCache`, строки 113–116) переиспользуется. `db_manager.get_async_session_context` — `clarinet/utils/db_manager.py:144-165` (пример использования: `routers/health.py:42`). Token вернуть наружу — нужен для периодической ревалидации.

### 2.5 `clarinet/repositories/record_type_repository.py` — набор типов для RBAC
```python
async def get_names_for_roles(self, role_names: set[str]) -> set[str]:
    """RecordType names visible to the given roles (RBAC filter for WS)."""
    if not role_names:
        return set()
    result = await self.session.execute(
        select(col(RecordType.name)).where(col(RecordType.role_name).in_(list(role_names)))
    )
    return set(result.scalars().all())
```
Имена ролей юзера — `get_user_role_names(user)` из `clarinet/api/dependencies.py:410-423` (roles уже eagerly loaded после `read_token`).

### 2.6 `clarinet/api/routers/ws.py` — новый роутер
```python
router = APIRouter()
PING_INTERVAL = 30.0

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    auth = await authenticate_websocket(websocket)
    if auth is None:
        await websocket.close(code=4401)
        return
    user, token = auth
    bus = get_event_bus()
    if bus is None:  # ws_enabled=False или lifespan не инициализирован
        await websocket.close(code=1011)
        return
    conn = WsConnection(
        user_id=user.id,
        is_admin=user.is_superuser or "admin" in get_user_role_names(user),
        allowed_types=await _load_allowed_types(user),
        queue=asyncio.Queue(maxsize=settings.ws_send_queue_size),
    )
    bus.register(conn)
    try:
        sender = asyncio.create_task(_send_loop(websocket, conn, user, token))
        receiver = asyncio.create_task(_recv_loop(websocket))
        done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    finally:
        bus.unregister(conn)
```
- `_load_allowed_types(user)`: короткоживущая сессия через `db_manager.get_async_session_context()` → `RecordTypeRepository(session).get_names_for_roles(get_user_role_names(user))`.
- `_send_loop`: цикл с дедлайнами `next_ping = monotonic() + PING_INTERVAL`, `next_reval = monotonic() + settings.ws_revalidate_seconds`:
  - `frame = await asyncio.wait_for(conn.queue.get(), timeout=до ближайшего дедлайна)`; по `TimeoutError` — проверить, какой дедлайн наступил;
  - `frame is None` (sentinel slow consumer) → `await websocket.close(code=4408)`; return;
  - отправка `await websocket.send_text(frame)`; если кадр содержит `"record_type"` — после отправки пересчитать `conn.allowed_types` через `_load_allowed_types` (новые типы становятся видимы сразу, не через 5 минут);
  - ping-дедлайн → `send_text('{"type": "ping"}')`;
  - reval-дедлайн → свежая сессия + `DatabaseStrategy(session).read_token(token, None)`; `None` → `send_text('{"type": "auth_expired"}')` + `close(4401)` + return.
- `_recv_loop`: `while True: await websocket.receive_text()` — дренаж входящих (игнор), `WebSocketDisconnect` завершает задачу.
- Никаких try/except вокруг доменных ошибок — но `WebSocketDisconnect` в send-loop (отправка в закрытый сокет) гасить и выходить.

### 2.7 `clarinet/api/app.py` — wiring
- Lifespan (функция `lifespan`, строки 175–453): **перед** `logger.info("Application startup complete")` (строка ~444):
  ```python
  from clarinet.services.events.bus import EventBus, set_event_bus
  from clarinet.services.events.capture import register_capture_listeners

  app.state.event_bus = EventBus(asyncio.get_running_loop())
  set_event_bus(app.state.event_bus)
  register_capture_listeners()
  ```
  В `finally` (перед `await db_manager.close()`, строка ~481):
  ```python
  if getattr(app.state, "event_bus", None) is not None:
      app.state.event_bus.shutdown()
  set_event_bus(None)
  ```
  Слушатели capture остаются зарегистрированными между lifespans (идемпотентный register, bus=None ⇒ no-op) — это и есть re-creatable shutdown pattern (`clarinet/CLAUDE.md`).
- `create_app` (блок include_router, строки 532–547, рядом с `health.router`):
  ```python
  if settings.ws_enabled:
      from clarinet.api.routers import ws
      app.include_router(ws.router, prefix="/api", tags=["WebSocket"])
  ```
  Образец условного монтирования — dicomweb, строки 550–552.

### 2.8 Явные emit для UoW-невидимых мутаций
Каждый call-site пометить комментарием `# ws-capture: explicit emit, UoW-invisible (Core bulk DML)`.

1. **`RecordRepository.delete_records`** (`record_repository.py:1104-1129`, Core `sa_delete` на 1125): после `await self.session.commit()` в ветке `commit=True` → `emit_entity("record", "deleted", [str(i) for i in record_ids])`.
2. **`RecordService.delete_record_cascade`** (`record_service.py:566`) вызывает `delete_records(..., commit=False)` и коммитит сам → после его commit тот же `emit_entity` со списком всех удалённых id (они уже есть — возвращаемое значение `tuple[list[int], int]`).
3. **`delete_patient`/`delete_study`** (`study_service.py:382-410` → `BaseRepository.delete` = `session.delete(entity)` + commit): сначала написать тест `test_delete_patient_emits_child_deleted` (см. 2.10). Relationships объявлены `cascade_delete=True` (ORM-каскад: `patient.py:99,108`, `study.py:48,49,89`, `record.py:231-234`) — при flush ORM загружает детей и кладёт их в `session.deleted`, capture увидит их сам. **Если тест зелёный — ничего не делать.** Если красный (дети снесены СУБД мимо ORM): перед `repo.delete(...)` собрать id детей лёгкими select'ами (`select(col(Study.study_uid)).where(...)`, аналогично Series по списку study_uid и Record по `patient_id`/`study_uid`) и `emit_entity(...)` после commit.
4. `Record.parent_record_id` `ON DELETE SET NULL` (`record.py:202-206`) — осиротевшие дети события не получают. Осознанно пропущено (minor, parent редко влияет на UI) — зафиксировать комментарием в `delete_records`.
5. Raw SQL в `report_repository.py:83` — READ ONLY (guard `_validate_select_only`, строки 29–41), событий не требует.

### 2.9 `tests/utils/urls.py`
Добавить константу: `WS_URL = "/api/ws"`.

### 2.10 Тесты фазы 2

Фикстура (в новом `tests/test_ws_events.py`):
```python
class RecordingBus:
    def __init__(self): self.events = []
    def publish(self, event): self.events.append(event)
    def publish_threadsafe(self, event): self.events.append(event)

@pytest.fixture
def ws_bus():
    register_capture_listeners()
    bus = RecordingBus()
    set_event_bus(bus)  # type: ignore[arg-type]
    yield bus
    set_event_bus(None)
```

| Тест | Файл | Суть |
|---|---|---|
| `test_record_insert_emits_created` | `tests/test_ws_events.py` | `seed_record(...)` → событие `record/created` с верными `id` (строка), `record_type_name`, `user_id` |
| `test_record_update_emits_updated` | — | `repo.update_fields(id, {"context_info": "x"})` (ORM-путь, `record_repository.py:660-677`) → `record/updated` |
| `test_rollback_emits_nothing` | — | add + flush + rollback → `bus.events == []` |
| `test_savepoint_retry_emits_single_patient_created` | — | занять следующий `auto_id` прямой вставкой (`session.add(Patient(auto_id=N))` без advance), затем `PatientRepository.create()` → ровно одно `patient/created` |
| `test_delete_records_bulk_emits_deleted` | — | `delete_records([ids])` → `record/deleted` по каждому id (explicit emit) |
| `test_delete_patient_emits_child_deleted` | — | патиент+study+series+record; `StudyService.delete_patient` → события `deleted` для всех четырёх сущностей (см. 2.8.3 — развилка) |
| `test_dedup_created_then_updated_single_event` | — | create + update в одной транзакции до commit → одно событие `created` |
| `test_allow_rbac_matrix` | `tests/test_ws_bus.py` | unit на `_allow`: admin видит всё; обычный — своё `user_id`-событие и свой тип; чужой тип отфильтрован; `patient/*` только админу; `record_type` всем; `TaskProgress(user_id=X)` только X |
| `test_slow_consumer_gets_sentinel` | — | queue maxsize=2, publish×3 → очередь содержит только `None`, conn выписан из bus |
| `test_ws_rejects_bad_cookie` | `tests/integration/test_ws_endpoint.py` | `TestClient(app)`: `websocket_connect(WS_URL)` без cookie → сервер закрывает 4401 (`WebSocketDisconnect.code == 4401` при `receive`) |

Интеграционный happy-path (`test_ws_receives_record_event`): `starlette.testclient.TestClient` поднимает app в отдельном потоке со своим event loop — **нельзя** переиспользовать `test_session`/`test_engine` фикстуры (cross-loop). Рецепт: file-based SQLite через monkeypatch `CLARINET_DATABASE_URL` + полный `with TestClient(app) as tc:` (прогонит lifespan: создание таблиц + admin), `tc.post("/api/auth/login", ...)` кредами админа из settings, `tc.websocket_connect(WS_URL)`, мутация через `tc.post(...)`, `ws.receive_json()` (пропуская `ping`) → кадр `entity`. Образец lifespan-in-test — `tests/test_app_startup.py`. Если упрётся в инфраструктуру более чем на ~час — оставить только handshake-тесты, happy-path вынести в ручную проверку фазы 3.

### Проверка фазы 2
```bash
./scripts/run_tests.sh -k "ws" -q > /tmp/test-ws-p2.txt 2>&1
make check
timeout 300 make test-unit > /tmp/test-ws-p2-unit.txt 2>&1
```
Коммит: `feat(ws): event bus, UoW capture, cookie-auth /api/ws endpoint`

---

## Фаза 3 — фронтенд-ядро

Все пути — от `clarinet/frontend/`. Контракты модулей: `.claude/rules/frontend-page-contract.md` (автоподгрузится). Таймеры — `plinth/javascript/global` (`set_timeout/clear_timeout`, тип `TimerID`).

### 3.1 `src/utils/websocket.gleam` + `src/utils/websocket.ffi.mjs`
Низкоуровневый транспорт. Паттерн FFI — как `utils/viewer_window.gleam`(:7-17)+`.ffi.mjs`; паттерн effect — как modem (`build/packages/modem/src/modem.gleam:90-98`).

```gleam
// websocket.gleam
import gleam/bool
import lustre
import lustre/effect.{type Effect}

pub type WebSocket

pub type Event {
  Connected(WebSocket)
  MessageReceived(String)
  Closed(code: Int)
}

pub fn connect(path: String, to_msg: fn(Event) -> msg) -> Effect(msg) {
  use dispatch <- effect.from
  use <- bool.guard(!lustre.is_browser(), Nil)
  do_connect(
    path,
    fn(ws) { dispatch(to_msg(Connected(ws))) },
    fn(text) { dispatch(to_msg(MessageReceived(text))) },
    fn(code) { dispatch(to_msg(Closed(code))) },
  )
}

pub fn close(socket: WebSocket) -> Effect(msg) {
  use _dispatch <- effect.from
  do_close(socket)
}

@external(javascript, "./websocket.ffi.mjs", "connect")
fn do_connect(path: String, on_open: fn(WebSocket) -> Nil, on_message: fn(String) -> Nil, on_close: fn(Int) -> Nil) -> Nil

@external(javascript, "./websocket.ffi.mjs", "closeSocket")
fn do_close(socket: WebSocket) -> Nil
```
```js
// websocket.ffi.mjs
export function connect(path, onOpen, onMessage, onClose) {
  const url = new URL(path, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(url);
  ws.onopen = () => onOpen(ws);
  ws.onmessage = (e) => { if (typeof e.data === "string") onMessage(e.data); };
  ws.onclose = (e) => onClose(e.code);
}

export function closeSocket(ws) {
  ws.onclose = null;  // deliberate close: не диспатчить Closed (иначе reconnect)
  try { ws.close(); } catch (_) {}
}
```
Бинарные кадры дропаются осознанно (протокол текстовый). Один `connect`-эффект диспатчит многократно — колбэки живут всю жизнь сокета.

### 3.2 `src/api/ws_events.gleam` — декодер wire-формата
```gleam
pub type Action { Created Updated Deleted }

pub type EntityEvent {
  EntityEvent(entity: String, action: Action, id: String,
    record_type_name: Option(String), user_id: Option(String))
}

pub type WsEvent {
  Entity(EntityEvent)
  TaskProgress(task: String, task_id: String, payload: dynamic.Dynamic)
  AuthExpired
  Ping
}

pub fn decode_frame(text: String) -> Result(WsEvent, Nil)
```
Реализация: `json.parse(text, frame_decoder())`; внутри — `decode.field("type", decode.string)` и `case`-ветвление (`decode.then`): `"entity"` → поля (`record_type_name`/`user_id` через `decode.optional_field(key, None, decode.optional(decode.string))`), `"task_progress"` → `payload` как `decode.dynamic`, `"auth_expired"`/`"ping"` → константы; неизвестный type → `decode.failure(Ping, "WsEvent")`. Action: `"created"|"updated"|"deleted"`, иначе failure.

### 3.3 `src/ws.gleam` — координатор соединения (store-level модуль по образцу `preload.gleam`)
```gleam
pub type State { Idle Connecting Connected(websocket.WebSocket) }

pub type Model {
  Model(state: State, attempt: Int,
    reconnect_timer: Option(global.TimerID), watchdog: Option(global.TimerID))
}

pub type Msg {
  Connect
  WsEvent(websocket.Event)
  ReconnectTick
  WatchdogTick
  Stop
}

pub type OutMsg {
  WsConnected(reconnected: Bool)
  WsEntityEvent(ws_events.EntityEvent)
  WsTaskProgress(task: String, task_id: String, payload: dynamic.Dynamic)
  WsAuthExpired
}

pub fn init() -> Model  // Idle, attempt 0, таймеры None
pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg), List(OutMsg))
```
Переходы `update`:

| Msg | Условие | Действие |
|---|---|---|
| `Connect` | state == Idle | → Connecting; effect `websocket.connect(config.base_path() <> "/api/ws", WsEvent)` |
| `WsEvent(Connected(sock))` | — | → `Connected(sock)`; OutMsg `[WsConnected(reconnected: model.attempt > 0)]`; attempt → 0; armed watchdog (`set_timeout(90_000, WatchdogTick)`) |
| `WsEvent(MessageReceived(text))` | — | re-arm watchdog (clear + set 90s); `decode_frame`: `Entity(e)` → `[WsEntityEvent(e)]`; `TaskProgress(..)` → `[WsTaskProgress(..)]`; `AuthExpired` → `[WsAuthExpired]`; `Ping`/`Error` → `[]` (ошибку декода — `logger.warn("ws", ...)`) |
| `WsEvent(Closed(4401))` | — | clear таймеры → Idle; `[WsAuthExpired]` (без reconnect) |
| `WsEvent(Closed(_))` | — | clear watchdog → Idle; `set_timeout(backoff_ms(attempt), ReconnectTick)`; attempt + 1 |
| `ReconnectTick` | state == Idle | как `Connect` |
| `WatchdogTick` | state == Connected | `websocket.close(sock)` нас не уведомит (onclose снят) → вручную: → Idle + reconnect-таймер как при `Closed(_)` |
| `Stop` | — | clear оба таймера; если Connected — `websocket.close(sock)`; → `init()` |

`backoff_ms(attempt) = int.min(1000 * int.bitwise_shift_left(1, attempt), 30_000)` (1s→2s→…→30s cap).

### 3.4 `src/store.gleam`
- `Model` (строки 32–61): добавить поля `ws: ws.Model` и `ws_enabled: Bool`.
- `init()` (строки 180–200): `ws: ws.init(), ws_enabled: False`.
- `Msg` (строки 100–177): добавить `WsMsg(ws.Msg)` рядом с `PreloadMsg` (строка 173).
- `reset_for_logout` (строки 211–222): строится от `init()` — ws сбросится сам; **сохранить** `ws_enabled: model.ws_enabled` (как project_name).

### 3.5 `src/main.gleam`
1. Хелпер:
   ```gleam
   fn ensure_ws(model: store.Model) -> Effect(store.Msg) {
     case model.user, model.ws_enabled, model.ws.state {
       Some(_), True, ws.Idle -> dispatch_effect(store.WsMsg(ws.Connect))
       _, _, _ -> effect.none()
     }
   }
   ```
   (`dispatch_effect` — `use dispatch <- effect.from; dispatch(msg); Nil`, если аналога нет.)
2. Точки вызова `ensure_ws` (батчить к существующим эффектам):
   - `store.CheckSessionResult(Ok(user))` — строки 265–320;
   - `store.ProjectInfoLoaded(Ok(info))` — строки 429–443: сюда же `ws_enabled: info.ws_enabled` в Model;
   - `apply_out_msgs` ветка `shared.SetUser(user)` — строки 905–964.
3. Делегирование `store.WsMsg(m)`: `ws.update(model.ws, m)` → записать model, `effect.map(_, store.WsMsg)`, и транслировать OutMsg-список:

   | ws.OutMsg | Действие main |
   |---|---|
   | `WsConnected(False)` | ничего (страница только что загружена) |
   | `WsConnected(True)` | resync: dispatch `store.CacheMsg(cache.InvalidateAllRecordBucketsMsg)` + `store.CacheMsg(cache.InvalidateFilterOptions)` + `init_page_for_route(model, model.route)` (строка 854) |
   | `WsEntityEvent(e)` | dispatch `store.CacheMsg(cache.WsEntityEvent(e))` |
   | `WsTaskProgress("preload", id, payload)` | dispatch `store.PreloadMsg(preload.ProgressPush(id, payload))` |
   | `WsTaskProgress("quarto_render", _, payload)` | если `model.page` — `store.AdminQuartoReportsPage(_)` → делегировать `store.AdminQuartoReportsMsg(quarto_reports.RenderPushed(payload))` (арм делегирования — строки 505–517); иначе игнор |
   | `WsAuthExpired` | dispatch `store.Logout`, **только если** `model.user != None` (идемпотентность) |
4. Ветка `store.Logout` (строки 341–360): добавить в batch эффект `dispatch store.WsMsg(ws.Stop)`.

### 3.6 `src/cache.gleam` — событийный кэш
- `Model` (строки 27–42): добавить `ws_debounce: Option(global.TimerID)`; обновить `cache.init()`.
- `Msg` (строки 46–87): добавить:
  ```gleam
  WsEntityEvent(event: ws_events.EntityEvent)
  WsRecordRefetched(id: String, result: Result(Record, ApiError))
  RefetchStaleBuckets
  ```
- Обработка `WsEntityEvent(e)` по `e.entity`/`e.action`:

  | entity | действие |
  |---|---|
  | `"record"`, action ≠ Deleted | если `dict.has_key(model.records, e.id)` → effect `records.get_record(e.id)` → `WsRecordRefetched(e.id, result)`. **Не** использовать `LoadRecordDetail` (у него auto-assign side effects). Всегда: пометить бакеты + дебаунс (ниже) |
  | `"record"`, Deleted | `dict.delete(model.records, e.id)`; убрать запись из `items` всех бакетов; пометить бакеты + дебаунс |
  | `"patient"` | Deleted → `dict.delete`; иначе если в dict → dispatch существующего `LoadPatientDetail(e.id)` |
  | `"study"` | Deleted → `dict.delete(model.studies, e.id)`; иначе если dict непуст → `LoadStudies` (точечного лоадера нет) |
  | `"series"` | Deleted → `dict.delete`; иначе если в dict → `LoadSeriesDetail(e.id)` |
  | `"record_type"` | Deleted → `dict.delete`; иначе если dict непуст → `LoadRecordTypes`; всегда → `InvalidateFilterOptions` + пометить бакеты + дебаунс |
  | `"user"` | если dict непуст → `LoadUsers` (события приходят только админам) |

- «Пометить бакеты + дебаунс»: все бакеты в `Live(_)`/`LoadingMore(_)` → `bucket.mark_stale` (`cache/bucket.gleam:83-89`); если `ws_debounce == None` → `set_timeout(750, RefetchStaleBuckets)`, сохранить TimerID.
- `RefetchStaleBuckets`: `ws_debounce = None`; для каждого бакета в `Stale(loaded_at)`:
  - `now - loaded_at > 600_000` (10 мин) → удалить бакет из dict (повторный визит загрузит через существующий `FetchBucketMsg`);
  - иначе → **тихий refetch**: тот же fetch-эффект, что у `FetchBucketMsg(key)` (`cursor=None`, результат в существующий `BucketLoaded(key, _)`), но **без** установки статуса `Loading` — items остаются на экране (stale-while-revalidate, страницы не мигают спиннером). Вынести существующий эффект фетча в приватный `fetch_bucket_effect(key)` и переиспользовать.
  - Хвостом: `InvalidateFilterOptions`-логика (refetch) + если `record_type_stats == Some(_)` → dispatch `LoadRecordTypeStats`.
- `WsRecordRefetched(id, Ok(record))`: `put_record` (строка 507) + `upsert_record_in_buckets` (строка 563).
- `WsRecordRefetched(id, Error(_))` (403/404 — запись стала недоступна): `dict.delete(model.records, id)` + убрать из items бакетов.
- Существующие механизмы не трогать: TTL 60s и LRU cap остаются страховкой, `upsert_record_in_buckets` — для локальных оптимистичных мутаций.

### 3.7 Тесты фазы 3 (`test/`, образец — `test/preload_test.gleam`)
- `test/ws_events_test.gleam`: декодер — кадр record с user_id, кадр entity без опциональных полей, task_progress (payload dynamic), auth_expired, ping, мусорный JSON → Error.
- `test/ws_test.gleam`: чистые переходы `ws.update` — Connected сбрасывает attempt и даёт `WsConnected(reconnected)` корректно (attempt 0 vs >0); `Closed(4401)` → `[WsAuthExpired]`, state Idle; `Closed(1006)` → attempt+1; `backoff_ms` капится на 30s; `Stop` из Connected.
- `test/cache_ws_test.gleam`: `WsEntityEvent(record deleted)` удаляет из records и bucket items; mark_stale переводит только Live/LoadingMore; `WsRecordRefetched(Error)` удаляет запись.

### Проверка фазы 3
```bash
make frontend-check
cd clarinet/frontend && gleam test > /tmp/test-ws-p3.txt 2>&1
make frontend-build
```
Ручная: `make run-dev`, два окна браузера → смена статуса записи в одном видна во втором без перезагрузки (≤1–2 с, после дебаунса); рестарт API → фронт переподключается (Network: новый ws-коннект) и список обновляется; logout → ws закрыт, реконнектов нет.
Коммит: `feat(frontend): websocket transport, event-driven cache invalidation`

---

## Фаза 4 — прогресс задач (preload + quarto)

### 4.1 Бэкенд: preload
`clarinet/services/dicomweb/service.py`:
- `start_preload` (строки 286–298): сигнатура → `async def start_preload(self, study_uids: list[str], user_id: UUID | None = None) -> str`; сохранить `self._preload_owners[task_id] = user_id` (новый dict; чистить в конце `_preload_worker` через `pop`).
- Роутер `clarinet/api/routers/dicomweb.py:224-235`: `_user: CurrentUserDep` → `user: CurrentUserDep`; `service.start_preload(body.study_uids, user.id)`.
- `_preload_worker` (строки 300–349): завести локальный хелпер:
  ```python
  def _publish(payload: dict[str, Any], *, force: bool = False) -> None:
      # throttle: not more than one frame per 500ms, terminal states always
      ...
      bus = get_event_bus()
      if bus is not None:
          bus.publish_threadsafe(TaskProgressEvent(
              task="preload", task_id=task_id, payload=dict(progress), user_id=user_id))
  ```
  Вызывать: после каждого `progress.update(...)` (строки 312, 331–336) — throttled; финальные `ready` (346) и `error` (349) — `force=True`. Throttle — `time.monotonic()` в замыкании, порог 0.5 s. `publish_threadsafe` обязателен: `on_progress` колбэк дёргается из потоков pynetdicom.

### 4.2 Frontend: `src/preload.gleam`
- `Model` (строки 27–33): + `last_push_ms: Int` (0 в `init`).
- `Msg` (строки 51–62): + `ProgressPush(task_id: String, payload: dynamic.Dynamic)`.
- `ProgressPush`: если `task_id` совпадает с активным прогрессом — `last_push_ms = now`, дальше **тот же код**, что `ProgressUpdate(task_id, Ok(payload))` (строки 216–270) — вынести общий обработчик payload в приватную функцию.
- `PollTick(task_id)` (поллинг `set_interval(2000, ...)`, строка 179): первой строкой guard — `now - model.last_push_ms < 2500` → пропустить fetch (`#(model, effect.none(), [])`). Push жив → HTTP-поллинг молчит; WS упал → поллинг сам возобновляется.
- Источник `now` в ms — тот же helper, что использует bucket `loaded_at_ms` (см. `cache.gleam`).

### 4.3 Бэкенд: quarto
`clarinet/services/quarto_render.py::write_status` (строки 69–95): после записи файла опубликовать собранный `payload` (строки 83–92):
```python
bus = get_event_bus()
if bus is not None:
    bus.publish_threadsafe(TaskProgressEvent(
        task="quarto_render", task_id=render_id, payload=payload))  # user_id=None -> admins only
```
`write_status` всегда зовётся через `asyncio.to_thread` (call-sites: `quarto_report_service.py:130,151` и `quarto_render.py:150,172,184,198`) — поэтому `publish_threadsafe`. В TaskIQ-воркере (`pipeline_enabled=True`) bus отсутствует → no-op; пуш работает для PENDING/dispatch-FAILED (пишутся в API-процессе) и для всего цикла в in-process режиме. Комментарий об этом ограничении — прямо у publish.

### 4.4 Frontend: `src/pages/admin/quarto_reports.gleam`
- `Msg`: + `RenderPushed(payload: dynamic.Dynamic)`.
- Обработка: декодировать из payload `render_id`, `status`, `error` (декодер статуса уже есть, строки 216–225) → обновить соответствующий `RenderEntry` тем же кодом, что `RenderPolled` (вынести общую функцию). Неизвестный `render_id` → игнор.
- Поллинг-цепочку (строки 151–214, интервал 3000, cap 200) **не менять** — push лишь ускоряет обновление; на терминальном статусе поллинг прекращается существующей логикой.

### Проверка фазы 4
```bash
./scripts/run_tests.sh -k "preload or quarto or ws" -q > /tmp/test-ws-p4.txt 2>&1
make check && make frontend-check && make frontend-build
```
Ручная: preload OHIF — прогресс растёт без запросов к `/preload/progress/` во вкладке Network (poll молчит при живом WS); обрыв WS (kill API, рестарт) — прогресс продолжает обновляться поллингом. Quarto (при `pipeline_enabled=False`): статус рендера меняется без поллинг-задержки 3 с.
Коммит: `feat(ws): push task progress for dicomweb preload and quarto renders`

---

## Фаза 5 — audit-обогащение (ЗАБЛОКИРОВАНА до мержа PR #330/#332)

Перед стартом: убедиться, что в main есть `clarinet/models/record_event.py` (PR #332) и `clarinet/models/pipeline_task_run.py` (PR #330); ребейзнуть ветку. **Сверить поля моделей с фактическим кодом** — PR могли измениться; ниже снапшот на 2026-06-12.

Снапшот: `RecordEvent` (`record_event.py:38-88`): `record_id: int|None` (FK SET NULL), `record_key: int|None` (денормализованный id, переживает удаление), `kind: str` (`created|status_changed|data_submitted|data_updated|assigned|unassigned|failed|invalidated|context_info_updated|files_cleared|deleted`), `actor_id: UUID|None`, `from_status/to_status`, `old_value/new_value`, `occurred_at`. Пишется `RecordService` рядом с мутацией (та же транзакция). `PipelineTaskRun` (`pipeline_task_run.py:21-96`): PK `id` = TaskIQ task_id, `task_name`, `queue`, `record_id`, `status` (`running|succeeded|failed|retrying`), `result`. Пишется `AuditMiddleware` **через HTTP API** (`POST/PATCH /api/pipelines/runs*`) — коммиты происходят в API-процессе ⇒ UoW-capture их видит.

Работы в `capture.py`:
1. **Audit-обогащение record-событий.** В `_on_flush` дополнительно ловить вставки `RecordEvent` в `session.new` → копить отдельным списком `audit_events` (поля: `record_key`, `kind`, `actor_id`). В `_on_commit`: маппинг `kind → action` (`created→created`, `deleted→deleted`, прочее → `updated`), `actor_id → user_id`; дедуп по `(entity="record", id)`: есть audit-событие → uow-дубль выбрасывается (audit богаче — несёт actor); record-мутация без audit-пары остаётся тонким uow-событием.
   - Внимание: у audit-события нет `record_type_name` (его нет в RecordEvent) — для RBAC-фильтра брать `record_type_name` из uow-пары по `record_key`; если пары нет (cascade-delete со снапшотом) — поле из `old_value`-снапшота или `None` (тогда фильтр пропустит событие только владельцу/админам — деградация задокументирована).
2. **`task_progress` для pipeline-задач.** `PipelineTaskRun` в `session.new`/`session.dirty` → `TaskProgressEvent(task="pipeline", task_id=run.id, payload={"task_name": ..., "status": ..., "record_id": ...})` админам. Расширить `Literal` в `models.py` и протокол (`task: "pipeline"`). Фронт-консьюмер — отдельной задачей (вне этого плана).
3. **Детектор рассинхрона.** uow-событие `record`, не покрытое audit-парой в той же транзакции **и** затронувшее аудируемую колонку (`status`, `user_id`, `data`, `context_info`, создание/удаление; `started_at`/`finished_at`/`checksum`/`anon_*` не считаются — в `_on_flush` для dirty-Record брать изменённые колонки через `sqlalchemy.inspect(obj).attrs[col].history`): прод — `logger.warning` внутри try/except `_on_commit`; тесты — при `CLARINET_WS_AUDIT_STRICT=1` копить в module-level список `orphan_events`, фикстура ассертит пустоту.
4. **Тесты** (`tests/test_ws_audit.py`): A (fallback) — мутация аудируемой колонки мимо RecordService (`repo.update_fields`, как `routers/record.py:398` context-info) → record-событие приходит; B (детектор) — тот же путь → WARNING в caplog / strict-список непуст; C (дедуп) — мутация через RecordService → ровно одно событие, с `user_id=actor_id`, без дубля.

Коммит: `feat(ws): audit-enriched record events, pipeline task progress, drift detector`

---

## Сквозная верификация (после фаз 2–4)

1. `./scripts/run_tests.sh -k "ws" -q > /tmp/test-ws-final.txt 2>&1` — все ws-тесты.
2. `make check` (после него — `Read` файлов заново перед дальнейшими `Edit`: ruff format мог переписать).
3. `timeout 300 make test-unit > /tmp/test-ws-unit.txt 2>&1`.
4. `make frontend-check && (cd clarinet/frontend && gleam test) && make frontend-build`.
5. Ручной сценарий из фаз 3–4 (два окна, рестарт сервера, preload, quarto).
6. E2E (Playwright, два контекста) в `deploy/test/e2e/` — опционально, отдельным PR-куском, только если фазы 1–4 прошли.

## Жёсткие запреты (нарушение = баг)

- В `after_flush` — никаких relationship-доступов (`MissingGreenlet`), только column-атрибуты.
- Не держать `AsyncSession` на время жизни WS-соединения — только короткоживущие `db_manager.get_async_session_context()` на connect/ревалидацию/refresh типов.
- `_on_commit` не должен уметь ронять бизнес-операцию — всё тело в try/except.
- Не добавлять полей данных в `EntityEvent` — события «тонкие», клиент дотягивает REST'ом под своим RBAC (исключает утечку `mask_records`-маскируемых данных).
- `asyncio.gather` с запросами на одной shared-сессии запрещён (`clarinet/CLAUDE.md`).
- Новый bulk/каскадный путь мутации без `emit_entity` + маркера `# ws-capture: ...` — регресс: молча исчезнут события.
