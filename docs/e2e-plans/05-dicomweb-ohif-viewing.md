# E2E Plan: DICOMweb / OHIF Viewing Workflow

## File

`tests/e2e/test_dicomweb_workflow.py`

## Goal

Test the complete DICOMweb proxy workflow that powers OHIF Viewer: QIDO-RS study
search, series search, instance search, WADO-RS metadata retrieval, and pixel
data frame retrieval. Verify the proxy correctly translates DICOMweb requests
into DICOM C-FIND/C-GET operations, caches results, and returns valid
DICOM JSON responses.

## Markers & Conditions

```python
pytestmark = [pytest.mark.dicom]
```

Auto-skip when Orthanc PACS is unreachable (required for real C-FIND/C-GET behind
the proxy).

## Fixtures

### Shared (from conftest)

| Fixture | Purpose |
|---|---|
| `client` | Authenticated `AsyncClient` (superuser, auth bypass) |
| `test_session` | Async SQLAlchemy session with auto-rollback |

### New (local to test file)

| Fixture | Purpose |
|---|---|
| `pacs_available` | Session-scoped; skips all tests if PACS unreachable |
| `override_dicomweb_deps` | Autouse; overrides `DicomWebProxyServiceDep`, `DicomWebCacheDep` to use test PACS |
| `dicomweb_cache` | `DicomWebCache` with `tmp_path` as disk cache dir |
| `known_study` | Study UID known to exist in test PACS |
| `known_series` | Series UID within `known_study` |
| `known_instance` | SOP Instance UID within `known_series` |

### Alternative: Mock-Based Fixtures

If testing without real PACS, mock the `DicomWebProxyService`:

| Fixture | Purpose |
|---|---|
| `mock_proxy_service` | `AsyncMock` of `DicomWebProxyService` with predefined returns |
| `override_proxy_dep` | Overrides `DicomWebProxyServiceDep` with `mock_proxy_service` |

### Client Override

Re-override `client` — authenticated user (DICOMweb endpoints require `CurrentUserDep`,
not superuser). Same auth bypass pattern.

## Mocking Strategy

### Option A: Real PACS (preferred for true e2e)

- Override `DicomWebProxyServiceDep` to inject service configured for test PACS.
- Override `DicomWebCacheDep` to use a `tmp_path`-based cache (auto-cleaned).
- Tests exercise real C-FIND/C-GET through the proxy.

### Option B: Mocked Service (for CI without PACS)

- Replace `DicomWebProxyService` with `AsyncMock`.
- Return pre-built DICOM JSON responses.
- Test router logic, content-type headers, auth enforcement.
- This option runs without external dependencies.

### Cache Strategy

- Use `tmp_path` for disk cache directory → automatically cleaned up.
- Memory cache uses default `cachetools.TTLCache`.
- Tests verify cache hit/miss behavior.

## DICOM JSON Response Format

DICOMweb responses use the DICOM JSON Model (PS3.18 F.2):

```json
[
  {
    "0020000D": {"vr": "UI", "Value": ["1.2.3.4.5"]},
    "00100010": {"vr": "PN", "Value": [{"Alphabetic": "DOE^JOHN"}]},
    "00080020": {"vr": "DA", "Value": ["20230101"]}
  }
]
```

## Test Classes & Scenarios

### `TestQidoRsStudySearch`

1. **`test_search_all_studies`**
   - `GET /dicom-web/studies`
   - Assert: 200, content-type `application/dicom+json`
   - Assert: response is a JSON array, each element has DICOM tags
   - Verify: StudyInstanceUID tag (`0020000D`) present in each result

2. **`test_search_studies_with_patient_filter`**
   - `GET /dicom-web/studies?PatientID=KNOWN_ID`
   - Assert: 200, results filtered to matching patient
   - Assert: all returned studies have matching PatientID tag

3. **`test_search_studies_empty_result`**
   - `GET /dicom-web/studies?PatientID=NONEXISTENT`
   - Assert: 200, empty array `[]`

4. **`test_search_studies_requires_auth`**
   - No login / no auth cookie
   - `GET /dicom-web/studies`
   - Assert: 401

### `TestQidoRsSeriesSearch`

5. **`test_search_series_in_study`**
   - `GET /dicom-web/studies/{study_uid}/series`
   - Assert: 200, `application/dicom+json`
   - Assert: each result has SeriesInstanceUID tag (`0020000E`)
   - Assert: all results belong to the queried study

6. **`test_search_series_with_modality_filter`**
   - `GET /dicom-web/studies/{study_uid}/series?Modality=CT`
   - Assert: only CT series returned

7. **`test_search_series_nonexistent_study`**
   - `GET /dicom-web/studies/1.2.999.999/series`
   - Assert: 200, empty array (QIDO-RS returns empty, not 404)

### `TestQidoRsInstanceSearch`

