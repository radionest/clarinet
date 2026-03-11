# API Layer Guide

## Dependency Injection Aliases (dependencies.py)

Reuse these — don't create new `Depends()` wrappers:

```python
# Auth
CurrentUserDep      = Annotated[User, Depends(current_active_user)]
OptionalUserDep     = Annotated[User | None, Depends(optional_current_user)]
SuperUserDep        = Annotated[User, Depends(current_superuser)]

# Session & pagination
SessionDep          = Annotated[AsyncSession, Depends(get_async_session)]
PaginationDep       = Annotated[PaginationParams, Depends()]

# Repositories
UserRepositoryDep, StudyRepositoryDep,
PatientRepositoryDep, SeriesRepositoryDep, RecordRepositoryDep,
RecordTypeRepositoryDep, FileDefinitionRepositoryDep, PipelineDefinitionRepositoryDep

# Services
UserServiceDep, StudyServiceDep, RecordServiceDep, RecordTypeServiceDep, AdminServiceDep, SlicerServiceDep

# DICOM
DicomClientDep, PacsNodeDep

# DICOMweb proxy
DicomWebCacheDep, DicomWebProxyServiceDep

# File registry
ProjectFileRegistryDep  # dict | None from app.state
```

### RBAC Dependencies (dependencies.py)

```python
# Role-based access control
AuthorizedRecordDep = Annotated[Record, Depends(authorize_record_access)]
```

- `get_user_role_names(user)` — extracts `{role.name for role in user.roles}` with try/except
- `authorize_record_access` — checks superuser → role_name match → raises `AuthorizationError`
- `mask_records(records, user)` — converts `Record` → `RecordRead` + masks patient data for non-superusers

### Router Auth Levels

Changing auth levels on routers has cascading impact on tests — check `tests/test_client.py`,
`tests/integration/test_study_crud.py`, and all e2e test fixtures.

| Router | Auth Level | Notes |
|--------|-----------|-------|
| `record.py` | `CurrentUserDep` | Role-based filtering on list/find endpoints; `AuthorizedRecordDep` on single-record endpoints |
| `study.py` | `current_superuser` | Admin-only (patients, studies, series) |
| `record_type.py` | `current_superuser` | Admin-only for mutations; read is open to authenticated |
| `user.py` | `current_superuser` | Admin-only |
| `admin.py` | `current_superuser` | Admin-only |
| `dicomweb.py` | `CurrentUserDep` | Any authenticated user |

### Factory pattern for new repos/services

```python
async def get_X_repository(session: SessionDep) -> XRepository:
    return XRepository(session)

XRepositoryDep = Annotated[XRepository, Depends(get_X_repository)]
```

## Application Lifespan (app.py)

Startup sequence:
1. Database init (`db_manager.create_db_and_tables_async()`)
2. Default roles + config reconciliation (`reconcile_config()`) → stores `app.state.config_mode`, `app.state.config_tasks_path`
2b. Load project file registry + custom schema hydrators from tasks folder
3. Admin user creation (`ensure_admin_exists()`)
4. RecordFlow engine setup (if `recordflow_enabled`) → `app.state.recordflow_engine`
5. Pipeline broker startup (if `pipeline_enabled`) → `app.state.pipeline_broker`; syncs pipeline definitions to DB
6. Session cleanup service start (if `session_cleanup_enabled`)
7. DICOMweb cache init (if `dicomweb_enabled`) → `app.state.dicomweb_cache`; cleanup service → `app.state.dicomweb_cleanup`

Shutdown (reverse order): stop DICOMweb cleanup → flush DICOMweb cache → stop session cleanup → shutdown pipeline broker → close RecordFlow client → close DB.

## Exception Handlers (exception_handlers.py)

`setup_exception_handlers(app)` maps domain exceptions → HTTP:
- `EntityNotFoundError` → 404
- `EntityAlreadyExistsError` → 409
- `AuthenticationError` → 401
- `InvalidCredentialsError` → 401
- `AuthorizationError` → 403
- `ValidationError` → 422
- `BusinessRuleViolationError` → 409
- `ConfigurationError` → 500 (logs traceback, returns generic message)
- `DatabaseError` → 500 (logs traceback, returns generic message)
- `SlicerConnectionError` → 502
- `SlicerError` → 422
- `AlreadyAnonymizedError` → 409
- `AnonymizationFailedError` → 500 (logs traceback)
- `FileNotFoundError` → 404
- `Exception` → 500 (catch-all, logs traceback)

