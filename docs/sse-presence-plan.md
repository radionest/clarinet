# Admin online presence — план реализации

Бейджи присутствия (зелёный кружок у онлайн-пользователей) в Role Matrix админки.
Живое обновление через SSE-события сессий. **Следующий PR поверх #368 (SSE)**.

> **Решение (зафиксировано):** онлайн = **валидная сессия** (не истекла И простой <`session_idle_timeout_minutes`=60м); idle-out — событийный через **idle-eviction в очистке** (§3.7). Альтернативы и обоснование — §1.

## Зависимость и секвенсирование

- Фича не собирается без инфраструктуры #368 (`services/events/`, `/api/events`,
  `src/sse.gleam`, `src/api/sse_events.gleam`, `utils/event_source`). #368 ещё OPEN.
- Реализация — стек поверх ветки `worktree-sse-migration`; PR-база = эта ветка.
  После мержа #368 → ребейз на `main`, смена базы PR.
- `auth_config.py`, `utils/session.py`, `services/session_cleanup.py`, `pages/admin.gleam`,
  `api/admin.gleam`, `models/admin.py`, `routers/admin.py`, `badges.css` — **#368 их не трогает**,
  конфликтов с базой нет.

---

## 1. Определение «онлайн» — альтернативы и решение

Это главное проектное решение: оно задаёт snapshot-запрос, какие события эмитятся и где «дыры».

### Сигналы в данных (`access_token`)

| Сигнал | Условие | Срок жизни |
|---|---|---|
| строка существует, не истекла | `expires_at > now` | login → +24ч (`session_expire_hours`); продлевается sliding-refresh при активности (потолок 30 дней); удаляется раньше при logout/revoke; удаляется очисткой ≤1ч после истечения |
| сессия проходит idle-проверку | `last_accessed > now − 60м` (`session_idle_timeout_minutes`) | `read_token` отклоняет токен при простое >60м, но **строку не удаляет и события не шлёт** |
| активен «прямо сейчас» | `last_accessed > now − N` (N мал) | гранулярность записи ~30с (`session_cache_ttl_seconds`) |

### Ключ к SSE: дискретные vs пассивные переходы

SSE шлёт **дискретные события** — их можно сэмитить ровно в момент перехода:
- `login` (write_token), `logout` (destroy_token), `revoke` — **дискретны**.

Сложность — **пассивные** переходы (наступают сами по времени, без точки в коде):
- **expiry** (`expires_at` прошло) — строку удаляет только часовая очистка (`_perform_cleanup`),
  которая **уже грузит `user_id` удаляемых токенов** → из неё МОЖНО сэмитить offline.
- **idle-out** (простой >60м) — `read_token` отклоняет, но **ничего не удаляет**;
  очистка трогает только `expires_at<=now`, не простаивающие → **источника события нет**.

### Альтернативы

| | **A. Живая сессия** | **C. Валидная сессия** (рекомендуется) | **B. Недавняя активность** |
|---|---|---|---|
| Онлайн = | `expires_at>now` | `expires_at>now` **И** `last_accessed>now−60м` | `expires_at>now` **И** `last_accessed>now−15м` |
| online-событие | login | login | login |
| offline мгновенно | logout, revoke (если последняя) | logout, revoke (если последняя) | logout, revoke |
| пассивный переход | expiry → **эмит из очистки** ✅ | idle-out → **нет источника** ⚠️ | idle-out 15м → **нет источника** ⚠️ |
| snapshot ↔ события согласованы | **да** (одно определение, очистка закрывает expiry) | да, если idle-out лечить (см. ниже) | нужен частый sweep (~1/мин) |
| точность «кто онлайн» | плохо: ушедший без logout висит **до 24ч** | хорошо: **60м** (= порог авторизации) | лучше: 15м |
| доп. инфраструктура | нет | нет (либо +eviction idle в очистке) | новый серверный sweep |
| новая настройка | нет | нет (reuse `session_idle_timeout_minutes`) | да (или reuse) |

### Решение — **C + refinement** (валидная сессия, idle-out событийный)

«Онлайн = сессия, которой можно авторизоваться прямо сейчас» (не истекла **и** не превысила
idle-таймаут). Это «живая сессия» в строгом смысле: простоявшая >60м сессия уже отклоняется
`read_token` — она **не живая**, хотя строка ещё есть. Переиспользуем существующий
`session_idle_timeout_minutes` (60м) — порог уже настраиваемый.

- **A** переоценивает присутствие: кружок висит до 24ч у того, кто ушёл, не разлогинившись.
- **B** требует частого серверного sweep'а и отдельной настройки.

