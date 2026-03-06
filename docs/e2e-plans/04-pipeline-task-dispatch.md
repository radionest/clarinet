# E2E Plan: Pipeline Task Dispatch → Execution → Result

## File

`tests/e2e/test_pipeline_workflow.py`

## Goal

Test the complete pipeline workflow: register pipeline definitions, sync them to
the database, retrieve definitions via API, dispatch a task through the broker,
verify execution, and confirm the result persists back via the API. Also test
the pipeline router endpoints and the interaction between pipeline and
record status changes.

## Markers & Conditions

```python
pytestmark = [pytest.mark.pipeline]
```

All tests auto-skip when RabbitMQ is unreachable.

## Fixtures

### Shared (from conftest)

| Fixture | Purpose |
|---|---|
| `client` | Authenticated `AsyncClient` (superuser, auth bypass) |
| `test_session` | Async SQLAlchemy session with auto-rollback |

### New (local to test file)

| Fixture | Purpose |
|---|---|
| `broker_available` | Session-scoped; skips if RabbitMQ unreachable |
| `test_pipeline` | Registers a test `Pipeline` with 2 steps in-memory |
| `synced_pipeline` | Calls `persist_definitions(repo)` to sync `test_pipeline` to DB |
| `test_record_type` | `RecordType` to associate with pipeline tasks |
| `test_record` | `Record` in `pending` status |
| `_reset_pipeline_registry` | Autouse; saves/restores the pipeline registry between tests |

### Client Override

Re-override `client` — authenticated superuser (same as `test_demo_processing.py`).

## Mocking Strategy

- **RabbitMQ broker**: use real broker (auto-skip if unavailable).
- **Pipeline workers**: for dispatch-only tests, don't start workers (just verify
  message is enqueued). For execution tests, start a temporary worker in-process.
- **RecordFlow engine**: `app.state.recordflow_engine = None` (isolate pipeline
  from record flows).
- **`_reset_singletons`**: save/restore module-level broker singleton to prevent
  cross-test pollution (pattern from `test_app_startup.py`).

## Data Setup

### Test Pipeline Definition

```python
from src.services.pipeline import Pipeline, PipelineStep

test_pipeline = Pipeline(
    name="test_e2e_pipeline",
    steps=[
        PipelineStep(
            name="preprocess",
            queue="default",
            handler="tests.e2e.helpers.mock_preprocess",
        ),
        PipelineStep(
            name="analyze",
            queue="default",
            handler="tests.e2e.helpers.mock_analyze",
        ),
    ],
)
```

### PipelineDefinition in DB

After `POST /api/pipelines/sync`, the `PipelineDefinition` table should contain:
- `name="test_e2e_pipeline"`, with 2 `PipelineStep` rows.

## Test Classes & Scenarios

### `TestPipelineDefinitionEndpoints`

1. **`test_sync_definitions`**
   - Register test pipeline in memory
   - `POST /api/pipelines/sync`
   - Assert: 200, `{"synced": N}` where N >= 1
   - DB check: `PipelineDefinition(name="test_e2e_pipeline")` exists

2. **`test_get_pipeline_definition`**
   - `GET /api/pipelines/test_e2e_pipeline/definition`
   - Assert: 200, response contains `name`, `steps` list
   - Assert: steps have correct `name`, `queue`, `handler`

3. **`test_get_nonexistent_definition_returns_404`**
   - `GET /api/pipelines/nonexistent/definition`
   - Assert: 404

4. **`test_sync_updates_existing_definition`**
   - Modify test pipeline (add a step)
   - `POST /api/pipelines/sync` again
   - `GET /api/pipelines/test_e2e_pipeline/definition`
   - Assert: steps list reflects the updated pipeline

5. **`test_sync_idempotent`**
   - Call `POST /api/pipelines/sync` twice with no changes
   - Assert: both calls succeed, definition unchanged

### `TestPipelineTaskDispatch`

Precondition: RabbitMQ reachable, pipeline definitions synced.

6. **`test_dispatch_task_to_broker`**
   - Import the pipeline's task function
   - Call `task.kiq(...)` to dispatch
   - Assert: message enqueued successfully (no error)
   - Verify: message appears in the queue (via broker management API or consumer)

7. **`test_dispatch_with_chain_middleware`**
   - Create a 2-step pipeline chain
   - Dispatch first step
   - Assert: `PipelineChainMiddleware` attaches `pipeline_name` and `step_index` labels

8. **`test_task_result_updates_record_via_api`**
   - Dispatch a task that calls back the API to update a record's status
   - Worker processes the task
   - DB check: record status changed (e.g., `pending → inwork`)
   - API check: `GET /api/records/{id}` confirms new status

### `TestPipelineWithRecordLifecycle`

9. **`test_anonymization_via_pipeline`**
    - Record at `pending` status, anonymization task dispatched
    - `POST /api/dicom/studies/{uid}/anonymize?background=true`
    - Assert: 202
    - (If worker running) verify anonymization completes

10. **`test_pipeline_task_failure_handling`**
    - Dispatch a task with a handler that raises an exception
    - Assert: task is marked as failed in broker
    - Record status does not change (no callback on failure)

### `TestPipelineBrokerConnectivity`

11. **`test_broker_startup_and_shutdown`**
    - Start broker via `app.state.pipeline_broker`
    - Verify connectivity (ping or health check)
    - Shutdown broker
    - Verify clean shutdown (no hanging connections)

12. **`test_broker_reconnection`**
    - Start broker, verify connectivity
    - Simulate brief disconnect (if possible)
    - Verify broker reconnects and tasks can be dispatched

## Assertions Checklist

- [ ] `POST /pipelines/sync` persists definitions to DB
- [ ] `GET /pipelines/{name}/definition` returns correct structure
- [ ] 404 for nonexistent pipeline
- [ ] Sync is idempotent and handles updates
- [ ] Task dispatch succeeds via broker
- [ ] Chain middleware attaches correct labels
- [ ] Task execution updates records via API callback
- [ ] Background anonymization returns 202
- [ ] Task failures are handled gracefully
- [ ] Broker startup/shutdown is clean

## Dependencies

- RabbitMQ running (auto-skip if unavailable)
- Test pipeline handlers (simple mock functions in test helpers)
