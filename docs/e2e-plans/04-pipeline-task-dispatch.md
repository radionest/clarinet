# E2E Plan: Pipeline Task Dispatch → Execution → Result

## File

`tests/e2e/test_pipeline_workflow.py`

## Goal

Test the complete pipeline workflow: register pipeline definitions, sync them to
the database, retrieve definitions via API, dispatch a task through the broker,
verify execution, and confirm the result persists back via the API. Also test
the pipeline router endpoints and the interaction between pipeline and
record status changes.

Additionally, test real-world pipeline patterns modelled after `examples/demo_liver/`:
RecordFlow-driven task dispatch (`.do_task()`), `@pipeline_task` with
`TaskContext` (file access, record queries, API callbacks), automatic file change
detection, and file-level invalidation cascades.

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
| `test_patient` | Pre-seeded `Patient(id="TEST_PAT001")` |
| `test_study` | Pre-seeded `Study(study_uid="1.2.3.4.5.6.7.8.9")` |

### New (local to test file)

| Fixture | Purpose |
|---|---|
| `broker_available` | Session-scoped; skips if RabbitMQ unreachable |
| `test_pipeline` | Registers a test `Pipeline` with 2 steps in-memory |
| `synced_pipeline` | Calls `persist_definitions(repo)` to sync `test_pipeline` to DB |
| `test_record_type` | `RecordType` to associate with pipeline tasks |
| `test_record` | `Record` in `pending` status |
| `_reset_pipeline_registry` | Autouse; saves/restores the pipeline registry between tests |
| `test_series` | `Series` linked to `test_study` for series-level operations |
| `record_types_liver` | Set of RecordType objects mimicking demo_liver (first_check, segment_CT_single, create_master_projection, compare_with_projection, second_review, update_master_model) |
| `liver_flow_registry` | Registers demo_liver-style RecordFlow rules + file flows; clears registries after test |
| `file_registry` | Registers file definitions (master_model, segmentation_single, master_projection) |
| `working_dirs` | Creates temp directories for PATIENT/STUDY/SERIES storage levels |
| `mock_worker` | Starts a temporary in-process worker that executes `@pipeline_task` functions |

### Client Override

Re-override `client` — authenticated superuser (same as `test_demo_processing.py`).

## Mocking Strategy

- **RabbitMQ broker**: use real broker (auto-skip if unavailable).
- **Pipeline workers**: for dispatch-only tests, don't start workers (just verify
  message is enqueued). For execution tests, start a temporary worker in-process.
- **RecordFlow engine**: `app.state.recordflow_engine = None` for isolated pipeline
  tests. For integration tests (§ RecordFlow-Driven, § Multi-Step Workflow), set
  `app.state.recordflow_engine` to a real `RecordFlowEngine` with test flows.
- **`_reset_singletons`**: save/restore module-level broker singleton to prevent
  cross-test pollution (pattern from `test_app_startup.py`).
- **File I/O**: `working_dirs` fixture creates temp directories; `@pipeline_task`
  FileResolver is patched to use them. No real DICOM/NIfTI files — use small
  dummy binary blobs to verify checksums and path resolution.
- **`image_processor`**: not available in test environment. Tasks that would call it
  (`init_master_model`, `compare_w_projection`) are replaced with lightweight
  mock implementations that write/read dummy files and produce deterministic
  comparison results.

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

### Demo-Liver RecordTypes

```python
# Minimal set replicating demo_liver/tasks/*.toml
record_types = [
    RecordType(name="first_check",               level="STUDY",   min_records=1, max_records=1),
    RecordType(name="segment_CT_single",          level="STUDY",   min_records=1, max_records=4, role="doctor_CT"),
    RecordType(name="create_master_projection",   level="SERIES",  min_records=1, max_records=1, role="expert"),
    RecordType(name="compare_with_projection",    level="SERIES",  min_records=1, max_records=1, role="auto"),
    RecordType(name="second_review",              level="SERIES",  min_records=1, max_records=1),
    RecordType(name="update_master_model",        level="PATIENT", min_records=1, max_records=1, role="expert"),
]
```