Routers don't need try/except for these — just let domain exceptions propagate.

## Pipeline Router (pipeline.py)

Mounted at `/api/pipelines`, conditional on `pipeline_enabled`. Endpoints:
- `GET /api/pipelines` — list all pipeline definitions from DB
- `GET /api/pipelines/{name}/definition` — get definition by name (used by `PipelineChainMiddleware`)
- `POST /api/pipelines/sync` — re-sync pipeline definitions to DB on demand

Uses `PipelineDefinitionRepositoryDep`.

## Config Mode Guards

Config mode guards on `/types` endpoints — see `clarinet/config/CLAUDE.md`.

## RecordFlow Integration

RecordFlow triggers are dispatched via the **service layer**, not directly from routers:

- `RecordService` wraps record mutations (`update_status`, `assign_user`, `submit_data`, `update_data`, `notify_file_change`, `bulk_update_status`, `notify_file_updates`) and fires the appropriate engine trigger (awaited directly).
- `StudyService` fires entity-creation triggers via `engine.fire()` (fire-and-forget) in `create_patient()`, `create_study()`, `create_series()`.
- Engine is injected via `get_recordflow_engine(request)` in `dependencies.py` (returns `None` when disabled).

Direct invalidation endpoint:
- `POST /records/{id}/invalidate` — body: `{mode, source_record_id, reason}`

## DICOMweb Proxy Router (dicomweb.py)

Mounted at `/dicom-web` (outside `/api` prefix for OHIF compatibility).
Conditional on `settings.dicomweb_enabled`. All endpoints require `CurrentUserDep`.

| Endpoint | DICOMweb | Backend |
|---|---|---|
| `GET /studies` | QIDO-RS | C-FIND Study |
| `GET /studies/{uid}/metadata` | WADO-RS | C-FIND series → C-GET all → metadata |
| `GET /studies/{uid}/series` | QIDO-RS | C-FIND Series |
| `GET /studies/{uid}/series/{uid}/instances` | QIDO-RS | C-FIND Image |
| `GET /studies/{uid}/series/{uid}/metadata` | WADO-RS | C-GET → cache → metadata |
| `GET /.../instances/{uid}/frames/{frames}` | WADO-RS | cached .dcm → pixel data |

OHIF static files served at `/ohif` (conditional on `settings.ohif_enabled`).

## SPA Frontend Routing

When `frontend_enabled=True`, catch-all `/{full_path:path}` serves:
- Static file if exists in `settings.static_directories`
- `index.html` otherwise (SPA client-side routing)
- Skips paths starting with `api/`, `dicom-web/`, or `ohif/`

## URL Reference for Tests

URL constants live in `tests/utils/urls.py`. The table below lists every endpoint
grouped by router prefix. Status codes: 201 = POST create, 204 = DELETE/bulk, 200 = default.

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
| `/api/records` | GET | 200 | List records |
| `/api/records` | POST | 201 | Create record |
| `/api/records/find` | POST | 200 | Search records |
| `/api/records/my` | GET | 200 | Current user's records |
| `/api/records/my/pending` | GET | 200 | Current user's pending records |
| `/api/records/available_types` | GET | 200 | Available record types for user |
| `/api/records/bulk/status` | PATCH | 204 | Bulk status update |
| `/api/records/{id}` | GET | 200 | Get record |
| `/api/records/{id}/schema` | GET | 200 | Hydrated JSON Schema (x-options → oneOf) |
| `/api/records/{id}/status` | PATCH | 200 | Update status |
| `/api/records/{id}/user` | PATCH | 200 | Assign user |
| `/api/records/{id}/data` | POST | 200 | Submit data |
| `/api/records/{id}/data` | PATCH | 200 | Update data |
| `/api/records/{id}/validate-files` | POST | 200 | Validate files |
| `/api/records/{id}/check-files` | POST | 200 | Check files |
| `/api/records/{id}/invalidate` | POST | 200 | Invalidate record |

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
| `/api/admin/records/{id}/assign` | PATCH | 200 | Admin assign record |
| `/api/admin/record-types/stats` | GET | 200 | Record type stats |

### DICOM (`/api/dicom`)

| URL | Method | Status | Description |
|---|---|---|---|
| `/api/dicom/patient/{id}/studies` | GET | 200 | PACS patient studies |
| `/api/dicom/import-study` | POST | 200 | Import study from PACS |
| `/api/dicom/studies/{uid}/anonymize` | POST | 200 | Anonymize study |

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
