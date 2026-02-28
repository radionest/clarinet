# Pipeline Service — Аудит DRY / KISS / YAGNI

## Общая оценка

Сервис хорошо структурирован: чистое разделение на broker, chain DSL, middleware, worker, message models. Однако есть конкретные нарушения принципов, часть из которых — точки реального технического долга.

---

## DRY — Нарушения


---

## KISS — Нарушения

### 5. DeadLetterMiddleware — новое подключение к RabbitMQ на каждый сбой (критично)

`middleware.py:119` — `_publish_to_dlq()` создает **новое aio_pika соединение** при каждом вызове:

```python
connection = await aio_pika.connect_robust(_build_amqp_url())
```

Проблемы:
- Дорогая операция (TCP handshake + AMQP auth) на каждый failure
- Нет connection pooling
- При каскадном сбое (много задач падают одновременно) — шторм подключений
- Middleware работает внутри worker-процесса, где уже есть активное соединение broker'а

**Рекомендация**: использовать встроенный механизм RabbitMQ DLQ через `x-dead-letter-exchange` / `x-dead-letter-routing-key` аргументы при декларации очередей в `create_broker()`. Это полностью исключает необходимость в `DeadLetterMiddleware` как классе и переносит маршрутизацию на уровень брокера. Альтернатива — получать `channel` из broker'а, а не создавать своё подключение.



## YAGNI — Мёртвый/ненужный код

### 8. `PipelineResult` — модель нигде не используется в рантайме

`message.py:41-54` — определена, экспортирована в `__init__.py:37`, но:
- Задачи возвращают `dict` (`msg.model_dump()`), не `PipelineResult`
- `PipelineChainMiddleware` ожидает `dict | PipelineMessage`, не `PipelineResult`
- Единственное использование — тесты самой модели (`test_pipeline.py:90-105`)

**Рекомендация**: удалить `PipelineResult`, убрать из экспортов и тестов.

### 9. `exceptions.py` — файл-прокси без логики

```python
from src.exceptions.domain import PipelineConfigError, PipelineError, PipelineStepError
```

14 строк, единственная роль — реэкспорт. Все реальные потребители (тесты) и так импортируют из `src.exceptions.domain`. Файл не добавляет ценности.

**Рекомендация**: удалить файл, в `__init__.py` импортировать напрямую из `src.exceptions.domain`.

### 10. `register_task()` — публичная функция без внешних вызовов

`chain.py:168-177` — экспортируется, но нигде не вызывается. Задачи регистрируются автоматически через `.step()`.

**Рекомендация**: убрать из публичного API (`__init__.py`). Оставить как внутреннюю, если нужна.

### 11. `get_all_pipelines()` — только в тестах

`chain.py:192-198` — используется только в `tests/test_pipeline.py`.

**Рекомендация**: оставить (полезна для отладки/introspection), но не критична.

### 12. `DeadLetterMiddleware` в публичном `__init__.py`

Middleware используется только внутри `broker.py:76-88`. Экспорт наружу не нужен.

**Рекомендация**: убрать из `__all__` в `__init__.py`.

---

## Запуск и взаимодействие — Замечания

### 13. API-процесс vs Worker-процесс — разрыв в инициализации

| | API-процесс (`app.py`) | Worker-процесс (`run_worker()`) |
|---|---|---|
| Broker | Singleton через `get_broker()` | Singleton + per-queue копии |
| Startup | `await broker.startup()` | `await broker.startup()` для каждого |
| Task loading | **Нет** — flow-файлы не загружаются | `_load_task_modules()` |
| Роль | Только отправка (dispatch) | Приём и выполнение |

Это корректно: API-серверу не нужны зарегистрированные задачи — он только отправляет сообщения через `pipeline.run()`. Но этот контракт нигде не задокументирован, и `_TASK_REGISTRY` в API-процессе будет пуст, что может ввести в заблуждение.

**Рекомендация**: добавить комментарий в `app.py` у запуска broker'а о том, что API использует broker только для dispatch, task registry заполняется только в worker.

### 14. `_ACK_TYPE_MAP` определяется внутри функции

`worker.py:113-117` — константа создаётся при каждом вызове `run_worker()`.

**Рекомендация**: вынести на уровень модуля.

### 15. Worker's per-queue broker копирует task registry вручную

`worker.py:104-106`:
```python
qbroker = create_broker(queue_name)
for task_name, task in singleton.get_all_tasks().items():
    qbroker.local_task_registry[task_name] = task
```

Это работает, но хрупко — зависит от внутреннего API TaskIQ (`local_task_registry`). При обновлении TaskIQ может сломаться.

---

## Приоритеты

| # | Проблема | Принцип | Приоритет |
|---|----------|---------|-----------|
| 5 | DLQ — новое подключение на каждый сбой | KISS | **Высокий** |
| 6 | `exec()` для загрузки модулей | KISS | **Высокий** |
| 1 | Routing key extraction x3 | DRY | Средний |
| 2 | NoResultError проверка x2 | DRY | Средний |
| 4 | Дубль dispatch в engine.py | DRY | Средний |
| 7 | Chain сериализация в каждом label | KISS | Средний |
| 8 | PipelineResult мёртвый код | YAGNI | Низкий |
| 9 | exceptions.py прокси-файл | YAGNI | Низкий |
| 10 | register_task() без вызовов | YAGNI | Низкий |
| 3 | Log prefix дубль | DRY | Низкий |
| 12 | DeadLetterMiddleware в публичном API | YAGNI | Низкий |
| 14 | _ACK_TYPE_MAP внутри функции | KISS | Низкий |

## Файлы для модификации

- `src/services/pipeline/middleware.py` — рефакторинг DLQ, хелперы
- `src/services/pipeline/broker.py` — routing key хелпер, DLQ через queue args
- `src/services/pipeline/worker.py` — importlib, _ACK_TYPE_MAP
- `src/services/pipeline/chain.py` — routing key хелпер, registry lookup вместо chain serialization
- `src/services/pipeline/message.py` — удаление PipelineResult
- `src/services/pipeline/__init__.py` — чистка экспортов
- `src/services/pipeline/exceptions.py` — удаление файла
- `src/services/recordflow/engine.py` — объединение dispatch методов
- `src/api/app.py` — комментарий о роли broker в API