### Demo-Liver RecordFlow Rules

```python
from src.services.recordflow import record, study, file, Field as F

# Entity trigger
study().on_created().create_record("first_check")

# Conditional record creation
(record("first_check")
    .on_status("finished")
    .if_record(F.is_good == True, F.study_type == "CT")
    .create_record("segment_CT_single"))

# Task dispatch on record finish
record("segment_CT_single").on_status("finished").do_task(mock_init_master_model)

# Chain: segmentation → projection → comparison
record("segment_CT_single").on_status("finished").create_record("create_master_projection")
record("create_master_projection").on_status("finished").create_record("compare_with_projection")
record("compare_with_projection").on_status("pending").do_task(mock_compare_w_projection)

# Conditional outcomes from comparison
(record("compare_with_projection")
    .on_status("finished")
    .if_record(F.false_positive_num > 0)
    .create_record("update_master_model"))
(record("compare_with_projection")
    .on_status("finished")
    .if_record(F.false_negative_num > 0)
    .create_record("second_review"))

# File-level invalidation
file("master_model").on_update().invalidate_all_records("create_master_projection")
```

---

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

---

### `TestPipelineTaskDecorator`

Tests for `@pipeline_task()` decorator lifecycle — message parsing, context
creation, and automatic file change detection. Modelled after `demo_liver`
tasks (`init_master_model`, `compare_w_projection`).

13. **`test_pipeline_task_receives_parsed_message_and_context`**
    - Define a `@pipeline_task()` function that asserts `msg` is a
      `PipelineMessage` and `ctx` is a `TaskContext`
    - Dispatch with a valid `PipelineMessage` dict (patient_id, study_uid,
      series_uid, record_id)
    - Worker executes task
    - Assert: task completes without error; `ctx.files`, `ctx.records`,
      `ctx.client` are all initialized

14. **`test_pipeline_task_file_resolver`**
    - `@pipeline_task()` that calls `ctx.files.resolve(file_def)` and
      `ctx.files.exists(file_def)` for a registered file definition
    - Pre-create a dummy file at the expected path
    - Assert: `resolve()` returns the correct absolute path,
      `exists()` returns `True`
    - Remove the file; assert `exists()` returns `False`

15. **`test_pipeline_task_record_query`**
    - `@pipeline_task()` that calls
      `await ctx.records.find("segment_CT_single", series_uid=msg.series_uid)`
    - Pre-create a Record of type `segment_CT_single` in DB
    - Assert: query returns the expected record(s)

16. **`test_pipeline_task_api_callback`**
    - `@pipeline_task()` that calls
      `ctx.client.update_record_data(msg.record_id, {"result": "ok"})`
    - Assert: after task completes, `GET /api/records/{id}` shows
      `data.result == "ok"`
    - Pattern: mirrors `compare_w_projection` writing `false_negative`,
      `false_positive_num` to record data

17. **`test_pipeline_task_detects_file_changes`**
    - `@pipeline_task()` that writes a new file into `ctx.files` working dir
    - Assert: after task completes, `POST /patients/{id}/file-events` was
      called (verify via spy/mock on the client or check engine received
      `handle_file_update`)
    - Pattern: mirrors `init_master_model` creating `master_model.seg.nii`

18. **`test_pipeline_task_no_file_event_when_unchanged`**
    - `@pipeline_task()` that reads files but writes nothing
    - Assert: no file-event notification sent (checksums unchanged)

---

### `TestRecordFlowDrivenTaskDispatch`

Tests for the integration path: RecordFlow DSL `.do_task()` / `.pipeline()`
triggering pipeline task dispatch via engine. Requires a real
`RecordFlowEngine` with registered flows.

19. **`test_do_task_dispatches_on_record_status_change`**
    - Register flow: `record("segment_CT_single").on_status("finished").do_task(mock_task)`
    - Create a Record of type `segment_CT_single` with status `inwork`
    - `PATCH /api/records/{id}/status` → `finished`
    - Assert: mock task message enqueued in broker
    - Assert: `PipelineMessage` contains correct `record_id`, `patient_id`,
      `study_uid`, `series_uid`

