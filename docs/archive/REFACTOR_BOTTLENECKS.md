# RecordFlow Engine — Self-Referencing HTTP Bottlenecks

## Архитектурный контекст

RecordFlowEngine использует `ClarinetClient` для взаимодействия с API.
Клиент инициализируется в `src/api/app.py:44-49` с `base_url=http://{host}:{port}/api` —
все HTTP-вызовы идут **на тот же сервер**, в котором работает engine.

Все вызовы происходят внутри `BackgroundTasks` FastAPI (async, не блокируют HTTP-ответ клиенту),
но создают inbound HTTP-запросы, которые конкурируют за DB connection pool и event loop.

---

## Карта всех self-referencing HTTP вызовов

| # | Метод engine | Клиентский вызов | Строка | Паттерн вызова | Макс. запросов |
|---|---|---|---|---|---|
| 1 | `_get_record_context` | `find_records()` | 305, 311, 317 | elif-цепочка (1 из 3) | 1 |
| 2 | `_invalidate_records` | `find_records()` | 530 | цикл по `record_type_names` | N типов |
| 3 | `_invalidate_single_record` | `invalidate_record()` | 481 | цикл по найденным записям | M записей |
| 4 | `_create_record` | `create_record()` | 429 | одиночный | 1 |
| 5 | `_create_entity_record` | `create_record()` | 237 | одиночный | 1 |
| 6 | `_update_record` | `update_record_status()` | 465 | одиночный | 1 |
| 7 | `_call_function` | client передаётся в callback | 560 | произвольный | ? |
| 8 | `_call_entity_function` | client передаётся в callback | 265 | произвольный | ? |
| 9 | `_invalidate_single_record` | client передаётся в callback | 504 | произвольный | ? |

---

## Проблема 1: `_get_record_context` — избыточные запросы

**Файл:** `engine.py:288-325`

### Текущее поведение

Три последовательных `find_records()` по patient, study, series.
Запрос по `patient_id` через `JOIN Study → JOIN Patient` (`record_repository.py:521`)
уже возвращает записи **всех уровней** (patient, study, series) для данного пациента.
Study- и series-запросы — подмножества patient-запроса.

### Проблема

- Избыточная передача данных: до 3000 записей с HTTP-десериализацией, большинство — дубликаты.
- Study/series запросы полезны только как gap fillers при truncation по `limit=1000`.

### Решение

Заменить три `if` на `elif`-цепочку с приоритетом от широкого к узкому:

```python
async def _get_record_context(self, record: RecordRead) -> dict[str, RecordRead]:
    context: dict[str, RecordRead] = {}
    try:
        if record.patient:
            records = await self.clarinet_client.find_records(
                patient_id=record.patient.id, limit=1000
            )
        elif record.study:
            records = await self.clarinet_client.find_records(
                study_uid=record.study.study_uid, limit=1000
            )
        elif record.series:
            records = await self.clarinet_client.find_records(
                series_uid=record.series.series_uid, limit=1000
            )
        else:
            records = []
        self._update_context_from_records(context, records)
    except Exception as e:
        logger.error(f"Error getting record context: {e}")
    return context
```

**Результат:** 3 HTTP-запроса → 1. Без потери корректности (в DICOM-иерархии patient всегда есть, если есть study/series).

### Остаточный риск

`limit=1000` truncation: при >1000 записей у пациента часть контекста будет потеряна.
Допустимо, если ни один пациент не приближается к этому числу.
Если нет — увеличить лимит или добавить fallback-запросы при `len(records) == 1000`.

---

## Проблема 2: `_invalidate_records` — последовательный цикл find + invalidate

**Файл:** `engine.py:511-545`

### Текущее поведение

```
for target_type_name in action.record_type_names:       # N типов
    target_records = await find_records(...)             # 1 HTTP на тип
    for target in target_records:                        # M записей
        await _invalidate_single_record(target, ...)     # 1 HTTP на запись
```

При 5 типах и 20 записях на тип: **5 + 100 = 105 последовательных HTTP-запросов** к себе.

### Проблема

