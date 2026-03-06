# E2E Plan: PACS Import → Anonymization → Export

## File

`tests/e2e/test_dicom_workflow.py`

## Goal

Test the complete DICOM lifecycle: query PACS for patient studies, import a study
with series into the local database, run anonymization (save to disk / send back
to PACS), and verify final state in the DB and filesystem.

## Markers & Conditions

```python
pytestmark = [pytest.mark.dicom]
```

All tests auto-skip when Orthanc PACS is unreachable (session-scoped fixture
`pacs_available` does a `GET /system` check).

## Fixtures

### Shared (from conftest)

| Fixture | Purpose |
|---|---|
| `client` | Authenticated `AsyncClient` (superuser, auth bypass) |
| `test_session` | Async SQLAlchemy session with auto-rollback |
| `test_patient` | Pre-seeded `Patient(id="TEST_PAT001")` |

### New (local to test file)

| Fixture | Purpose |
|---|---|
| `pacs_available` | Session-scoped; skips all tests if PACS unreachable |
| `override_dicom_deps` | Autouse; overrides `DicomClientDep`, `PacsNodeDep` for test PACS |
| `override_anon_settings` | Patches `settings.anon_save_to_disk`, `settings.anon_send_to_pacs` |
| `cleanup_anon_files` | Yields, then removes temp anonymization output dir |
| `pacs_study_uid` | Known study UID that exists on the test PACS (constant or uploaded in fixture) |

### Client Override

Re-override `client` from e2e conftest (same pattern as `test_demo_processing.py`):
authenticated superuser with session + auth overrides.

## Mocking Strategy

- **DicomClient / PacsNode**: override via `app.dependency_overrides` to point at
  the test Orthanc instance (same pattern as `tests/integration/test_dicom_router.py`).
- **AnonymizationService settings**: patch `settings.anon_uid_salt`,
  `settings.anon_save_to_disk`, `settings.anon_send_to_pacs` per scenario.
- **RecordFlow engine**: set `app.state.recordflow_engine = None` (disabled) to
  isolate DICOM flow from record-processing side effects.
- **No RabbitMQ**: `settings.pipeline_enabled = False` so anonymization runs in-process
  (not dispatched to pipeline).

## Test Classes & Scenarios

### `TestPacsSearchAndImport`

1. **`test_search_patient_studies`**
   - `GET /api/dicom/patient/{patient_id}/studies`
   - Assert: response 200, list of `PacsStudyWithSeries`, each has `study`, `series`, `already_exists=False`
   - Verify series list is non-empty for the known study

2. **`test_search_nonexistent_patient`**
   - `GET /api/dicom/patient/NONEXISTENT/studies`
   - Assert: 200, empty list

3. **`test_import_study_creates_study_and_series`**
   - `POST /api/dicom/import-study` with `{study_instance_uid, patient_id}`
   - Assert: 200, response contains `study_uid`, `patient_id`
   - DB check: `Study` row exists, `Series` rows match PACS series count
   - Verify `series_description`, `modality`, `instance_count` persisted

4. **`test_import_duplicate_study_fails`**
   - Import same study twice
   - Assert: second call returns 409 (CONFLICT)

5. **`test_search_after_import_shows_already_exists`**
   - After import, re-search patient studies
   - Assert: `already_exists=True` for the imported study

### `TestAnonymizationWorkflow`

Precondition: study imported (use `test_import_study_creates_study_and_series` as setup
or a fixture that imports the study).

6. **`test_anonymize_study_save_to_disk`**
   - Patch `settings.anon_save_to_disk = True`, `settings.anon_send_to_pacs = False`
   - `POST /api/dicom/studies/{uid}/anonymize`
   - Assert: 200, `AnonymizationResult` with `status="success"`, `anonymized_study_uid` set
   - DB check: `Study.anon_uid` populated, `Series.anon_uid` populated for each series
   - Filesystem: anonymized DICOM files exist under `settings.anon_output_dir`

7. **`test_anonymize_study_send_to_pacs`**
   - Patch `settings.anon_save_to_disk = False`, `settings.anon_send_to_pacs = True`
   - `POST /api/dicom/studies/{uid}/anonymize`
   - Assert: 200, result `status="success"`
   - Verify anonymized study UID now findable via C-FIND on test PACS

8. **`test_anonymize_already_anonymized_returns_conflict`**
   - Anonymize same study twice
   - Assert: second call returns 409 (`AlreadyAnonymizedError`)

9. **`test_anonymize_nonexistent_study_returns_404`**
   - `POST /api/dicom/studies/1.2.999.999/anonymize`
   - Assert: 404

10. **`test_background_anonymization_returns_202`**
    - `POST /api/dicom/studies/{uid}/anonymize?background=true`
    - Assert: 202, body contains `study_uid`

### `TestFullDicomCycle`

11. **`test_import_then_anonymize_then_verify_db_state`**
    - Combined scenario:
      1. Search patient → import study → anonymize → verify
      2. Assert final DB state: patient exists, study has `anon_uid`, all series have `anon_uid`
      3. DICOM tags in anonymized files verified (PatientName replaced, PatientID replaced)

## Assertions Checklist

- [ ] HTTP status codes (200, 201, 202, 404, 409)
- [ ] Response models match Pydantic schemas
- [ ] DB rows created/updated correctly (Study, Series, anon_uid fields)
- [ ] Filesystem artifacts created/cleaned up
- [ ] DICOM tag values in anonymized files (deidentified patient name/ID)
- [ ] Idempotency (duplicate import/anonymize handled gracefully)

## Dependencies

- Orthanc PACS running (auto-skip if unavailable)
- Test DICOM data uploaded to PACS (fixture or pre-loaded)
- `pydicom` for DICOM tag verification
