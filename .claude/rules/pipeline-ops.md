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
| `pipeline_retry_count` (int) | 3 | Max retries for failed tasks |
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

## Built-in Tasks

Registered in `clarinet/services/pipeline/tasks/` — imported at broker startup.
- `convert_series_to_nifti` — C-GET DICOM series → NIfTI conversion. Queue: `clarinet.dicom`. Requires `msg.series_uid`. Idempotent (skips if `volume.nii.gz` exists). Output: `VOLUME_NIFTI` FileDef (level=SERIES).
- `prefetch_dicom_web` — prefetch a study into the DICOMweb disk cache via direct C-GET to `{storage_path}/dicomweb_cache/{study}/{series}/`. Queue: `clarinet.dicom`. Requires `msg.study_uid`. Bypasses API memory tier. Idempotent (skips series with valid disk cache or `dcm_anon/` copy). Payload: `skip_if_anon` (default `True`).

Task name collision: `register_task()` in `chain.py` prevents project tasks from shadowing built-in tasks (identity check `existing is not task` → `PipelineConfigError`).
