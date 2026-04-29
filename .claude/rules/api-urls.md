---
paths:
  - "tests/**"
  - "clarinet/api/routers/**"
---

# API URL Reference for Tests

URL constants live in `tests/utils/urls.py`. Status codes: 201 = POST create, 204 = DELETE/bulk, 200 = default.

**Rule:** All test files must use constants from `tests/utils/urls.py` — no hardcoded URL strings. New endpoints must have a matching constant added.

**Rule:** Endpoints that return 409 (business conflict) must declare `responses={409: {"description": "..."}}` in the decorator.

### Auth (`/api/auth`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/auth/login` | POST | 200 | Login (fastapi-users) |
| `/api/auth/logout` | POST | 200 | Logout |
| `/api/auth/register` | POST | 200 | Register |
| `/api/auth/me` | GET | 200 | Current user info |
| `/api/auth/session/validate` | GET | 200 | Validate session |
| `/api/auth/session/refresh` | POST | 200 | Refresh session |
| `/api/auth/sessions/active` | GET | 200 | List active sessions |
| `/api/auth/sessions/{token_preview}` | DELETE | 200 | Revoke session |

### Users (`/api/user`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/user` | GET | 200 | List users |
| `/api/user` | POST | 201 | Create user |
| `/api/user/me` | GET | 200 | Current user detail |
| `/api/user/me/roles` | GET | 200 | Current user roles |
| `/api/user/roles` | POST | 201 | Create role |
| `/api/user/roles/{role_name}` | GET | 200 | Role details |
| `/api/user/{user_id}` | GET | 200 | Get user |
| `/api/user/{user_id}` | PUT | 200 | Update user |
| `/api/user/{user_id}` | DELETE | 204 | Delete user |
| `/api/user/{user_id}/roles` | GET | 200 | User roles |
| `/api/user/{user_id}/roles/{role_name}` | POST | 200 | Add role |
| `/api/user/{user_id}/roles/{role_name}` | DELETE | 200 | Remove role |
| `/api/user/{user_id}/activate` | POST | 200 | Activate user |
| `/api/user/{user_id}/deactivate` | POST | 200 | Deactivate user |

### Records (`/api/records`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/records` | POST | 201 | Create record |
| `/api/records/find` | POST | 200 | Search records (cursor pagination, returns RecordPage) |
| `/api/records/find/random` | POST | 200 | Find random record matching filters (RecordRead or null) |
| `/api/records/available_types` | GET | 200 | Available record types for user |
| `/api/records/bulk/status` | PATCH | 204 | Bulk status update |
| `/api/records/{id}` | GET | 200 | Get record |
| `/api/records/{id}/schema` | GET | 200 | Hydrated JSON Schema (x-options → oneOf) |
| `/api/records/{id}/status` | PATCH | 200 | Update status |
| `/api/records/{id}/user` | PATCH | 200 | Assign user |
| `/api/records/{id}/data` | POST | 200 | Submit data |
| `/api/records/{id}/data` | PATCH | 200 | Update data |
| `/api/records/{id}/data/prefill` | POST | 200 | Prefill data (error if exists) |
| `/api/records/{id}/data/prefill` | PUT | 200 | Replace prefill data |
| `/api/records/{id}/data/prefill` | PATCH | 200 | Merge into prefill data |
| `/api/records/{id}/validate-files` | POST | 200 | Validate files |
| `/api/records/{id}/check-files` | POST | 200 | Check files |
| `/api/records/{id}/fail` | POST | 200 | Manually fail record |
| `/api/records/{id}/invalidate` | POST | 200 | Invalidate record |
| `/api/records/{id}/viewers` | GET | 200 | List viewer URIs for all enabled viewers |
| `/api/records/{id}/viewers/{name}` | GET | 200 | Get viewer URI for a specific viewer |

