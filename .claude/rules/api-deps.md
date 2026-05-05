---
paths:
  - "clarinet/api/dependencies.py"
  - "clarinet/api/routers/**"
---

# API — DI Aliases & Reference

## Dependency Injection Aliases (dependencies.py)

Reuse these — don't create new `Depends()` wrappers:

```python
# Auth
CurrentUserDep      = Annotated[User, Depends(current_active_user)]
OptionalUserDep     = Annotated[User | None, Depends(optional_current_user)]
SuperUserDep        = Annotated[User, Depends(current_superuser)]
AdminUserDep        = Annotated[User, Depends(current_admin_user)]   # is_superuser OR 'admin' role

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

### RBAC Dependencies

```python
AuthorizedRecordDep = Annotated[Record, Depends(authorize_record_access)]
```

- `get_user_role_names(user)` — extracts `{role.name for role in user.roles}` with try/except
- `authorize_record_access` — checks superuser -> role_name match -> raises `AuthorizationError`
- `current_admin_user` — passes `is_superuser=True` OR membership in the built-in `admin` role; used by `admin.py`, `reports.py`, `study.py`, `user.py` (router-level on `study.py`, per-endpoint elsewhere). `dicom.py` stays `current_superuser`-only.
- `mask_records(records, user)` — converts `Record` -> `RecordRead` + masks patient data for non-superusers

### Factory pattern for new repos/services

```python
async def get_X_repository(session: SessionDep) -> XRepository:
    return XRepository(session)

XRepositoryDep = Annotated[XRepository, Depends(get_X_repository)]
```

## DICOMweb Proxy Router Endpoints (dicomweb.py)

Mounted at `/dicom-web` (outside `/api` prefix for OHIF compatibility).
Conditional on `settings.dicomweb_enabled`. All endpoints require `CurrentUserDep`.

| Endpoint | DICOMweb | Backend |
|---|---|---|
| `GET /studies` | QIDO-RS | C-FIND Study |
| `GET /studies/{uid}/metadata` | WADO-RS | C-FIND series -> C-GET all -> metadata |
| `GET /studies/{uid}/series` | QIDO-RS | C-FIND Series |
| `GET /studies/{uid}/series/{uid}/instances` | QIDO-RS | C-FIND Image |
| `GET /studies/{uid}/series/{uid}/metadata` | WADO-RS | C-GET -> cache -> metadata |
| `GET /.../instances/{uid}/frames/{frames}` | WADO-RS | cached .dcm -> pixel data |

OHIF static files served at `/ohif` (conditional on `settings.ohif_enabled`).
