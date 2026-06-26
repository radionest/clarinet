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
| `reports.py` | `ReportsAccessDep` | Capability-gated: superuser/`admin` OR a role mapped to `reports` in `settings.role_capabilities`. Same guard on `quarto_reports.py` |
| `dicom.py` | mixed | `search_patient_studies` + `import_study_from_pacs` use `AdminUserDep`; `anonymize_study` stays `SuperUserDep` |
| `dicomweb.py` | `CurrentUserDep` | Any authenticated user |

## Application Lifespan (app.py)

Startup sequence:
1. Database init (`db_manager.create_db_and_tables_async()`)
1b. Default roles (`add_default_user_roles()`)
1c. Anchor the `clarinet_plan` package + load registries: `activate_plan_package(config_tasks_path)` → `_ensure_record_types_imported()` (imports record types, sets FileDef names before validators read them) → `_load_plan_registries()` (clears the 3 registries + `_register_builtin_hydrators()`, then `load_custom_validators/hydrators/slicer_hydrators`). Must run BEFORE `reconcile_config` so the registries are populated when reconcile validates `RecordType.data_validators` and `RecordType.slicer_context_hydrators` names. Loading contract: `.claude/rules/custom-code-loading.md`
2. Config reconciliation (`reconcile_config()`) → stores `app.state.config_mode`, `app.state.config_tasks_path`; fail-fasts on unknown role_names, validator names, and slicer-hydrator names
2b. Load project file registry from tasks folder
3. Admin user creation (`ensure_admin_exists()`)
4. RecordFlow engine setup (if `recordflow_enabled`) → `app.state.recordflow_engine`
5. Pipeline broker startup (if `pipeline_enabled`) → `app.state.pipeline_broker`; syncs pipeline definitions to DB
6. Session cleanup service start (if `session_cleanup_enabled`)
7. DICOMweb cache init (if `dicomweb_enabled`) → `app.state.dicomweb_cache`; cleanup service → `app.state.dicomweb_cleanup`

Any `ConfigLoadError` from steps 1c/2/2b/4/5 (a broken plan/ `.py` file) is converted to
`StartupError(component="Config")` — the server refuses to start instead of running without
custom validators/hydrators/flows. Loading contract: `.claude/rules/custom-code-loading.md`.

Shutdown (reverse order): stop DICOMweb cleanup → flush DICOMweb cache → stop session cleanup → shutdown pipeline broker → close RecordFlow client → close DB.

## Middleware (middleware.py)

- `NullQueryParamMiddleware` — strips query params with null-like values (`"null"`, `"Null"`, `"NULL"`) so FastAPI treats them as absent (uses `None` default). Only re-encodes the query string when params are actually removed. Controlled by `settings.coerce_null_query_params` (default `True`). Added after CORS in `create_app()`.

## Exception Handlers (exception_handlers.py)

`setup_exception_handlers(app)` maps domain exceptions → HTTP responses.
Routers don't need try/except — just let domain exceptions propagate.
See `clarinet/api/exception_handlers.py` for the full mapping.

## Pipeline Router (pipeline.py)

Mounted at `/api/pipelines` (unconditionally). Endpoints:
- `GET /api/pipelines/{name}/definition` — get definition by name (used by `PipelineChainMiddleware`); no auth (workers)
- `POST /api/pipelines/sync` — re-sync pipeline definitions to DB on demand; no auth
- `POST /api/pipelines/runs` / `PATCH /api/pipelines/runs/{task_id}` — task run audit rows written by `AuditMiddleware` (`AdminUserDep`; service token resolves to admin — regular users must not forge audit)
- `GET /api/pipelines/runs[/{task_id}]` — list/get runs (`AdminUserDep`)

Uses `PipelineDefinitionRepositoryDep` + `PipelineTaskRunRepositoryDep`. Record-scoped view: `GET /api/records/{id}/runs` in record.py (`AuthorizedRecordDep`).

## Config Mode Guards

Config mode guards on `/types` endpoints — see `clarinet/config/CLAUDE.md`.

## RecordFlow Integration

RecordFlow triggers are dispatched via the **service layer**, not directly from routers:

- `RecordService` wraps record mutations (`update_status`, `assign_user`, `submit_data`, `update_data`, `notify_file_change`, `bulk_update_status`, `notify_file_updates`) and fires the appropriate engine trigger (awaited directly).
- `StudyService` fires entity-creation triggers via `engine.fire()` (fire-and-forget) in `create_patient()`, `create_study()`, `create_series()`.
- Engine is injected via `get_recordflow_engine(request)` in `dependencies.py` (returns `None` when disabled).

Invalidation (routes through RecordService):
- `POST /records/{id}/invalidate` — body: `{mode, source_record_id, reason}`; hard mode fires RecordFlow triggers (enables auto task restart)

## Record Audit Trail

`RecordService` appends a `RecordEvent` row right after every mutation and
**before** the RecordFlow dispatch (kinds: created / status_changed /
data_submitted / data_updated / assigned / unassigned / failed / invalidated /
context_info_updated / files_cleared / deleted with snapshot; machine markers
like claim/bulk/cascade go into `new_value.via`, `reason` stays human text).
The actor comes from `AuditActorDep` (`dependencies.py`, backed by
`auth_config.is_service_request`): the current user's UUID, or `None` when
the request authenticated via `X-Internal-Token` (pipeline workers,
RecordFlow) — every mutating endpoint passes `actor_id=actor` into the
service. `record_event.record_key` is a denormalized record id without FK —
it keeps a deleted record's history correlatable after `record_id` goes NULL.
Prefill writes are deliberately not audited (high-volume system noise).
Read endpoints: `GET /records/{id}/events` (AuthorizedRecordDep, oldest
first) and `GET /admin/records/events/deleted`. Downstream projects need an
alembic migration for the `record_event` table.

## SPA Frontend Routing

When `frontend_enabled=True`, catch-all `/{full_path:path}` serves:
- Static file if exists in `settings.static_directories`
- `index.html` otherwise (SPA client-side routing)
- Skips paths starting with `api/`, `dicom-web/`, or `ohif/`

## URL Reference for Tests

URL constants: `tests/utils/urls.py`. Full endpoint table: `.claude/rules/api-urls.md` (loaded automatically when editing tests or routers).

Status code conventions: 201 = POST create, 204 = DELETE/bulk, 200 = default.
