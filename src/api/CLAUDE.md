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
RecordTypeRepositoryDep

# Services
UserServiceDep, StudyServiceDep

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
1. Database init (`db_manager.initialize()`)
2. Default roles + demo RecordTypes creation
3. Admin user creation (`create_admin_user()`)
4. RecordFlow engine setup (if `recordflow_enabled`)
5. Session cleanup service start (if `session_cleanup_enabled`)

Shutdown: stop cleanup service → close RecordFlow client → close DB.

## Exception Handlers (exception_handlers.py)

`setup_exception_handlers(app)` maps domain exceptions → HTTP:
- `EntityNotFoundError` → 404
- `EntityAlreadyExistsError` → 409
- `AuthenticationError` → 401
- `AuthorizationError` → 403
- `ValidationError` → 422
- `BusinessRuleViolationError` → 409

Routers don't need try/except for these — just let domain exceptions propagate.

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