**idle-out — закрывается refinement'ом (в составе фичи, §3.7):** часовая очистка
`_perform_cleanup` дополнительно удаляет сессии с `last_accessed <= now − idle` (они уже невалидны
для `read_token`, удалять безопасно — см. ниже) и эмитит presence `offline` для юзеров без
оставшихся валидных сессий. Тогда idle-out — событийный; точность = интервал очистки
(сейчас `session_cleanup_interval=3600`; для меньшей задержки idle-offline — уменьшить).

**Почему idle-сессии не удалялись раньше (и почему refinement безопасен):** `expires_at` —
единственные «часы удаления», на них построена очистка; idle — runtime-гейт в `read_token`.
idle-сессия уже недоступна и не воскресает (idle-проверка в `read_token` стоит до бампа
`last_accessed` → каждый запрос 401 до обновления), а по `expires_at` (≤24ч) она и так
удаляется. Эгер-удаление было лишь незадействованной микро-оптимизацией хранения; новая ценность —
**источник события offline для idle**, которого до этой фичи не требовалось. Цена — лёгкая связка
фонового GC с auth-политикой (idle), которые раньше были разделены.

### Локализация выбора в коде

Альтернатива = **один аргумент** `within_minutes` у общего хелпера:
- `get_online_user_ids(session, within_minutes=None)` → `None` = A; `=session_idle_timeout_minutes` = C; `=15` = B.
Снимок-эндпоинт и offline-guard зовут этот хелпер с одним значением. Сменить альтернативу = поменять одну константу.

---

## 2. Wire-протокол — новый кадр `presence`

Дополняет протокол #368 (см. `docs/sse-migration-tasks.md` §«Wire-протокол»). Новый тип кадра:

```
data: {"type": "presence", "user_id": "<uuid>", "online": true}
data: {"type": "presence", "user_id": "<uuid>", "online": false}
```

- Адресат — **только admin** (онлайн-статус = admin-only данные, как `entity in {patient,study,series,user}`).
- «Тонкое» событие: только `user_id` + `online`. Никаких PII сверх того, что админ уже видит в Role Matrix.

---

## 3. Backend (стек на #368)

### 3.1 `clarinet/services/events/models.py`
Добавить класс и расширить алиас:
```python
class PresenceEvent(BaseModel):
    """A user coming online (session acquired) or going offline (last session gone)."""
    user_id: UUID
    online: bool

    def to_wire(self) -> str:
        return json.dumps({"type": "presence", "user_id": str(self.user_id), "online": self.online})

type Event = EntityEvent | TaskProgressEvent | PresenceEvent
```

### 3.2 `clarinet/services/events/bus.py`
В `_allow`, до ветки EntityEvent (рядом с TaskProgressEvent):
```python
if isinstance(event, PresenceEvent):
    return conn.is_admin   # presence — admins only
```
Импорт `PresenceEvent` из `.models`.

### 3.3 `clarinet/services/events/capture.py`
Хелпер рядом с `emit_entity` (маркер `# sse-capture: explicit emit, session lifecycle`):
```python
def emit_presence(user_id: UUID, online: bool) -> None:
    """Publish a presence transition. No-op without a bus (worker/CLI process)."""
    bus = get_event_bus()
    if bus is not None:
        bus.publish(PresenceEvent(user_id=user_id, online=online))
```
Все вызовы — в async-контексте на основном loop ⇒ `bus.publish` (не threadsafe) корректен.

### 3.4 `clarinet/utils/session.py`
Общий фильтр + два хелпера (источник истины для snapshot И offline-guard):
```python
async def get_online_user_ids(session, within_minutes: int | None) -> set[UUID]:
    """Distinct user_ids with a session valid 'now'. within_minutes=None → expiry only (Alt A)."""
    conds = [AccessToken.expires_at > datetime.now(UTC)]
    if within_minutes:
        conds.append(AccessToken.last_accessed > datetime.now(UTC) - timedelta(minutes=within_minutes))
    result = await session.execute(select(col(AccessToken.user_id)).where(*conds).distinct())
    return set(result.scalars().all())

async def is_user_online(session, user_id: UUID, within_minutes: int | None) -> bool:
    """True if the user still has ≥1 valid session — the offline-emit guard."""
    # SELECT 1 ... LIMIT 1 по тем же conds + user_id
```
В `revoke_user_sessions` (после commit): `if not await is_user_online(...): emit_presence(user_id, False)`.

### 3.5 `clarinet/api/auth_config.py` — точки жизненного цикла
- `write_token` (после `await self.session.commit()`, ~стр. 160): `emit_presence(user.id, True)`.
  (Идемпотентно: повторный online при второй сессии — на клиенте просто `set.insert`.)