20. **`test_do_task_auto_creates_single_step_pipeline`**
    - After registering `do_task(mock_task)`, check `_PIPELINE_REGISTRY`
    - Assert: pipeline `_task:{mock_task.__name__}` exists with 1 step
    - `POST /api/pipelines/sync` persists it
    - `GET /api/pipelines/_task:{name}/definition` returns valid definition

21. **`test_do_task_skipped_when_condition_false`**
    - Register flow with `.if_record(F.is_good == True).do_task(mock_task)`
    - Create Record with `data.is_good = False`, change status to `finished`
    - Assert: no task dispatched (broker queue empty or no message for this record)

22. **`test_do_task_fires_when_condition_true`**
    - Same flow as above
    - Create Record with `data.is_good = True`, change status to `finished`
    - Assert: task dispatched

23. **`test_pipeline_action_dispatches_named_pipeline`**
    - Register flow: `record("type").on_status("finished").pipeline("test_e2e_pipeline", extra="value")`
    - Trigger status change
    - Assert: first step of `test_e2e_pipeline` dispatched with `extra="value"`
      in payload

---

### `TestFileTriggersAndInvalidation`

Tests for `file().on_update().invalidate_all_records()` — the pattern from
`demo_liver` where master model file changes invalidate all projection records.

24. **`test_file_update_triggers_invalidation`**
    - Register flow: `file("master_model").on_update().invalidate_all_records("create_master_projection")`
    - Create 2 Records of type `create_master_projection` (different series) with
      status `finished`
    - Simulate file change: `POST /patients/{patient_id}/file-events`
      with `{"file_name": "master_model"}`
    - Assert: both records reset to `pending` (hard invalidation)
    - Assert: records have `context_info` explaining the invalidation reason

25. **`test_file_update_does_not_affect_other_record_types`**
    - Same setup + a Record of type `segment_CT_single` (status `finished`)
    - Trigger `master_model` file-event
    - Assert: `segment_CT_single` record unchanged (still `finished`)

26. **`test_pipeline_task_file_write_triggers_file_event`**
    - `@pipeline_task()` that creates a file matching `master_model` pattern
    - Register file flow: `file("master_model").on_update().invalidate_all_records("create_master_projection")`
    - Create a `create_master_projection` record (status `finished`)
    - Execute the task via worker
    - Assert: `@pipeline_task` wrapper detects checksum change → calls
      `POST /patients/{id}/file-events` → engine invalidates the projection record
    - End-to-end: task file write → automatic invalidation cascade

27. **`test_file_update_on_nonexistent_records_is_noop`**
    - Trigger `master_model` file-event when no `create_master_projection`
      records exist
    - Assert: no error, engine handles gracefully

---

### `TestConditionalRecordCreation`

Tests for `.if_record(F.field == val)` conditions and `.create_record()`
with multiple targets — patterns from demo_liver `first_check` branching.

28. **`test_conditional_create_record_on_matching_data`**
    - Register flow:
      ```python
      record("first_check").on_status("finished")
          .if_record(F.is_good == True, F.study_type == "CT")
          .create_record("segment_CT_single")
      ```
    - Create `first_check` Record with `data = {"is_good": True, "study_type": "CT"}`
    - Change status to `finished`
    - Assert: `segment_CT_single` Record created for the same study

29. **`test_conditional_create_record_skipped_on_non_matching_data`**
    - Same flow as above
    - Create `first_check` with `data = {"is_good": True, "study_type": "MRI"}`
    - Change status to `finished`
    - Assert: no `segment_CT_single` Record created

30. **`test_conditional_is_good_false_creates_nothing`**
    - Create `first_check` with `data = {"is_good": False, "study_type": "CT"}`
    - Change status to `finished`
    - Assert: no segmentation records created (AND semantics — both conditions must match)

