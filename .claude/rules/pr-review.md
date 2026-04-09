# Clarinet PR review checklist

Project-specific checklist read by the global `pr-diff-reviewer` subagent. Applied in addition to the universal checklist (C1–C10). Each check below is keyed `P<n>` to avoid collisions.

## Architecture (see CLAUDE.md, `.claude/rules/`)

- **P1.** DB access only through `clarinet/repositories/` — routers and services must not touch the session directly.
- **P2.** Business logic lives in `clarinet/services/`, not in routers or repositories.
- **P3.** API routers use dependency aliases from `clarinet/api/dependencies.py` — no ad-hoc `Depends(...)` calls.
- **P4.** Domain exceptions come from `clarinet/exceptions/` (e.g. `NOT_FOUND`, `CONFLICT`). Never raise bare `HTTPException` in services or repositories.
- **P5.** Exception flow: repos raise domain exceptions → exception handlers convert to HTTP responses. New exception types need a matching handler.

## Imports

- **P6.** Logger: `from clarinet.utils.logger import logger`. Never `import loguru` directly.
- **P7.** Settings: `from clarinet.settings import settings`. No ad-hoc `os.environ[...]` for settings that already exist.
- **P8.** Do NOT move runtime-needed imports into `TYPE_CHECKING` on FastAPI router files — DI uses `Annotated[...]` at runtime. (See `.claude/rules/api-deps.md`.)

## Database / migrations

- **P9.** Any schema change in `clarinet/models/*.py` has a matching Alembic migration in `alembic/versions/`.
- **P10.** Self-referencing SQLModel relationships use `Optional["ClassName"]` / `list["ClassName"]` with `# noqa: UP045, UP037` and `sa_relationship_kwargs={"remote_side": ..., "foreign_keys": ...}`. Do not let ruff auto-fix these.
- **P11.** Async DB URL conversion uses the `get_async_database_url()` helper from `clarinet.utils.db_manager`, not manual `str.replace("psycopg2", "asyncpg")`.
- **P12.** Alembic template `alembic/script.py.mako` stays self-contained: `import sqlmodel`, `from sqlalchemy import Text` (and any other symbols referenced in generated migrations) must be present.
- **P12a.** **New non-nullable columns on existing tables MUST declare `server_default`** (or be `Optional[...]`). Without it, alembic autogenerate emits `ALTER TABLE ... ADD COLUMN ... NOT NULL` and PostgreSQL refuses with `column "..." of relation "..." contains null values` on populated tables. For booleans use **`sql_expression.true()` / `sql_expression.false()`** from `sqlalchemy.sql` — NOT `text("1")`: a raw `text("1")` emits an integer literal on PG, which breaks CREATE TABLE and ALTER TABLE with "default for column is of type integer" because PG has no implicit int→bool cast. See in-line comment on `RecordTypeBase.mask_patient_data` and `clarinet/models/CLAUDE.md` → "Additive migrations on populated tables". Real bugs: PR #144 (the original) and PR #149 v1 (wrong fix).

## API surface

- **P13.** New mutable endpoints (POST/PUT/PATCH/DELETE) on records use `authorize_mutable_record_access` (or equivalent mutation-aware dependency), NOT the base `AuthorizedRecordDep` which only checks read access.
- **P14.** Any new endpoint is reflected in `.claude/rules/api-urls.md` (URL + status codes + auth requirements).
- **P15.** Request/response models are Pydantic/SQLModel — no raw dicts as public API contracts.

## Pipeline and async execution (`clarinet/services/pipeline/`)

- **P16.** New pipeline tasks use `@pipeline_task()` decorator (new style) or `@broker.task()` (old style) consistently — both return dict, check chain compatibility. See `.claude/rules/pipeline-ops.md`.
- **P17.** Inside pipeline tasks: use `TaskContext` / `FileResolver` / `RecordQuery`, not ad-hoc DB queries or file lookups.
- **P18.** `asyncio.gather` must not be applied to coroutines that share one `AsyncSession`.

## Tests

- **P19.** New service or repository function has a matching test: pure-unit tests go into root-level `tests/test_*.py`; tests that touch DB, HTTP, or external services go into `tests/integration/`.
- **P20.** Test output redirected to `/tmp/test-<worktree>.txt`, never piped through `| tail` / `| tee`.
- **P21.** Do not run multiple `make test-*` targets in parallel — DB and port conflicts.
- **P22.** For full "run all tests" — use `make test-all-stages`, not `make test`.
- **P23.** Schemathesis tests: if adding new endpoints, confirm there are no schemathesis boundary-value regressions (see memory `devops_schemathesis_boundary_bug.md`).

## Frontend (Gleam/Lustre)

- **P24.** Frontend lives in `clarinet/frontend/src/` as Gleam (not React/Vue). Selectors in E2E tests reference real Gleam components — check `.claude/rules/e2e-tests.md` when touching login/pages.
- **P25.** VM E2E tests go through the `PATH_PREFIX` sub-path from `deploy/vm/vm.conf`, never the root `/`.

## Code style specific to Clarinet

- **P26.** Async/await for all I/O in the app. Sync blocking ops inside an async function are a blocker.
- **P27.** f-string logging: `logger.info(f"user={user.id}")`, not `%` formatting.
- **P28.** `print()` is forbidden for diagnostics — always `logger.<level>(...)`.

## Path-scoped rules to re-read

When the diff touches these paths, open the corresponding rule file before finishing the review:

- `clarinet/api/routers/**`, `clarinet/api/dependencies.py` → `.claude/rules/api-deps.md`, `.claude/rules/api-urls.md`
- `clarinet/repositories/record_repository.py`, `clarinet/repositories/record_type_repository.py` → `.claude/rules/record-repo.md`
- `clarinet/services/pipeline/**`, `tests/**/*pipeline*` → `.claude/rules/pipeline-ops.md`
- `clarinet/services/slicer/context*.py`, `tasks/**/context_hydrators.py` → `.claude/rules/slicer-context.md`
- `clarinet/services/slicer/helper.py` → `.claude/rules/slicer-helper-api.md`
- `clarinet/services/recordflow/**`, `tasks/**/*_flow.py` → `.claude/rules/recordflow-dsl.md`
- `clarinet/models/file_schema.py`, `clarinet/repositories/file_definition_repository.py` → `.claude/rules/file-registry.md`
- `tests/schema/**`, `schemathesis.toml` → `.claude/rules/schemathesis.md`
- `tests/**`, `scripts/run_tests.sh` → `.claude/rules/test-debugging.md`
- `deploy/test/e2e/**` → `.claude/rules/e2e-tests.md`
- `settings.toml`, `plan/**`, `examples/**` → `.claude/rules/project-setup.md`