- `destroy_token` (после commit, ~стр. 358): `if not await is_user_online(self.session, user.id, THRESHOLD): emit_presence(user.id, False)`.
- `THRESHOLD = settings.session_idle_timeout_minutes` (Alt C).
- Импорт `emit_presence`/`is_user_online` локально внутри функций (избежать циклов на уровне модуля).

### 3.6 `clarinet/api/routers/auth.py`
`revoke_session` (после commit, стр. 228): тот же offline-guard → `emit_presence`.

### 3.7 `clarinet/services/session_cleanup.py` — offline по expiry И idle (refinement)
Расширить предикат удаления в `_perform_cleanup` (и `cleanup_once`) с «истёкших» на «истёкшие ИЛИ idle-невалидные»:
```python
now = datetime.now(UTC)
idle = settings.session_idle_timeout_minutes
dead = AccessToken.expires_at <= now
if idle > 0:
    dead = or_(dead, AccessToken.last_accessed <= now - timedelta(minutes=idle))
# выбрать батч по `dead`, собрать affected = {t.user_id}, удалить, commit
```
В цикле уже грузятся `tokens_to_delete` с `user_id`. После commit батча:
`for uid in affected: if not await is_user_online(session, uid, idle): emit_presence(uid, False)`.
(Покрывает и expiry, и idle-out событийно — частота = интервал очистки. `cleanup_once` в CLI без шины:
emit — no-op, но предикат тот же для консистентности.) Retention-удаление «древних» — без изменений.

### 3.8 Снимок-эндпоинт
- `clarinet/models/admin.py`: `class OnlineUsersResponse(PydanticBaseModel): user_ids: list[str]`.
- `clarinet/api/routers/admin.py`: добавить `SessionDep` к импортам, новый эндпоинт:
```python
@router.get("/online-users", response_model=OnlineUsersResponse)
async def get_online_users(_current_user: AdminUserDep, session: SessionDep) -> OnlineUsersResponse:
    ids = await get_online_user_ids(session, settings.session_idle_timeout_minutes)
    return OnlineUsersResponse(user_ids=sorted(str(u) for u in ids))
```
- `tests/utils/urls.py`: `ADMIN_ONLINE_USERS_URL = "/api/admin/online-users"`.
- URL-таблица: `.claude/rules/api-urls.md` → строка в разделе Admin.

### 3.9 `clarinet/settings.py`
Рекомендация C — **новой настройки не нужно** (reuse `session_idle_timeout_minutes`).
Если нужен независимый порог (Alt B / отвязать от idle) — добавить `admin_online_threshold_minutes: int = 15`.

---

## 4. Frontend (стек на #368)

### 4.1 `src/api/sse_events.gleam`
- В `SseEvent` добавить `Presence(user_id: String, online: Bool)`.
- В `frame_decoder`: `"presence" -> presence_decoder()`:
```gleam
fn presence_decoder() -> decode.Decoder(SseEvent) {
  use user_id <- decode.field("user_id", decode.string)
  use online <- decode.field("online", decode.bool)
  decode.success(Presence(user_id:, online:))
}
```

### 4.2 `src/sse.gleam`
- В `OutMsg` добавить `SsePresence(user_id: String, online: Bool)`.
- В `handle_frame` ветка (re-arm watchdog, как у Entity):
```gleam
Ok(sse_events.Presence(user_id, online)) -> #(
  Model(..model, watchdog: None, last_frame_ms: now),
  arm_watchdog(model.watchdog),
  [SsePresence(user_id, online)],
)
```

### 4.3 `src/main.gleam`
В трансляции `sse.OutMsg` (таблица фазы 3.5 SSE-плана) добавить ветку `SsePresence(uid, online)`:
если текущая страница — admin dashboard (`store.AdminPage(_)`) → диспатч page-msg
`admin.PresenceChanged(uid, online)`; иначе игнор. **Паттерн — как `quarto_render`** («если открыта
страница X → её Msg, иначе игнор»). Точные имена `store.AdminPage` / page-Msg-конструктора —
сверить со `store.gleam` при реализации.

### 4.4 `src/api/admin.gleam`
```gleam
pub fn get_online_users() -> Promise(Result(List(String), ApiError)) {
  http_client.get("/admin/online-users")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(_, online_users_decoder(), "Invalid online users"))
  })
}
fn online_users_decoder() -> decode.Decoder(List(String)) {
  use user_ids <- decode.field("user_ids", decode.list(decode.string))
  decode.success(user_ids)
}
```

### 4.5 `src/pages/admin.gleam`
- `Model`: + `online_user_ids: set.Set(String)` (`import gleam/set`); `init` → `set.new()`.
  (Либо `List(String)` + `list.contains`, как уже сделано для `role_names` — на малом N эквивалентно.)
