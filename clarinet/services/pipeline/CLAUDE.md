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
| `message.py` | PipelineMessage (Pydantic model) |
| `chain.py` | Pipeline chain builder DSL (step-by-step, queue routing) |
| `middleware.py` | RetryMiddleware, DLQPublisher, PipelineChainMiddleware, PipelineLoggingMiddleware, DeadLetterMiddleware |
| `context.py` | TaskContext system: FileResolver (sync), RecordQuery (async), build_task_context() |
| `sync_wrappers.py` | SyncRecordQuery, SyncPipelineClient, SyncTaskContext — sync wrappers for thread-based tasks |
| `task.py` | `pipeline_task()` decorator factory — auto client lifecycle + TaskContext, sync/async auto-detect |
| `worker.py` | get_worker_queues() auto-detect, run_worker() entry point |
| `rabbitmq_cleanup.py` | Test resource cleanup via Management HTTP API (queues/exchanges) |

## Usage

### Define a pipeline

```python
from clarinet.services.pipeline import Pipeline, PipelineMessage

imaging_pipeline = (
    Pipeline("ct_segmentation")
    .step(fetch_dicom, queue="clarinet.dicom")
    .step(run_segmentation, queue="clarinet.gpu")
    .step(generate_report, queue="clarinet.default")
)
```

### Define a task

Use `@pipeline_task()` — it handles PipelineMessage parsing, ClarinetClient lifecycle,
and TaskContext construction automatically:

```python
from clarinet.services.pipeline import pipeline_task, PipelineMessage, TaskContext

@pipeline_task(queue="clarinet.dicom")
async def fetch_dicom(msg: PipelineMessage, ctx: TaskContext):
    path = ctx.files.resolve("dicom_source")
    # ... fetch DICOM data using ctx.client, ctx.records ...
    await ctx.client.update_record_data(msg.record_id, {"status": "fetched"})
```

The decorator also auto-registers the task in `_TASK_REGISTRY` (no manual `register_task()` needed).
Retries are enabled by default (3x, exponential backoff + jitter). Extra kwargs are forwarded
to `broker.task()`.

**Sync task support**: `pipeline_task()` auto-detects sync handlers via `inspect.iscoroutinefunction`.
Sync handlers run in a thread (`asyncio.to_thread()`) and receive `SyncTaskContext` with
sync wrappers (`SyncRecordQuery`, `SyncPipelineClient`) instead of async originals.

```python
from clarinet.services.pipeline import pipeline_task, PipelineMessage, SyncTaskContext

@pipeline_task()
def my_cpu_bound_task(msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    records = ctx.records.find("ct_seg", series_uid=msg.series_uid)
    ctx.client.submit_record_data(msg.record_id, {"done": True})
```

**`auto_submit` parameter**: When `auto_submit=True` and handler returns a `dict`, the decorator
automatically calls `client.submit_record_data(msg.record_id, result)`. Runs before file
change detection. Skipped for non-dict results or when `record_id` is None.

```python
@pipeline_task(auto_submit=True)
def compare_task(msg: PipelineMessage, ctx: SyncTaskContext) -> dict:
    return {"score": 0.95}  # auto-submitted
```

**Automatic file change detection**: After successful task execution, the wrapper computes
checksums for all files accessed via `ctx.files` (resolve/exists/glob) and compares them
with pre-task snapshots. Changed files are reported to `POST /patients/{id}/file-events`,
which triggers RecordFlow file flows (e.g. `file(master_model).on_update().invalidate_all_records(...)`).

**Legacy pattern** — `@broker.task` still works for simple tasks that don't need TaskContext:

```python
broker = get_broker()

@broker.task
async def simple_task(msg: dict) -> dict:
    message = PipelineMessage.model_validate(msg)
    # ...
    return message.model_dump()
```

For standalone `@broker.task` tasks not added via `.step()`, call `register_task(simple_task)`
explicitly so `PipelineChainMiddleware` can dispatch them by name.

### Execute from RecordFlow

```python
record('ct_scan').on_status('finished').pipeline('ct_segmentation')
```

### Run a worker