8. **`test_search_instances_in_series`**
   - `GET /dicom-web/studies/{study_uid}/series/{series_uid}/instances`
   - Assert: 200, `application/dicom+json`
   - Assert: each result has SOPInstanceUID tag (`00080018`)

9. **`test_search_instances_nonexistent_series`**
   - Assert: 200, empty array

### `TestWadoRsMetadata`

10. **`test_retrieve_study_metadata`**
    - `GET /dicom-web/studies/{study_uid}/metadata`
    - Assert: 200, `application/dicom+json`
    - Assert: array of instance metadata objects
    - Verify: each instance has SOPInstanceUID, SOPClassUID
    - Verify: BulkDataURIs present for pixel data tags (7FE00010)

11. **`test_retrieve_series_metadata`**
    - `GET /dicom-web/studies/{study_uid}/series/{series_uid}/metadata`
    - Assert: 200, valid DICOM JSON
    - Verify: BulkDataURIs point to frame retrieval endpoint pattern
      (`/dicom-web/studies/.../instances/{uid}/frames/1`)

12. **`test_metadata_triggers_cache_population`**
    - First call: cache miss → C-GET from PACS
    - Second call: cache hit → faster, same result
    - Assert: both calls return identical metadata
    - Verify: cache directory contains `.dcm` files after first call

13. **`test_metadata_nonexistent_study`**
    - `GET /dicom-web/studies/1.2.999.999/metadata`
    - Assert: 200, empty array (or appropriate error)

### `TestWadoRsFrameRetrieval`

14. **`test_retrieve_single_frame`**
    - First, retrieve metadata to populate cache
    - `GET /dicom-web/studies/{s}/series/{se}/instances/{i}/frames/1`
    - Assert: 200, content-type is multipart or `application/octet-stream`
    - Assert: response body is non-empty (pixel data)

15. **`test_retrieve_multiple_frames`**
    - `GET .../frames/1,2` (for multi-frame instance, or if applicable)
    - Assert: 200, response contains pixel data

16. **`test_retrieve_frame_uncached_instance`**
    - Request frame for instance not yet in cache
    - Assert: service triggers C-GET to cache, then returns frame
    - Or: returns 404 if instance doesn't exist

17. **`test_retrieve_frame_invalid_frame_number`**
    - `GET .../frames/0` (frame numbers are 1-based)
    - Assert: 422 or appropriate error

### `TestDicomWebAuthEnforcement`

18. **`test_all_endpoints_require_authentication`**
    - For each endpoint (`/studies`, `/studies/{uid}/metadata`, etc.):
      - Call without auth
      - Assert: 401

### `TestDicomWebCacheLifecycle`

19. **`test_cache_populated_by_metadata_retrieval`**
    - Start with empty cache
    - `GET /dicom-web/studies/{uid}/series/{uid}/metadata`
    - Assert: cache dir now contains `.dcm` files for the series

20. **`test_cache_serves_subsequent_requests`**
    - First request: populates cache
    - Second request: served from cache (verify by timing or mock)
    - Assert: identical response content

21. **`test_cache_cleanup_removes_old_entries`**
    - Populate cache with test data
    - Trigger cleanup with low TTL
    - Assert: cache entries removed

### `TestOhifViewerIntegration`

22. **`test_ohif_static_files_served`** (if `ohif_enabled`)
    - `GET /ohif/`
    - Assert: 200, HTML response (OHIF app shell)

23. **`test_full_ohif_viewing_sequence`**
    - Simulates what OHIF Viewer does:
      1. `GET /dicom-web/studies` → list studies
      2. Pick first study → `GET /dicom-web/studies/{uid}/series` → list series
      3. Pick first series → `GET /dicom-web/studies/{s}/series/{se}/instances` → list instances
      4. `GET /dicom-web/studies/{s}/series/{se}/metadata` → get all metadata
      5. `GET /dicom-web/studies/{s}/series/{se}/instances/{i}/frames/1` → get pixel data
      6. All steps succeed with correct content types

## Assertions Checklist

- [ ] All QIDO-RS endpoints return `application/dicom+json` content type
- [ ] QIDO-RS responses are valid DICOM JSON arrays
- [ ] QIDO-RS filters (PatientID, Modality) work correctly
- [ ] QIDO-RS returns empty array for no matches (not 404)
- [ ] WADO-RS metadata contains BulkDataURIs
- [ ] WADO-RS frame retrieval returns pixel data bytes
- [ ] Cache is populated on first metadata retrieval
- [ ] Cache is used on subsequent requests
- [ ] All endpoints enforce authentication (401 without auth)
- [ ] OHIF viewing sequence works end-to-end
- [ ] Frame numbers are validated (1-based)

## Dependencies

- Orthanc PACS with test DICOM data (auto-skip if unavailable)
- For mocked variant: no external dependencies