- `Msg`: + `OnlineUsersLoaded(Result(List(String), types.ApiError))`, `PresenceChanged(user_id: String, online: Bool)`.
- `init` batch (стр. 101): + `load_effect(admin_api.get_online_users, OnlineUsersLoaded)`.
- `update`:
  - `OnlineUsersLoaded(Ok(ids))` → `online_user_ids: set.from_list(ids)`.
  - `OnlineUsersLoaded(Error(err))` → presence некритичен: `AuthError → [shared.Logout]`, иначе `[]` (без тоста).
  - `PresenceChanged(uid, True)` → `set.insert`; `PresenceChanged(uid, False)` → `set.delete`.
- `view` `role_matrix_row` (стр. 529, td с email, после superuser-бейджа):
```gleam
case set.contains(model.online_user_ids, user.id) {
  True -> html.span([attribute.class("online-dot")], [])
  False -> element.none()
}
```
- **Самовосстановление:** main при `SseConnected(reconnected: True)` уже делает re-init страницы →
  `OnlineUsersLoaded` перезапрашивается → дрейф (в т.ч. idle-out) лечится на каждом reconnect.

### 4.6 `src/store.gleam`
Если страница ещё не имеет публичного page-Msg в `store.Msg` для внешнего диспатча — убедиться,
что вариант `AdminMsg(admin.Msg)` (или как он назван) существует; presence роутится через него.
(Делегирование `delegate_page_update` для admin уже есть — добавляем только обработку 2 новых Msg.)

### 4.7 CSS — `public/css/components/badges.css`
Зелёный кружок только у онлайн (офлайн — ничего), как выбрано:
```css
.online-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    margin-left: var(--spacing-xs);
    border-radius: 50%;
    background: var(--green);
    vertical-align: middle;
}
```
(Tooltip не делаем — выбран «зелёный кружок без подсказки». Если позже понадобится `title` —
добавить i18n-Key, не хардкод.)

---

## 5. Тесты

**Backend** (фикстуру `RecordingBus` из #368 расширить, чтобы ловить `PresenceEvent`):
- `test_presence_emit_on_login` — `write_token` → `PresenceEvent(online=True)`.
- `test_presence_emit_on_logout_last_session` — `destroy_token` при единственной сессии → offline;
  при второй активной сессии → offline **не** эмитится.
- `test_allow_presence_admin_only` — `_allow`: admin True, обычный False.
- `test_get_online_user_ids` — посев сессий (свежая / idle>60м / истёкшая) → в наборе только свежая (Alt C).
- `test_online_users_endpoint` — 200 + `user_ids` для admin; 403 для обычного.
- `test_cleanup_evicts_idle_and_emits_offline` — посеять idle>60м сессию (не истёкшую) → после
  `_perform_cleanup` строка удалена и пришёл `PresenceEvent(online=False)`; истёкшая → то же.

**Frontend** (образец `test/sse_events_test.gleam`, `test/sse_test.gleam`):
- декод `presence`-кадра (online true/false), мусор → Error.
- `sse.update`: `MessageReceived(presence)` → `[SsePresence(..)]`.
- `admin`: `OnlineUsersLoaded(Ok)` ставит набор; `PresenceChanged` insert/delete.

---

## 6. Фазы и коммиты

1. **Backend presence + idle-eviction.** §3 целиком (включая §3.7 refinement) + backend-тесты §5.
   `feat(sse): presence events, idle-session eviction, admin online-users snapshot`
2. **Frontend badges.** §4 целиком + frontend-тесты §5.
   `feat(admin): online presence dots in role matrix via SSE`

Проверка: `./scripts/run_tests.sh -k "presence or sse" -q > /tmp/test-presence.txt 2>&1`,
`make check`, `make frontend-check && (cd clarinet/frontend && gleam test) && make frontend-build`.
Ручная: два окна (admin + обычный юзер) → login/logout второго виден кружком без перезагрузки;
reconnect (рестарт API) → набор перезапрашивается.

---

## 7. Известные ограничения

- **idle-offline-латентность:** простой >60м гасит кружок на следующем проходе очистки (refinement §3.7),
  по умолчанию ≤1ч (`session_cleanup_interval`); для меньшей задержки — уменьшить интервал. snapshot при
  reconnect/навигации корректирует раньше.
- **Multi-worker:** in-process bus (наследуется от #368) — presence работает при одном uvicorn-процессе.
- **CLI `session revoke-user`** идёт в отдельном процессе без шины → пуша нет; лечится снимком при
  следующем reconnect/перезагрузке.
- **RBAC:** presence-кадры получают только admin-соединения (`_allow`); обычные юзеры онлайн-статус не видят.