```bash
uv run clarinet worker                        # auto-detect queues
uv run clarinet worker --queues default gpu   # explicit queues
uv run clarinet worker --workers 4            # parallel workers
uv run clarinet worker --dicom WORKER:4006    # with Storage SCP for C-MOVE
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

Pipeline definitions are stored in the `pipeline_definition` DB table (model: `clarinet/models/pipeline_definition.py`).
Core sync logic lives in `persist_definitions(repo)` — iterates the registry and upserts each definition.
At startup, `sync_pipeline_definitions()` wraps it with a db_manager session (bootstrap pattern).
The `POST /api/pipelines/sync` endpoint calls `persist_definitions` directly with the DI-provided repo.
`Pipeline.run()` only dispatches the first step — no DB writes.
Task labels carry only `pipeline_id` + `step_index` (no serialized chain).
After each step, `PipelineChainMiddleware.post_execute()` fetches the definition from the HTTP API
(`GET /api/pipelines/{name}/definition`) and dispatches the next step. Chain stops on error.


## Built-in Tasks

Located in `tasks/` — auto-imported when broker starts:
- `convert_series_to_nifti` (queue `clarinet.dicom`) — downloads DICOM via C-GET, converts to NIfTI with correct affine/spacing. Requires `msg.series_uid`. Idempotent (skips if output exists). Output: `VOLUME_NIFTI` FileDef, level=SERIES.
- `prefetch_dicom_web` (queue `clarinet.dicom`) — prefetches a study into the DICOMweb disk cache via direct C-GET to `{storage_path}/dicomweb_cache/{study}/{series}/`. Requires `msg.study_uid`. Bypasses the API memory tier. Idempotent (skips series with valid disk cache or `dcm_anon/` copy). Payload knob: `skip_if_anon` (default `True`).

Task name collision: `register_task()` in `chain.py` prevents project tasks from shadowing built-in tasks (identity check `existing is not task` → `PipelineConfigError`).

### auto_submit

`@pipeline_task(auto_submit=True)` — if handler returns a `dict`, automatically calls `client.submit_record_data(msg.record_id, result)` before file-change detection. Skipped when `record_id` is None.

## Retry & DLQ

- **Retries enabled by default** via `RetryMiddleware` with `default_retry_label=True`
- 3 retries, exponential backoff + jitter, max delay 120s (all configurable via settings)
- **Business errors (4xx) are never retried.** `RetryMiddleware` (extends `SmartRetryMiddleware`) checks `ClarinetAPIError.status_code` — if 400–499, the retry is skipped and the error goes straight to DLQ. Rationale: HTTP 4xx means a business-logic violation (409 Conflict, 404 Not Found, 422 Validation Error) that will fail identically on every attempt. Retrying wastes time and delays error visibility. 5xx errors and non-HTTP exceptions (`ConnectionError`, `TimeoutError`) are retried normally.
- **`DLQPublisher`** — shared AMQP connection to `clarinet.dead_letter`. One instance is created in `create_broker()` and passed to both `DeadLetterMiddleware` and `PipelineChainMiddleware` (composition pattern). Lifecycle owned by `DeadLetterMiddleware`.
- **`DeadLetterMiddleware`** routes terminal failures to DLQ after all retries are exhausted
- `RetryMiddleware` sets `NoResultError` on retry; DeadLetterMiddleware skips those and only publishes real errors to DLQ
- **`pipeline_ack_type`** controls when messages are acknowledged (default `when_executed` — message redelivered if worker crashes)
- Middleware order: Retry → Logging → DeadLetter → Chain (DeadLetter must be before Chain so DLQPublisher is started before chain middleware needs it)

## TaskContext System

`pipeline_task()` decorator provides `TaskContext` with: `files` (FileResolver), `records` (RecordQuery), `client` (ClarinetClient), `msg` (PipelineMessage). Sync tasks get `SyncTaskContext` with sync wrappers.

`build_task_context(msg, client)` fallback: record_id → series_uid → study_uid → empty context.

`@broker.task()` still works for simple tasks without TaskContext.

## RecordFlow Integration

`PipelineAction` in `flow_action.py` dispatches a pipeline from a RecordFlow trigger.
The engine builds a `PipelineMessage` from the record context and calls `pipeline.run()`.

Settings, testing, and dependencies: `.claude/rules/pipeline-ops.md` (auto-loaded for pipeline files).
