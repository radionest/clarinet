---
paths:
  - "clarinet/api/dependencies.py"
  - "clarinet/api/routers/**"
---

# API — DI Aliases & Reference

Deep reference: [Backend architecture](../../docs/kb/architecture.md), [Domain model](../../docs/kb/domain-model.md) (roles, capabilities, masking).

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
RecordTypeRepositoryDep, FileDefinitionRepositoryDep, PipelineDefinitionRepositoryDep,
PipelineTaskRunRepositoryDep, RecordEventRepositoryDep

# Services
UserServiceDep, StudyServiceDep, RecordServiceDep, RecordTypeServiceDep, AdminServiceDep, SlicerServiceDep,
AnonymizationServiceDep, ReportServiceDep, QuartoReportServiceDep

# Registries (from app.state; empty fallback when lifespan bypassed)
ReportRegistryDep, QuartoReportRegistryDep, ViewerRegistryDep

# DICOM
DicomClientDep, PacsNodeDep

# DICOMweb proxy
DicomWebCacheDep, DicomWebProxyServiceDep

# File registry
ProjectFileRegistryDep  # dict | None from app.state

# Slicer per-client override — read header-first (X-Clarinet-Storage-Path-Client),
# then the clarinet_storage_path_client cookie (URL-decoded; rides on formosh
# form-submits that strip custom headers). Both set by the frontend from
# localStorage; None when absent, blank, or rejected. Consumed by
# build_slicer_context_async to override settings.storage_path_client.
ClientStoragePathDep    # Annotated[str | None, Depends(get_client_storage_path)]
```

### RBAC Dependencies

```python
AuthorizedRecordDep = Annotated[Record, Depends(authorize_record_access)]   # record read access
MutableRecordDep    = Annotated[Record, Depends(authorize_mutable_record_access)]  # + superuser/owner/unassigned
AuditActorDep       = Annotated[UUID | None, Depends(get_audit_actor)]  # current user id, or None for X-Internal-Token (system)
```

- `get_user_role_names(user)` — returns `set(user.role_names)`; delegates to the `User.role_names` computed_field, which logs a warning when `roles` was not eagerly loaded
- `authorize_record_access` — checks superuser -> role_name match -> raises `AuthorizationError`
- `authorize_mutable_record_access` (`MutableRecordDep`) — builds on `AuthorizedRecordDep`; mutation allowed for superuser, the assigned user, or an unassigned record. Additionally bypasses the owner check when `record.record_type.shared_editing` is `True`; any role-holder may then mutate the record regardless of `user_id`
- `require_mutable_config(request)` — raises `AuthorizationError` when `app.state.config_mode == "python"` (RecordType mutations disabled — Python files are the single source of truth)
- `current_admin_user` — passes `is_superuser=True` OR membership in the built-in `admin` role; used by `admin.py`, `study.py`, `user.py` (router-level on `study.py`, per-endpoint elsewhere), and `dicom.py` (search/import only — `anonymize_study` stays `current_superuser`).
- `require_capability(capability)` — dependency factory; `capability` is a
  `Capability` enum member. Admits a user whose effective capabilities
  (`resolve_capabilities`, `clarinet/models/capability.py`) include it.
  Superuser/`admin` implicitly hold every capability.
- `ReportsAccessDep = Annotated[User, Depends(require_capability(Capability.REPORTS))]`
  — used by `reports.py` and `quarto_reports.py`.

Projects grant capabilities to roles in `settings.toml`:

```toml
[role_capabilities]
analyst = ["reports"]
```
Roles named here are auto-created at startup; unknown capabilities fail-fast.

- `mask_records(records, user)` — converts `Record` -> `RecordRead` + masks patient data for non-superusers. Lives in `clarinet/api/masking.py` (not `dependencies.py`); used by `record.py`

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
| `GET /studies/{uid}/series/{uid}/archive` | — | ensure cached -> ZIP of in-memory datasets |
| `POST /preload` | — | start background preload (1–20 study UIDs) -> `{task_id}` |
| `GET /preload/progress/{task_id}` | — | poll preload progress |

OHIF static files served at `/ohif` (conditional on `settings.ohif_enabled`).