- Линейное время выполнения O(N + N*M) HTTP-вызовов.
- Каждый `invalidate_record()` — отдельный HTTP round-trip с полной сериализацией/десериализацией.
- При DICOM import с большим количеством серий (см. Проблему 4) эффект мультиплицируется.

### Решение

Два уровня параллелизации:

```python
async def _invalidate_records(
    self,
    action: InvalidateRecordsAction,
    record: RecordRead,
    context: dict[str, RecordRead],
) -> None:
    # 1) Параллельный поиск по всем типам
    async def find_targets(type_name: str) -> list[RecordRead]:
        try:
            return await self.clarinet_client.find_records(
                patient_id=record.patient.id,
                record_type_name=type_name,
                limit=1000,
            )
        except Exception as e:
            logger.error(
                f"Failed to find records of type '{type_name}' "
                f"for patient {record.patient.id}: {e}"
            )
            return []

    results = await asyncio.gather(*[
        find_targets(name) for name in action.record_type_names
    ])

    all_targets = [
        target
        for records in results
        for target in records
        if target.id != record.id
    ]

    # 2) Параллельная инвалидация всех найденных записей
    await asyncio.gather(*[
        self._invalidate_single_record(target, record, action)
        for target in all_targets
    ])
```

**Результат:** 105 последовательных → ~2 параллельных батча (find + invalidate).

### Подводные камни

- **DB connection pool exhaustion:** 100 одновременных `invalidate_record()` вызовов
  создадут 100 inbound HTTP-запросов. Нужен semaphore или chunked gather:
  ```python
  sem = asyncio.Semaphore(10)  # макс. 10 concurrent
  async def limited_invalidate(target):
      async with sem:
          await self._invalidate_single_record(target, record, action)
  ```
- **Callback ordering:** если `action.callback` зависит от порядка инвалидации —
  параллелизация нарушит последовательность. Текущий код не предполагает порядка,
  но пользовательские callbacks непредсказуемы.
- **Частичные ошибки:** `_invalidate_single_record` уже ловит исключения per-record,
  поэтому `gather` не упадёт от одной ошибки.

---

## Проблема 3: `_execute_flow` — последовательные actions

**Файл:** `engine.py:348-376`

### Текущее поведение

```python
# Unconditional actions
for action in flow.actions:
    await self._execute_action(action, record, context)

# Conditional actions
for condition in flow.conditions:
    result = await self._evaluate_and_run_condition(condition, context, record)
```

Каждый action — потенциальный HTTP-вызов (`create_record`, `update_record_status`,
`invalidate_records`, `call_function`). Все выполняются последовательно.

### Проблема

Flow с 3 `add_record()` + 1 `invalidate_records(5 типов)` = 3 + 105 = 108 последовательных
HTTP-вызовов. Время выполнения одного flow может составлять десятки секунд.

### Решение

Параллелизация **unconditional actions**, если порядок не важен:

```python
# Unconditional actions — параллельно
await asyncio.gather(*[
    self._execute_action(action, record, context) for action in flow.actions
])
```

### Подводные камни

- **Порядок может быть важен:** `add_record('A')` затем `update_record('A', status='ready')` —
  второй action зависит от первого. Сейчас DSL не декларирует зависимости между actions.
- **Context mutation:** `_create_record` не модифицирует `context`, но если в будущем
  добавится запись созданного record в context — параллельная мутация словаря.
- **Рекомендация:** пока не параллелизовать actions. Приоритет — решить Проблему 2,
  которая даёт основной выигрыш без риска нарушения семантики.

---

## Проблема 4: DICOM import — множественные параллельные BackgroundTasks

**Файл:** `src/api/routers/dicom.py:136-154`

### Текущее поведение

```python
for idx, s in enumerate(pacs_series):
    await service.create_series(...)
    if engine:
        background_tasks.add_task(
            engine.handle_entity_created,
            "series", patient_id, study_uid, s.series_instance_uid,
        )
```

Каждая серия регистрируется как отдельный BackgroundTask. При импорте study с 50 series →
50 задач, каждая из которых может вызвать `create_record()` и другие HTTP-вызовы.

