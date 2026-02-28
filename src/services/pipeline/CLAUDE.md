# Pipeline Service — Distributed Task Queue

TaskIQ-based distributed task pipeline for long-running operations (GPU processing, DICOM chains, multi-step workflows).

## Architecture

- **TaskIQ** as task queue (not FastStream) — built-in retry, DLQ, FastAPI DI compatibility
- **AioPikaBroker** connects to RabbitMQ via existing `settings.rabbitmq_*` configuration
- **Direct exchange** (`clarinet`) with queue-based routing (`clarinet.default`, `clarinet.gpu`, `clarinet.dicom`)
- **PipelineChainMiddleware** advances multi-step pipelines via DB-backed definitions (HTTP API lookup)

## Module Structure

| File | Purpose |
|------|---------|
| `__init__.py` | Public API: Pipeline, PipelineMessage, get_pipeline, etc. |
| `broker.py` | `get_broker()` singleton + `create_broker(queue)` per-queue factory, middlewares, result backend |
| `message.py` | PipelineMessage, PipelineResult (Pydantic models) |
| `chain.py` | Pipeline chain builder DSL (step-by-step, queue routing) |
| `middleware.py` | PipelineChainMiddleware, PipelineLoggingMiddleware, DeadLetterMiddleware |
| `worker.py` | get_worker_queues() auto-detect, run_worker() entry point |
| `exceptions.py` | Re-exports from `src.exceptions.domain` |

## Usage

### Define a pipeline

```python
from src.services.pipeline import Pipeline, PipelineMessage

imaging_pipeline = (
    Pipeline("ct_segmentation")
    .step(fetch_dicom, queue="clarinet.dicom")
    .step(run_segmentation, queue="clarinet.gpu")
    .step(generate_report, queue="clarinet.default")
)
```

### Define a task

```python
from src.services.pipeline import get_broker, register_task

broker = get_broker()

@broker.task  # retries enabled by default (3x, exponential backoff + jitter)
async def fetch_dicom(msg: dict) -> dict:
    message = PipelineMessage(**msg)
    # ... fetch DICOM data ...
    return message.model_dump()
```

Tasks added via `.step()` are auto-registered in `_TASK_REGISTRY`. For standalone tasks not
used in a pipeline, call `register_task(fetch_dicom)` explicitly so `PipelineChainMiddleware`
can dispatch them by name.

### Execute from RecordFlow

```python
record('ct_scan').on_status('finished').pipeline('ct_segmentation')
```

### Run a worker

```bash
uv run clarinet worker                        # auto-detect queues
uv run clarinet worker --queues default gpu   # explicit queues
uv run clarinet worker --workers 4            # parallel workers
```

## Queue Routing

- Exchange: `clarinet` (direct type)
- Routing key convention: `clarinet.gpu` → routing key `gpu`
- Default queue: `clarinet.default` (all workers)
- GPU queue: `clarinet.gpu` (workers with `have_gpu=True`)
- DICOM queue: `clarinet.dicom` (workers with `have_dicom=True`)
- Workers call `create_broker(queue_name)` per queue — each gets its own exchange/queue binding.
  `get_broker()` returns the default singleton used for task dispatch in the application.

## Chain Advancement (DB-backed)

Pipeline definitions are stored in the `pipeline_definition` DB table (model: `src/models/pipeline_definition.py`).
Definitions are synced to the database at application startup via `sync_pipeline_definitions()`
(bootstrap pattern, same as `add_default_user_roles`). Can also be synced on demand via
`POST /api/pipelines/sync`. `Pipeline.run()` only dispatches the first step — no DB writes.
Task labels carry only `pipeline_id` + `step_index` (no serialized chain).
After each step, `PipelineChainMiddleware.post_execute()` fetches the definition from the HTTP API
(`GET /api/pipelines/{name}/definition`) and dispatches the next step. Chain stops on error.


## Retry & DLQ

- **Retries enabled by default** via `SmartRetryMiddleware` with `default_retry_label=True`
- 3 retries, exponential backoff + jitter, max delay 120s (all configurable via settings)
- **`DeadLetterMiddleware`** routes terminal failures to `clarinet.dead_letter` queue after all retries are exhausted
- SmartRetryMiddleware sets `NoResultError` on retry; DeadLetterMiddleware skips those and only publishes real errors to DLQ
- **`pipeline_ack_type`** controls when messages are acknowledged (default `when_executed` — message redelivered if worker crashes)
- Middleware order: SmartRetry → Logging → DeadLetter → Chain

## Settings

- `pipeline_enabled` (bool) — enable broker in app lifespan
- `pipeline_result_backend_url` (str | None) — Redis URL; if set, attaches `RedisAsyncResultBackend` enabling `task.wait_result()`
- `pipeline_worker_prefetch` (int) — max tasks per worker
- `pipeline_default_timeout` (int) — task timeout in seconds
- `pipeline_retry_count` (int, default 3) — max retries for failed tasks
- `pipeline_retry_delay` (int, default 5) — initial retry delay in seconds
- `pipeline_retry_max_delay` (int, default 120) — max retry delay with exponential backoff
- `pipeline_ack_type` (str, default `when_executed`) — `when_received` | `when_executed` | `when_saved`


## RecordFlow Integration

`PipelineAction` in `flow_action.py` dispatches a pipeline from a RecordFlow trigger.
The engine builds a `PipelineMessage` from the record context and calls `pipeline.run()`.

## Testing

Use `InMemoryBroker` for unit tests:
```python
from src.services.pipeline import get_test_broker
broker = get_test_broker()
```

Unit tests: `tests/test_pipeline.py`

Integration tests: `tests/integration/test_pipeline_integration.py` (18 tests, real RabbitMQ on klara `192.168.122.151`)
- `pytest.mark.pipeline` marker — auto-skips when RabbitMQ unreachable
- Run: `uv run pytest -m pipeline -v` or `make test-integration`
- Fixtures in `tests/integration/conftest.py`: `pipeline_broker_factory`, `_check_rabbitmq`, `_purge_test_queues`

## Dependencies

Optional group `pipeline` in `pyproject.toml`:
- `taskiq>=0.11.0`
- `taskiq-aio-pika>=0.4.0`
- `taskiq-redis>=1.0.0`
