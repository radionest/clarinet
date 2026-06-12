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
| `/api/records` | POST | 201 | Create record. **409**: `RECORD_LIMIT_REACHED`, `UNIQUE_PER_USER`, `PARENT_REQUIRED` (record_type with `parent_required=True` and no `parent_record_id` in payload). `user_id` is inherited from the parent only when RecordType has `inherit_user_from_parent=True` |
| `/api/records/find` | POST | 200 | Search records (cursor pagination, returns RecordPage) |
| `/api/records/find/random` | POST | 200 | Find random record matching filters (RecordRead or null) |
| `/api/records/available_types` | GET | 200 | Available record types for user |
| `/api/records/filter-options` | POST | 200 | Distinct patient/record_type/user values for filter dropdowns (RBAC-scoped; body filters ignored) |
| `/api/records/bulk/status` | PATCH | 204 | Bulk status update. **409** for non-superusers when any target record is finished and its type locks submitted records (`editable=False` or expired `edit_window_days`); **409** when any target is `preparing` and the new status is `inwork`/`finished`. Preparing → pending re-validates files per record (may land in `blocked`) |
| `/api/records/{id}` | GET | 200 | Get record |
| `/api/records/{id}/schema` | GET | 200 | Hydrated JSON Schema (x-options → oneOf) |
| `/api/records/{id}/status` | PATCH | 200 | Update status. **409** for non-superusers when the record is finished and its type locks submitted records; **409** on `preparing` → `inwork`/`finished` (must exit via `pending`). Preparing → pending re-validates files (may land in `blocked`) |
| `/api/records/{id}/user` | PATCH | 200 | Assign user |
| `/api/records/{id}/context-info` | PATCH | 200 | Replace context_info (markdown). Body: `{"context_info": str \| null}`. Auth: superuser/owner/unassigned |
| `/api/records/{id}/data` | POST | 200 | Submit data. **409** when the record is `blocked`, `preparing`, or already `finished` |
| `/api/records/{id}/data` | PATCH | 200 | Update data. **409** for non-superusers when the type locks submitted records (`editable=False` or expired `edit_window_days`) |
| `/api/records/{id}/data/prefill` | POST | 200 | Prefill data (error if exists). Allowed statuses: `pending`/`blocked`/`preparing` |
| `/api/records/{id}/data/prefill` | PUT | 200 | Replace prefill data. Allowed statuses: `pending`/`blocked`/`preparing` |
| `/api/records/{id}/data/prefill` | PATCH | 200 | Merge into prefill data. Allowed statuses: `pending`/`blocked`/`preparing` |
| `/api/records/{id}/submit` | POST | 200 | Submit + run `slicer_result_validator` if configured; merges `__execResult` into data on save. **409** when the record is `blocked`, `preparing`, or already `finished` |
| `/api/records/{id}/submit` | PATCH | 200 | Re-submit a finished record (same Slicer-validator + `__execResult` merge as POST). **409** for non-superusers when the type locks submitted records |
| `/api/records/{id}/validate-files` | POST | 200 | Validate files |
| `/api/records/{id}/check-files` | POST | 200 | Check files |
| `/api/records/{id}/output-files/{name}` | GET | 200 | Download a single OUTPUT file by `FileDefinition.name` (404 if not defined or not on disk). Auth: `AuthorizedRecordDep` |
| `/api/records/{id}/fail` | POST | 200 | Manually fail record |
| `/api/records/{id}/invalidate` | POST | 200 | Invalidate record. Hard mode: **409** for non-superusers when the record is finished and its type locks submitted records |
| `/api/records/{id}/events` | GET | 200 | Audit trail (RecordEvent), oldest first; `actor_id=null` = system action. Auth: `AuthorizedRecordDep` |
| `/api/records/{id}/runs` | GET | 200 | Pipeline task runs for this record, newest first. Auth: `AuthorizedRecordDep`; patient/study/series ids masked per record masking policy |
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
| `/api/patients` | POST | 201 | Add patient. **422**: malformed `patient_id` (`code=INVALID_PATIENT_IDENTIFIER`) |
| `/api/patients/{id}` | GET | 200 | Patient details. **422**: malformed path id |
| `/api/patients/{id}` | DELETE | 204 | Delete patient. **422**: malformed path id |
| `/api/patients/{id}/anonymize` | POST | 200 | Anonymize patient. **422**: malformed path id |
| `/api/patients/{id}/file-events` | POST | 200 | Notify file changes for patient. **422**: malformed path id |
| `/api/studies` | GET | 200 | List studies |
| `/api/studies` | POST | 201 | Add study. **422**: malformed `patient_id` |
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
| `/api/admin/records/events/deleted` | GET | 200 | Audit events of deleted records (snapshot in `old_value`), newest first |
| `/api/admin/record-types/stats` | GET | 200 | Record type stats |
| `/api/admin/reports` | GET | 200 | List custom SQL reports (`*.sql` from `settings.reports_path`) |
| `/api/admin/reports/{name}/download?format=csv\|xlsx` | GET | 200 | Download report as CSV or XLSX (default: csv) |
| `/api/admin/quarto-reports` | GET | 200 | List Quarto report templates (`*.qmd` from `settings.quarto_reports_path`) |
| `/api/admin/quarto-reports/{name}/render` | POST | 202 | Start background render. Body `{"formats":["docx","pdf"]}`; returns the pending render state (with `render_id`). **404** template or a declared `clarinet.data` SQL report is unknown. **422** empty `formats`. **503** quarto CLI not installed |
| `/api/admin/quarto-reports/{name}/renders/{render_id}/status` | GET | 200 | Poll the render status sidecar. **404** render unknown |
| `/api/admin/quarto-reports/{name}/renders/{render_id}/download?format=docx\|pdf` | GET | 200 | Download the rendered file. **409** render not finished. **404** render unknown |

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
| `/api/pipelines/runs` | POST | 201 | Create task run audit row (AdminUserDep; AuditMiddleware service token resolves to admin). Idempotent on duplicate id |
| `/api/pipelines/runs` | GET | 200 | List runs, filters: `status`, `task_name`, `record_id`, `since` (started_at lower bound) + pagination (AdminUserDep) |
| `/api/pipelines/runs/{task_id}` | GET | 200 | Get single run (AdminUserDep) |
| `/api/pipelines/runs/{task_id}` | PATCH | 200 | Record terminal status (AdminUserDep); late `retrying` after a terminal status is ignored |

