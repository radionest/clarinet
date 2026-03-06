# E2E Plan: File Upload → Validation → Record Processing

## File

`tests/e2e/test_file_processing.py`

## Goal

Test the complete file-driven record lifecycle: create a record type with file
definitions, create a record (auto-blocked when required files missing), place
files on disk, trigger file check (auto-unblock), submit record data with file
validation, and verify file checksums and change detection via `check-files`
and `file-events` endpoints.

## Markers & Conditions

No special markers — all tests run against in-memory SQLite + local filesystem.

## Fixtures

### Shared (from conftest)

| Fixture | Purpose |
|---|---|
| `client` | Authenticated `AsyncClient` (superuser, auth bypass) |
| `test_session` | Async SQLAlchemy session with auto-rollback |
| `test_patient` | `Patient(id="TEST_PAT001")` |
| `test_study` | `Study(study_uid="1.2.3.4.5.6.7.8.9")` |

### New (local to test file)

| Fixture | Purpose |
|---|---|
| `test_series` | `Series` linked to `test_study` |
| `rt_with_files` | `RecordType` with INPUT and OUTPUT `FileDefinition` links |
| `rt_without_files` | `RecordType` without file definitions (control) |
| `working_dir` | `tmp_path` simulating `settings.working_folder / patient / study / series` |
| `mock_working_folder` | Patches `RecordRead.working_folder` property to point at `working_dir` |
| `_clear_registry` | Autouse; clears `RECORD_REGISTRY`, `ENTITY_REGISTRY`, `FILE_REGISTRY` |
| `recordflow_engine` | Optional; `RecordFlowEngine` with file-update flow attached to `app.state` |

### Client Override

Re-override `client` (same as `test_demo_processing.py`) — authenticated superuser.

## Mocking Strategy

- **Working folder**: patch the property so file operations target `tmp_path`.
- **RecordFlow engine**: attach to `app.state.recordflow_engine` for file-change
  trigger tests; set to `None` for isolated file tests.
- **`run_in_fs_thread`**: no mock needed, it runs real I/O in thread pool.
- **No external services** (no PACS, Slicer, RabbitMQ).

## Data Setup

### Record Type with File Definitions

```python
RecordTypeCreate(
    name="segmentation",
    level="SERIES",
    data_schema={
        "type": "object",
        "properties": {"quality": {"type": "string", "enum": ["good", "bad"]}},
        "required": ["quality"],
    },
    file_registry=[
        FileDefinitionRead(name="input_nifti", pattern="*.nii.gz", role="INPUT", required=True),
        FileDefinitionRead(name="output_mask", pattern="mask_*.nii.gz", role="OUTPUT", required=False),
    ],
)
```

### File on Disk

```
{working_dir}/
  some_scan.nii.gz       # matches input_nifti pattern
```

## Test Classes & Scenarios

### `TestRecordCreationWithFiles`

1. **`test_create_record_auto_blocked_when_files_missing`**
   - Create record type with required INPUT file
   - `POST /api/records/` → record created
   - Assert: record `status == "blocked"` (required input files not present)

2. **`test_create_record_pending_when_files_present`**
   - Place matching file in `working_dir` BEFORE creating record
   - `POST /api/records/` → record created
   - Assert: `status == "pending"`, `files` field populated

3. **`test_create_record_pending_when_no_file_defs`**
   - Use `rt_without_files`
   - `POST /api/records/` → record created
   - Assert: `status == "pending"` (no file requirements to check)

### `TestFileValidation`

4. **`test_validate_files_endpoint_reports_missing`**
   - Record is blocked (no files on disk)
   - `POST /api/records/{id}/validate-files`
   - Assert: `valid=False`, errors list names the missing file

5. **`test_validate_files_endpoint_reports_valid`**
   - Place matching file on disk
   - `POST /api/records/{id}/validate-files`
   - Assert: `valid=True`, `matched_files` contains file info

6. **`test_validate_files_on_record_without_file_defs`**
   - `POST /api/records/{id}/validate-files` (record type has no files)
   - Assert: `valid=True`, empty result

### `TestCheckFilesAndAutoUnblock`

7. **`test_check_files_unblocks_record_when_files_appear`**
   - Record starts as `blocked`
   - Place required file on disk
   - `POST /api/records/{id}/check-files`
   - Assert: record transitions to `pending`
   - DB check: `record.status == "pending"`, `record.files` populated

