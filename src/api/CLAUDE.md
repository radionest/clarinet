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
UserRepositoryDep, UserRoleRepositoryDep, StudyRepositoryDep,
PatientRepositoryDep, SeriesRepositoryDep, RecordRepositoryDep,
RecordTypeRepositoryDep, PipelineDefinitionRepositoryDep

# Services
UserServiceDep, StudyServiceDep, AdminServiceDep, SlicerServiceDep

# DICOM
DicomClientDep, PacsNodeDep

# DICOMweb proxy
DicomWebCacheDep, DicomWebProxyServiceDep
```

### Factory pattern for new repos/services

```python
async def get_X_repository(session: SessionDep) -> XRepository:
    return XRepository(session)

XRepositoryDep = Annotated[XRepository, Depends(get_X_repository)]
```

## Application Lifespan (app.py)

Startup sequence:
1. Database init (`db_manager.create_db_and_tables_async()`)
2. Default roles + demo RecordTypes creation
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

## RecordFlow Integration (record.py)

Endpoints that trigger RecordFlow engine via `BackgroundTasks`:
- `PATCH /records/{id}/status` → `engine.handle_record_status_change()`
- `PATCH /records/{id}/data` → `engine.handle_record_data_update()`
- `POST /records/{id}/assign` → `engine.handle_record_status_change()`

Engine is accessed via `request.app.state.recordflow_engine` (may be `None` if disabled).

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
