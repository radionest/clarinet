# API Layer Guide

DI aliases, RBAC deps, factory patterns, DICOMweb endpoints: `.claude/rules/api-deps.md` (auto-loaded for dependencies.py and routers).

### Router Auth Levels

Changing auth levels on routers has cascading impact on tests — check `tests/test_client.py`,
`tests/integration/test_study_crud.py`, and all e2e test fixtures.

| Router | Auth Level | Notes |
|--------|-----------|-------|
| `record.py` | `CurrentUserDep` | Role-based filtering on list/find endpoints; `AuthorizedRecordDep` on single-record endpoints |
| `study.py` | `current_admin_user` | Admin-only (patients, studies, series): is_superuser OR `admin` role |
| `record_type.py` | `current_superuser` | Admin-only for mutations; read is open to authenticated |
| `user.py` | `AdminUserDep` | Admin-only mutations: is_superuser OR `admin` role; `/me` and `/me/roles` are open to any authenticated user |
| `admin.py` | `AdminUserDep` | Admin-only: is_superuser OR `admin` role |
| `reports.py` | `AdminUserDep` | Admin-only: is_superuser OR `admin` role |
| `dicom.py` | mixed | `search_patient_studies` + `import_study_from_pacs` use `AdminUserDep`; `anonymize_study` stays `SuperUserDep` |
| `dicomweb.py` | `CurrentUserDep` | Any authenticated user |

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

## Middleware (middleware.py)

- `NullQueryParamMiddleware` — strips query params with null-like values (`"null"`, `"Null"`, `"NULL"`) so FastAPI treats them as absent (uses `None` default). Only re-encodes the query string when params are actually removed. Controlled by `settings.coerce_null_query_params` (default `True`). Added after CORS in `create_app()`.

## Exception Handlers (exception_handlers.py)

`setup_exception_handlers(app)` maps domain exceptions → HTTP responses.
Routers don't need try/except — just let domain exceptions propagate.
See `clarinet/api/exception_handlers.py` for the full mapping.

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

Invalidation (routes through RecordService):
- `POST /records/{id}/invalidate` — body: `{mode, source_record_id, reason}`; hard mode fires RecordFlow triggers (enables auto task restart)

## SPA Frontend Routing

When `frontend_enabled=True`, catch-all `/{full_path:path}` serves:
- Static file if exists in `settings.static_directories`
- `index.html` otherwise (SPA client-side routing)
- Skips paths starting with `api/`, `dicom-web/`, or `ohif/`

## URL Reference for Tests

URL constants: `tests/utils/urls.py`. Full endpoint table: `.claude/rules/api-urls.md` (loaded automatically when editing tests or routers).

Status code conventions: 201 = POST create, 204 = DELETE/bulk, 200 = default.