### Record Types (`/api/records/types`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/records/types` | GET | 200 | List types |
| `/api/records/types` | POST | 201 | Create type |
| `/api/records/types/find` | POST | 200 | Search types |
| `/api/records/types/{name}` | GET | 200 | Get type |
| `/api/records/types/{name}` | PATCH | 200 | Update type |
| `/api/records/types/{name}` | DELETE | 204 | Delete type |

### Patients, Studies, Series (`/api`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/patients` | GET | 200 | List patients |
| `/api/patients` | POST | 201 | Add patient |
| `/api/patients/{id}` | GET | 200 | Patient details |
| `/api/patients/{id}` | DELETE | 204 | Delete patient |
| `/api/patients/{id}/anonymize` | POST | 200 | Anonymize patient |
| `/api/studies` | GET | 200 | List studies |
| `/api/studies` | POST | 201 | Add study |
| `/api/studies/{uid}` | GET | 200 | Study details |
| `/api/studies/{uid}` | DELETE | 204 | Delete study |
| `/api/studies/{uid}/series` | GET | 200 | Study series |
| `/api/studies/{uid}/add_anonymized` | POST | 200 | Add anonymized study |
| `/api/series` | GET | 200 | List series |
| `/api/series` | POST | 201 | Add series |
| `/api/series/random` | GET | 200 | Random series |
| `/api/series/find` | POST | 200 | Search series |
| `/api/series/{uid}` | GET | 200 | Series details |
| `/api/series/{uid}/add_anonymized` | POST | 200 | Add anonymized series |

### Admin (`/api/admin`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/admin/stats` | GET | 200 | Admin stats |
| `/api/admin/records/{id}` | DELETE | 200 | Cascade-delete record + descendants + output files (admin; 409 if any inwork) |
| `/api/admin/records/{id}/assign` | PATCH | 200 | Admin assign record |
| `/api/admin/records/{id}/status` | PATCH | 200 | Admin set record status |
| `/api/admin/records/{id}/user` | DELETE | 200 | Admin unassign record user |
| `/api/admin/records/{id}/output-files` | DELETE | 200 | Clear output files (admin) |
| `/api/admin/record-types/stats` | GET | 200 | Record type stats |

### DICOM (`/api/dicom`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/dicom/patient/{id}/studies` | GET | 200 | PACS patient studies |
| `/api/dicom/import-study` | POST | 200 | Import study from PACS |
| `/api/dicom/studies/{uid}/anonymize` | POST | 200 | Anonymize study (sync). 202 in `?background=true` mode (requires `anonymize-study` Record). 404 in background mode without that Record |

### Pipelines (`/api/pipelines`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/pipelines/{name}/definition` | GET | 200 | Pipeline definition |
| `/api/pipelines/sync` | POST | 200 | Sync definitions |

### Slicer (`/api/slicer`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/slicer/exec` | POST | 200 | Execute script |
| `/api/slicer/exec/raw` | POST | 200 | Execute raw script |
| `/api/slicer/ping` | GET | 200 | Ping Slicer |
| `/api/slicer/clear` | POST | 200 | Clear scene |
| `/api/slicer/records/{id}/open` | POST | 200 | Open record in Slicer |
| `/api/slicer/records/{id}/validate` | POST | 200 | Validate in Slicer |

### DICOMweb (`/dicom-web`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/dicom-web/studies` | GET | 200 | QIDO-RS search studies |
| `/dicom-web/studies/{uid}/metadata` | GET | 200 | WADO-RS study metadata |
| `/dicom-web/studies/{uid}/series` | GET | 200 | QIDO-RS search series |
| `/dicom-web/studies/{uid}/series/{uid}/instances` | GET | 200 | QIDO-RS instances |
| `/dicom-web/studies/{uid}/series/{uid}/metadata` | GET | 200 | WADO-RS series metadata |
| `/dicom-web/.../instances/{uid}/frames/{f}` | GET | 200 | WADO-RS pixel data |
| `/dicom-web/studies/{uid}/series/{uid}/archive` | GET | 200 | Download series as ZIP |