31. **`test_create_multiple_records_from_single_trigger`**
    - Register flow:
      ```python
      record("first_check").on_status("finished")
          .if_record(F.is_good == True, F.study_type == "CT")
          .create_record("segment_CT_single", "segment_CT_with_archive")
      ```
    - Trigger with matching data
    - Assert: both `segment_CT_single` AND `segment_CT_with_archive` Records created

32. **`test_multiple_conditional_branches`**
    - Register separate flows for CT, MRI, CT-AG (different `.if_record()` conditions)
    - Trigger `first_check` finished with `study_type == "MRI"`
    - Assert: only `segment_MRI_single` created, not CT or CT-AG types

---

### `TestEntityCreationTriggers`

33. **`test_study_creation_triggers_record_creation`**
    - Register flow: `study().on_created().create_record("first_check")`
    - `POST /api/dicom/import-study` (or create Study via API)
    - Assert: `first_check` Record automatically created for the new study
    - Assert: Record has correct `study_uid` and `patient_id`

34. **`test_study_creation_does_not_duplicate_records`**
    - Same flow; import the same study twice (second should 409)
    - Assert: only 1 `first_check` Record exists

---

### `TestMultiStepWorkflow`

Integration tests exercising the full demo_liver chain:
`study creation → first_check → segmentation → task dispatch → projection →
comparison → conditional outcomes`. Requires real `RecordFlowEngine` +
real broker + mock worker.

35. **`test_full_chain_study_to_segmentation`**
    - Register full demo_liver flows (fixture `liver_flow_registry`)
    - Create a Study (triggers `first_check` creation)
    - Fill `first_check` data: `is_good=True, study_type="CT", best_series=...`
    - Change `first_check` status → `finished`
    - Assert chain:
      - `segment_CT_single` Record created
      - No `segment_MRI_single` (study_type != MRI)

36. **`test_segmentation_finish_dispatches_init_master_model`**
    - Continue from test 35: change `segment_CT_single` status → `finished`
    - Assert: `mock_init_master_model` task dispatched to broker
    - Assert: `create_master_projection` Record created for the same series

37. **`test_projection_finish_triggers_comparison_task`**
    - Continue chain: change `create_master_projection` status → `finished`
    - Assert: `compare_with_projection` Record created with status `pending`
    - Assert: `mock_compare_w_projection` task dispatched (triggered by
      `.on_status("pending").do_task(...)`)

38. **`test_comparison_with_false_positive_creates_update_master_model`**
    - Mock `compare_w_projection` writes
      `data = {"false_positive_num": 2, "false_negative_num": 0}`
    - Change `compare_with_projection` status → `finished`
    - Assert: `update_master_model` Record created (condition `F.false_positive_num > 0`)
    - Assert: no `second_review` Record (false_negative_num == 0)

39. **`test_comparison_with_false_negative_creates_second_review`**
    - Mock comparison writes `data = {"false_positive_num": 0, "false_negative_num": 3}`
    - Change `compare_with_projection` status → `finished`
    - Assert: `second_review` Record created
    - Assert: no `update_master_model` Record (false_positive_num == 0)

40. **`test_comparison_with_both_discrepancies_creates_both_records`**
    - Mock comparison writes `data = {"false_positive_num": 1, "false_negative_num": 2}`
    - Change status → `finished`
    - Assert: both `update_master_model` AND `second_review` Records created

41. **`test_comparison_clean_creates_no_followup`**
    - Mock comparison writes `data = {"false_positive_num": 0, "false_negative_num": 0}`
    - Change status → `finished`
    - Assert: neither `update_master_model` nor `second_review` created

42. **`test_master_model_update_invalidates_projections`**
    - Continue from test 38: after `update_master_model` completes, expert
      modifies `master_model` file → file-event triggered
    - Assert: all `create_master_projection` Records reset to `pending`
    - Assert: downstream `compare_with_projection` Records unaffected (only
      projections invalidated, comparison re-runs after new projection)

---

### `TestTaskContextIntegration`

