---
paths:
  - "clarinet/services/pipeline/**"
  - "tests/**/*pipeline*"
---

# Pipeline — Settings & Testing Reference

## Settings

| Setting | Default | Description |
|---|---|---|
| `pipeline_enabled` (bool) | — | Enable broker in app lifespan |
| `pipeline_result_backend_url` (str \| None) | — | Redis URL; if set, attaches `RedisAsyncResultBackend` enabling `task.wait_result()` |
| `pipeline_worker_prefetch` (int) | — | Max tasks per worker |
| `pipeline_default_timeout` (int) | — | Task timeout in seconds |
| `pipeline_retry_count` (int) | 3 | Max retries for failed tasks (only infrastructure errors — 4xx are never retried) |
| `pipeline_retry_delay` (int) | 5 | Initial retry delay in seconds |
| `pipeline_retry_max_delay` (int) | 120 | Max retry delay with exponential backoff |
| `pipeline_ack_type` (AcknowledgeType) | `when_executed` | `when_received` \| `when_executed` \| `when_saved` |
| `PYTEST_WORKERS` (env, Makefile) | 10 | Max xdist workers; override: `PYTEST_WORKERS=4 make test-fast` |

## Testing

Use `InMemoryBroker` for unit tests:
```python
from clarinet.services.pipeline import get_test_broker
broker = get_test_broker()
```

Unit tests: `tests/test_pipeline.py`, `tests/test_pipeline_context.py`

Integration tests: `tests/integration/test_pipeline_integration.py` (18 tests, real RabbitMQ on klara `192.168.122.151`)
- `pytest.mark.pipeline` marker — auto-skips when RabbitMQ unreachable
- Run: `uv run pytest -m pipeline -v` or `make test-integration`
- Fixtures in `tests/integration/conftest.py`: `pipeline_broker_factory`, `_check_rabbitmq`, `_purge_test_queues`, `_cleanup_orphaned_test_resources`
- Test queues created with `x-expires: 3600000` (1h) — auto-deleted by RabbitMQ if abandoned
- Pre-session cleanup fixture deletes orphaned test resources via Management HTTP API
- CLI: `uv run clarinet rabbitmq clean` / `--dry-run` / `uv run clarinet rabbitmq status`
- Makefile: `make clean-rabbitmq`

## Dependencies

Optional group `pipeline` in `pyproject.toml`:
- `taskiq>=0.11.0`
- `taskiq-aio-pika>=0.4.0`
- `taskiq-redis>=1.0.0`

## Queue Namespacing

Queue names are derived from `settings.pipeline_task_namespace` (which is normalized
from `settings.project_name`):

| Setting property | Default (`project_name="Clarinet"`) | Custom (`project_name="Liver"`) |
|---|---|---|
| `settings.default_queue_name` | `clarinet.default` | `liver.default` |
| `settings.gpu_queue_name` | `clarinet.gpu` | `liver.gpu` |
| `settings.dicom_queue_name` | `clarinet.dicom` | `liver.dicom` |
| `settings.dlq_queue_name` | `clarinet.dead_letter` | `liver.dead_letter` |

Tasks should use these properties (`settings.dicom_queue_name`) instead of hard-coded
strings (`"clarinet.dicom"`) — otherwise multi-project deployments collide on the same
RabbitMQ queue.

`routing_key = full queue name` — guarantees no cross-project collisions on a shared
exchange.  Each queue gets its own broker via `get_broker_for(queue_name)`; tasks are
bound to their broker at decoration time, so `task.kicker().kiq()` always publishes to
the right queue.

## Anonymized UID contract

`FileResolver.build_working_dirs*` and the writer/reader helpers
(`build_context` → `render_working_folder`) refuse to render a path
against a raw UID. When `anon_uid` / `anon_id` is missing they raise
`AnonPathError` (re-exported from `clarinet.exceptions`). The
`RetryMiddleware` 4xx skip only fires for `ClarinetAPIError`, so an
`AnonPathError` from a worker is retried `pipeline_retry_count` times
with exponential backoff before `DeadLetterMiddleware` routes it to
the DLQ — usually the right shape, since the race window closes once
the anonymization run finishes and a retry succeeds.

This is desirable: an asymmetric anonymization run (PR #250) can flip
a study from non-anon → anon mid-pipeline, and silently falling back
to the raw UID made downstream tasks load files the writer no longer
produces under that identifier. If the task genuinely needs to address
the unanonymized layout — e.g. a UX-side preview generator — opt in
explicitly with `FileResolver.build_working_dirs(record, fallback_to_unanonymized=True)`.

Reading the `record.working_folder` field (computed on `RecordRead`)
already uses the UX fallback so API responses keep serialising on
non-anon records. Backend logic must NOT use that value; call
`FileResolver.build_working_dirs(record)` directly.

## Built-in Tasks

Registered in `clarinet/services/pipeline/tasks/` and `clarinet/services/dicom/pipeline.py` — imported at broker startup.
- `convert_series_to_nifti` — C-GET DICOM series → NIfTI conversion. Queue: `settings.dicom_queue_name`. Requires `msg.series_uid`. Idempotent (skips if `volume.nii.gz` exists). Output: `VOLUME_NIFTI` FileDef (level=SERIES).
- `prefetch_dicom_web` — prefetch a study into the DICOMweb disk cache via direct C-GET to `{storage_path}/dicomweb_cache/{study}/{series}/`. Queue: `settings.dicom_queue_name`. Requires `msg.study_uid`. Bypasses API memory tier. Idempotent (skips series with valid disk cache or `dcm_anon/` copy). Payload: `skip_if_anon` (default `True`).
- `anonymize_study_pipeline` — Record-aware DICOM anonymization (skip-guard + Patient anonymize + DICOM anonymize + submit to Record). Queue: `settings.dicom_queue_name`. Requires `msg.record_id`. Payload knobs: `save_to_disk`, `send_to_pacs`. Downstream wraps via `run_anonymization(msg, ctx, extra_record_data=...)` to add project-specific Record fields.

Task name collision: `register_task()` in `chain.py` prevents project tasks from shadowing built-in tasks (identity check `existing is not task` → `PipelineConfigError`).