### Workflow visualization (`/api/admin/workflow`)

Admin-only (`AdminUserDep`). 503 when `recordflow_enabled=False`.

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/admin/workflow/graph?record_id=&expanded=&scope=` | GET | 200 | Workflow graph. `scope=schema` (default) — project-wide graph; firings populated when `record_id` is set. `scope=instance` — subgraph centered on `record_id`'s record_type (parents + children + glue); requires `record_id` (422 otherwise) |
| `/api/admin/workflow/dry-run` | POST | 200 | Plan a trigger; returns `{plan, digest}`. Body: `{record_id, trigger_kind, status_override?}` |
| `/api/admin/workflow/fire` | POST | 200 | Execute trigger after digest match (409 on mismatch). Body: `{record_id, trigger_kind, status_override?, plan_digest}` |
| `/api/admin/workflow/dispatch-dry-run` | POST | 200 | Plan a direct enqueue of one `call:*` or `pipeline:*` node; returns `{preview, digest}`. 404 if node unknown, 422 if node kind is not dispatchable. Body: `{node_id, record_id}` |
| `/api/admin/workflow/dispatch` | POST | 200 | Enqueue the planned action into TaskIQ after digest match (409 on mismatch / replay). Returns `{preview, task_id}`. Body: `{node_id, record_id, plan_digest}` |

### Slicer (`/api/slicer`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/slicer/exec` | POST | 200 | Execute script |
| `/api/slicer/exec/raw` | POST | 200 | Execute raw script |
| `/api/slicer/ping` | GET | 200 | Ping Slicer |
| `/api/slicer/clear` | POST | 200 | Clear scene |
| `/api/slicer/records/{id}/open` | POST | 200 | Open record in Slicer |
| `/api/slicer/records/{id}/validate` | POST | 200 | Validate in Slicer |

**Optional request header `X-Clarinet-Storage-Path-Client`** — per-client override for the storage prefix visible to the user's Slicer. Honored by `/slicer/records/{id}/open`, `/slicer/records/{id}/validate`, and `/records/{id}/submit` (POST and PATCH). Set by the frontend from `localStorage` (managed on the `/settings` page). When absent or blank, falls back to `settings.storage_path_client` (legacy global). Consumed via `ClientStoragePathDep` in `dependencies.py`.

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
| `/dicom-web/preload` | POST | 200 | Start multi-study preload. Body `{"study_uids": [...]}` (1–20 UIDs). **422**: empty/oversized list |
| `/dicom-web/preload/progress/{task_id}` | GET | 200 | Poll preload progress (`starting`/`fetching`/`ready`/`error`/`not_found`) |