End-to-end tests verifying `TaskContext` (FileResolver + RecordQuery) works
correctly when tasks are dispatched through the full pipeline.

43. **`test_task_context_resolves_patient_level_file`**
    - Dispatch task with `patient_id`, `study_uid`, `series_uid` in message
    - Task calls `ctx.files.resolve(master_model_def)` where
      `master_model` has `level="PATIENT"`
    - Assert: path is `{storage}/{patient_id}/master_model.seg.nii`

44. **`test_task_context_resolves_series_level_file_with_pattern`**
    - Task calls `ctx.files.resolve(segmentation_single_def, user_id="USR1")`
    - Assert: path is `{storage}/{patient_id}/{study_uid}/{series_uid}/segmentation_single_USR1.seg.nrrd`
    - Pattern `{user_id}` replaced correctly

45. **`test_task_context_glob_multiple_files`**
    - Pre-create `segmentation_single_USR1.seg.nrrd` and
      `segmentation_single_USR2.seg.nrrd` in working dir
    - Task calls `ctx.files.glob(segmentation_single_def)`
    - Assert: returns both file paths

46. **`test_task_writes_record_data_via_client`**
    - Pattern from `compare_w_projection`: task calls
      `ctx.client.update_record_data(record_id, payload)`
    - Assert: API reflects updated data fields (`false_negative`,
      `false_positive_num`, etc.)

---

## Assertions Checklist

### Basic Pipeline (tests 1–12)

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

### `@pipeline_task` Decorator (tests 13–18)

- [ ] Task receives parsed `PipelineMessage` and initialized `TaskContext`
- [ ] `ctx.files.resolve()` returns correct paths per level
- [ ] `ctx.files.exists()` reflects filesystem state
- [ ] `ctx.records.find()` queries DB correctly
- [ ] `ctx.client.update_record_data()` persists data via API
- [ ] File writes detected via checksum comparison → file-event sent
- [ ] No file-event when files unchanged

### RecordFlow → Pipeline Integration (tests 19–23)

- [ ] `.do_task()` dispatches task on matching status change
- [ ] `.do_task()` auto-creates single-step pipeline definition
- [ ] Conditional `.if_record()` gates task dispatch correctly
- [ ] `.pipeline()` dispatches named multi-step pipeline

### File Triggers & Invalidation (tests 24–27)

- [ ] `file().on_update()` triggers `invalidate_all_records()`
- [ ] Only targeted record types are invalidated
- [ ] `@pipeline_task` file write → file-event → invalidation cascade works end-to-end
- [ ] File-event with no matching records is a no-op

### Conditional Record Creation (tests 28–32)

- [ ] `.if_record(F.field == val)` creates record when all conditions match
- [ ] No record created when any condition fails (AND semantics)
- [ ] `.create_record("a", "b")` creates multiple records in one trigger
- [ ] Multiple conditional branches route to correct record types

### Entity Triggers (tests 33–34)

- [ ] `study().on_created().create_record()` fires on study import
- [ ] No duplicate records on repeated triggers

### Multi-Step Workflow (tests 35–42)

- [ ] Full chain: study → first_check → segmentation → task → projection → comparison
- [ ] Comparison with false_positive → `update_master_model`
- [ ] Comparison with false_negative → `second_review`
- [ ] Both discrepancies → both records created
- [ ] Clean comparison → no follow-up records
- [ ] Master model file update → projection invalidation cascade

### TaskContext Integration (tests 43–46)

- [ ] PATIENT-level file resolves to patient directory
- [ ] SERIES-level file with `{user_id}` pattern resolves correctly
- [ ] Glob returns all matching files for multi-file definitions
- [ ] Record data written via `ctx.client` persists to API

## Dependencies

- RabbitMQ running (auto-skip if unavailable)
- Test pipeline handlers (simple mock functions in test helpers)
- Mock `init_master_model` and `compare_w_projection` tasks (lightweight
  replacements that create/read dummy files and write deterministic data)
- Temp filesystem directories for file-level tests
- Full RecordFlowEngine for integration/workflow tests (tests 19+)
