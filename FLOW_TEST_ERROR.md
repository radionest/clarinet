# Flaky Pipeline Tests in Parallel Mode (`pytest -n auto`)

## Симптомы

При `make test-fast` (`pytest -n auto`, 16 воркеров) от 2 до 11 pipeline-тестов падают нестабильно.
Набор падающих тестов **меняется каждый запуск**. При последовательном запуске или запуске pipeline-тестов отдельно — 0 ошибок.

Типичные ошибки:
- `AssertionError: No chain_failure message in DLQ` — сообщение не успело дойти
- `AssertionError: Expected multiple calls, got 0` — middleware не вызвался
- `Cannot send task to the queue` — брокер не может отправить в очередь
- `ChannelNotFoundEntity` → каскад `CancelledError` — из `_purge_test_queues` при `passive=True`

## Корневые причины

### 1. `_purge_test_queues` использует `passive=True` для несуществующих очередей

`tests/integration/conftest.py:264-277` — фикстура purge делает `channel.declare_queue(queue_name, passive=True)`.
Если очередь ещё не создана, AMQP возвращает `NOT_FOUND` и **закрывает канал**.
Последующие итерации цикла падают с `ChannelClosed` → каскад `CancelledError`.

### 2. Параллельное давление на RabbitMQ

16 воркеров одновременно создают exchange + queues каждый → RabbitMQ `INTERNAL_ERROR (541)`.
Pipeline-тесты timing-sensitive (ждут сообщения из очередей с таймаутами 5-10 сек).
Когда воркер перегружен другими тестами, event loop не успевает обработать AMQP-ответы.

### 3. `--dist load` не учитывает `xdist_group`

Маркер `pytest.mark.xdist_group("pipeline")` работает **только** с `--dist loadgroup`.
Makefile использовал `pytest -n auto` (дефолт `--dist load`), поэтому группировка не применялась.

## Что было сделано

### Изменение 1: `xdist_group("pipeline")` — маркеры добавлены

**`tests/integration/test_pipeline_integration.py:25-30`**:
```python
pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.asyncio,
    pytest.mark.xdist_group("pipeline"),  # ADDED
    pytest.mark.usefixtures("_check_rabbitmq", "_purge_test_queues", "_clear_pipeline_registries"),
]
```

**`tests/e2e/test_pipeline_workflow.py`** — добавлен `xdist_group` к 3 классам:
- `TestPipelineTaskDispatch` (line 370)
- `TestPipelineWithRecordLifecycle` (line 434)
- `TestPipelineBrokerConnectivity` (line 493)

**`tests/integration/test_app_startup.py`** — добавлен `xdist_group` к `test_startup_pipeline_enabled` (line 119).

### Изменение 2: `--dist loadgroup` в Makefile

**`Makefile:128,133`** — заменено `pytest -n auto` → `pytest -n auto --dist loadgroup` для `test-fast` и `test-unit`.

### Изменение 3: Устойчивый `_purge_test_queues`

**`tests/integration/conftest.py:264-282`** — при `ChannelNotFoundEntity` (или любом exception) переоткрываем канал:
```python
except Exception:
    # Queue may not exist yet (passive=True fails) or channel
    # closed after NOT_FOUND — reopen channel for next iteration
    try:
        channel = await connection.channel()
    except Exception:
        break
```

## Текущий результат

| Сценарий | До | После |
|---|---|---|
| Pipeline-тесты отдельно (`-k pipeline`) | 0-5 failures | 0 failures (стабильно) |
| Полный прогон (`make test-fast`) | 3-11 pipeline failures | 0-4 pipeline failures |

Улучшение значительное, но **полная стабильность в полном прогоне не достигнута**.

## Оставшаяся проблема

Даже с `loadgroup`, pipeline-тесты на воркере `gw0` конкурируют с другими тестами за event loop.
Timing-sensitive тесты (chain, DLQ, middleware logging) иногда не укладываются в таймауты.

## Возможные дальнейшие шаги

### Вариант A: Увеличить таймауты в timing-sensitive тестах
Найти тесты с `wait_seconds=5.0` / `asyncio.wait_for(..., timeout=...)` и увеличить до 15-30 сек.
Файлы: `tests/integration/test_pipeline_integration.py` (функция `_get_message_from_queue`, тесты chain/DLQ).

### Вариант B: Выделить pipeline в отдельный воркер через `-n` ограничение
Использовать `pytest-xdist` worker count = группы, чтобы pipeline-воркер не был перегружен.

### Вариант C: `forked` scope для pipeline session fixtures
Убедиться, что session-scoped fixtures (`test_exchange`, `test_queues`, `_cleanup_orphaned_test_resources`)
не конфликтуют между integration и e2e conftest на одном воркере.

### Вариант D: Не использовать `passive=True` в `_delete_e2e_test_resources`
`tests/e2e/conftest.py:148-182` — аналогичная проблема с `passive=True` в session finalizer.
Хотя это teardown, закрытый канал может повлиять на следующие операции.

## Файлы затронутые изменениями

1. `tests/integration/test_pipeline_integration.py` — `xdist_group("pipeline")`
2. `tests/e2e/test_pipeline_workflow.py` — `xdist_group("pipeline")` на 3 классах
3. `tests/integration/test_app_startup.py` — `xdist_group("pipeline")`
4. `tests/integration/conftest.py` — resilient `_purge_test_queues`
5. `Makefile` — `--dist loadgroup`

## Slicer-тесты (не связано)

5-11 slicer-тестов всегда падают — это pre-existing проблема (нет запущенного Slicer).
Не связано с pipeline flakiness.