### Проблема

- 50 BackgroundTasks запускаются FastAPI quasi-параллельно (все async, расчередуются на event loop).
- Каждая задача делает HTTP-вызов к себе → 50+ одновременных inbound-запросов.
- Если entity flow содержит `add_record` + flow на этот record содержит `invalidate_records` —
  каскадный эффект: 50 × (1 create + N invalidate) HTTP-вызовов.

### Решение

Батчирование entity triggers:

```python
# Вариант A: единый background task с последовательной обработкой
async def handle_bulk_entity_created(series_list):
    for s in series_list:
        await engine.handle_entity_created("series", patient_id, study_uid, s.uid)

background_tasks.add_task(handle_bulk_entity_created, pacs_series)
```

```python
# Вариант B: semaphore в engine для ограничения concurrency
class RecordFlowEngine:
    def __init__(self, client, max_concurrent=5):
        self._sem = asyncio.Semaphore(max_concurrent)

    async def handle_entity_created(self, ...):
        async with self._sem:
            ...  # existing logic
```

**Рекомендация:** Вариант B — минимальные изменения, универсальная защита.

---

## Проблема 5: Неконтролируемые callbacks

**Файл:** `engine.py:560, 265, 504`

### Текущее поведение

`ClarinetClient` передаётся в пользовательские функции:

- `_call_function` (строка 560) — flow action `.call(my_func)`
- `_call_entity_function` (строка 265) — entity flow action `.call(my_func)`
- `_invalidate_single_record` (строка 504) — invalidation callback

Разработчик flow может написать callback, делающий произвольное количество HTTP-вызовов
через переданный client.

### Проблема

Engine не контролирует, сколько HTTP-вызовов сделает callback.
Callback с `for item in items: await client.create_record(...)` —
неограниченное количество self-referencing запросов.

### Решение

Документирование + защитный таймаут:

```python
async def _call_function(self, action, record, context):
    kwargs = {
        "record": record,
        "context": context,
        "client": self.clarinet_client,
    } | action.extra_kwargs

    try:
        result = action.function(*action.args, **kwargs)
        if asyncio.iscoroutine(result):
            await asyncio.wait_for(result, timeout=30.0)  # защитный таймаут
    except asyncio.TimeoutError:
        logger.error(f"Callback {action.function.__name__} timed out after 30s")
    except Exception as e:
        logger.error(f"Error calling function {action.function.__name__}: {e}")
```

---

## Приоритеты реализации

| Приоритет | Проблема | Сложность | Выигрыш |
|---|---|---|---|
| 1 | `_get_record_context` → elif | Тривиальная | 3× меньше запросов на каждый flow trigger |
| 2 | `_invalidate_records` → gather | Средняя | O(1) вместо O(N+N*M) HTTP round-trips |
| 3 | DICOM import → semaphore | Низкая | Защита от cascade при bulk import |
| 4 | Callbacks → timeout | Низкая | Защита от зависания engine |
| 5 | `_execute_flow` → gather actions | Высокая (семантика) | Отложить до появления зависимостей |

---

## Общие рекомендации

### Semaphore на уровне engine

Единый `asyncio.Semaphore` ограничивает общее число concurrent self-referencing запросов
вне зависимости от источника (flow, invalidation, DICOM import, callback):

```python
class RecordFlowEngine:
    def __init__(self, clarinet_client: ClarinetClient, max_concurrent_requests: int = 10):
        self.clarinet_client = clarinet_client
        self._request_sem = asyncio.Semaphore(max_concurrent_requests)

    async def _guarded_request(self, coro):
        async with self._request_sem:
            return await coro
```

Все вызовы клиента оборачиваются через `_guarded_request()`.

### Альтернатива: уйти от HTTP

Фундальное решение — заменить `ClarinetClient` (HTTP) на прямые вызовы
repository/service layer внутри engine. Это устраняет self-referencing полностью,
но требует передачи `AsyncSession` в engine и управления транзакциями.
Значительный рефакторинг — рассматривать как долгосрочную цель.