8. **`test_check_files_stays_blocked_when_files_still_missing`**
   - Record is `blocked`, no files placed
   - `POST /api/records/{id}/check-files`
   - Assert: `changed_files=[]`, `checksums={}`, record still `blocked`

9. **`test_check_files_detects_changed_file`**
   - Record is `pending` with stored checksums
   - Modify file content on disk
   - `POST /api/records/{id}/check-files`
   - Assert: `changed_files` lists the modified file name
   - Assert: `checksums` updated in DB

10. **`test_check_files_no_change`**
    - Record is `pending`, files unchanged since last check
    - `POST /api/records/{id}/check-files`
    - Assert: `changed_files=[]`, checksums unchanged

### `TestDataSubmissionWithFiles`

11. **`test_submit_data_succeeds_when_files_valid`**
    - Record is `pending`, required files present
    - `POST /api/records/{id}/data` with `{"quality": "good"}`
    - Assert: 200, `status == "finished"`, `data == {"quality": "good"}`
    - Assert: `files` field populated in response

12. **`test_submit_data_fails_when_files_missing`**
    - Record is `pending`, files removed from disk
    - `POST /api/records/{id}/data` with valid data
    - Assert: 422 (ValidationError — file validation fails with `raise_on_invalid=True`)

13. **`test_submit_data_fails_on_blocked_record`**
    - Record is `blocked`
    - `POST /api/records/{id}/data`
    - Assert: 409 (CONFLICT — "Record is blocked")

14. **`test_submit_data_fails_on_already_finished_record`**
    - Record is already `finished`
    - `POST /api/records/{id}/data`
    - Assert: 409 (CONFLICT — "Record already finished")

15. **`test_submit_data_with_invalid_schema`**
    - `POST /api/records/{id}/data` with `{"quality": "unknown"}`
    - Assert: 422 (schema validation fails — "unknown" not in enum)

### `TestFileEventsEndpoint`

16. **`test_file_events_dispatches_to_engine`**
    - Attach a mock `RecordFlowEngine` to `app.state`
    - `POST /api/patients/{id}/file-events` with `["report.pdf", "scan.nii.gz"]`
    - Assert: 200, `{"dispatched": ["report.pdf", "scan.nii.gz"]}`
    - Verify: engine's `handle_file_update` was called for each file

17. **`test_file_events_without_engine`**
    - `app.state.recordflow_engine = None`
    - `POST /api/patients/{id}/file-events` with `["report.pdf"]`
    - Assert: 200, `{"dispatched": ["report.pdf"]}` (no error, just no-op)

### `TestBulkStatusUpdate`

18. **`test_bulk_status_update`**
    - Create 3 records (pending)
    - `PATCH /api/records/bulk/status` with `record_ids=[1,2,3]`, `new_status="inwork"`
    - Assert: 204
    - DB check: all 3 records now have `status == "inwork"`

19. **`test_bulk_status_update_empty_list`**
    - `PATCH /api/records/bulk/status` with `record_ids=[]`, `new_status="inwork"`
    - Assert: 204 (no-op)

### `TestFullFileProcessingCycle`

20. **`test_complete_file_driven_record_lifecycle`**
    - Combined scenario:
      1. Create record type with required INPUT file → 201
      2. Create record → auto-blocked (files missing)
      3. `validate-files` → invalid
      4. Place file on disk
      5. `check-files` → record auto-unblocks to `pending`
      6. `validate-files` → valid
      7. Submit data → `finished`
      8. Modify file on disk
      9. `check-files` → detects change, `changed_files` non-empty
      10. Verify DB: record has `data`, `files`, `file_checksums`

## Assertions Checklist

- [ ] Record auto-blocked when required INPUT files missing
- [ ] Record stays `pending` when no file definitions or files present
- [ ] `validate-files` reports correct valid/invalid state
- [ ] `check-files` auto-unblocks `blocked` records when files appear
- [ ] `check-files` detects file content changes via checksums
- [ ] Data submission validates files and schema
- [ ] Data submission rejects blocked/finished records
- [ ] `file-events` dispatches to RecordFlow engine
- [ ] Bulk status update works for multiple records
- [ ] Full lifecycle: blocked → pending → finished with file tracking

## Dependencies

- `tmp_path` pytest fixture (for filesystem operations)
- No external services
