# Testing Conventions

## Stack

- **pytest** + **pytest-asyncio** for async tests
- Configuration in `tests/conftest.py`
- Run: `make test-fast` (default), `make test-unit`, `make test`, `make test-cov`, `make test-integration`, `make test-schema`

## Structure

- `tests/integration/` — integration tests (API endpoints, CRUD)
- `tests/e2e/` — end-to-end tests (auth workflows)
- `tests/schema/` — Schemathesis property-based API schema tests
- `tests/utils/` — test helpers
- Root `tests/` — unit tests (client, file patterns, validation)

## Key Test Files

- `tests/test_recordflow_dsl.py` — unit tests for RecordFlow DSL (FlowResult, comparisons, FlowRecord builder, engine unit tests with mocked client)
- `tests/integration/test_recordflow.py` — integration tests for RecordFlow (engine with real DB, API-triggered flows, invalidation, direct invalidate endpoint)
- `tests/test_client.py` — ClarinetClient unit tests with mocked HTTP
- `tests/test_pipeline.py` — unit tests for Pipeline service (message models, chain DSL, worker queues, exceptions)
- `tests/integration/test_pipeline_integration.py` — integration tests for Pipeline service (real RabbitMQ: broker connectivity, task dispatch/routing/execution, multi-step chains, middleware)
- `tests/integration/test_app_startup.py` — regression tests for app startup with different pipeline settings (lifespan + lazy client login)
- `tests/test_dicomweb_cache.py` — unit tests for DICOMweb two-tier cache (memory + disk)
- `tests/test_dicomweb_cleanup.py` — unit tests for DICOMweb cache cleanup service
- `tests/test_dicomweb_converter.py` — unit tests for DICOMweb data converters
- `tests/test_config_loader.py` — unit tests for config loader (TOML/JSON discovery, file references, schema resolution)
- `tests/integration/test_config_reconciler.py` — integration tests for config reconciler (create/update/unchanged/orphan/delete, file_registry + data_schema diffs)
- `tests/integration/test_config_toml_sync.py` — integration tests for TOML bidirectional sync (bootstrap from TOML, export, round-trip)
- `tests/integration/test_config_python_mode.py` — integration tests for Python config mode (loader, FileRef resolution, schema sidecars)
- `tests/integration/test_parent_child.py` — integration tests for parent-child relationships (DAG validation, parent record type matching, API endpoints, config reconciler, search criteria, user_id inheritance)
- `tests/test_schema_hydration.py` — unit tests for schema hydration (registry, walker, built-in study_series hydrator, edge cases)
- `tests/integration/test_schema_hydration_api.py` — integration tests for schema hydration API (GET /records/{id}/schema, POST data validation against hydrated oneOf)

## Guidelines

- Mock external dependencies
- Use fixtures (defined in `conftest.py`) for code reuse
- All async tests need `@pytest.mark.asyncio`
- Use `AsyncClient` from httpx for API testing
- `pytest.mark.pipeline` marker for tests requiring RabbitMQ (auto-skip when unreachable)

## Auth Fixtures Reference

| Fixture | Auth Level | Source | Used By |
|---------|-----------|--------|---------|
| `client` | superuser (overridden) | `conftest.py` | Most integration + unit tests |
| `unauthenticated_client` | no auth | `conftest.py` | Auth workflow e2e tests |
| `clarinet_client` | real login (cookies) | `conftest.py` | RecordFlow integration, ClarinetClient tests |
| `fresh_client` | session override only | `conftest.py` | Lazy-load regression tests |

### Mock User Helper

Use `create_mock_superuser()` and `create_authenticated_client()` from `tests/conftest.py`
when overriding the `client` fixture in e2e tests:

```python
from tests.conftest import create_authenticated_client, create_mock_superuser

@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    mock_user = await create_mock_superuser(test_session, email="my_test@test.com")
    async for ac in create_authenticated_client(mock_user, test_session, test_settings):
        yield ac
```

`create_mock_superuser` **expunges** the user after `refresh()` — this prevents
`MissingGreenlet` when other fixtures (e.g. `demo_record_types`) call
`session.expire_all()`. Without expunge, accessing `user.is_superuser` in an
endpoint triggers a lazy-load on the expired object in async context.

## Pitfalls

### MagicMock Auto-Creates Attributes

`MagicMock()` returns a new mock object (truthy) for any attribute access — not `None`.
When production code adds `if record.field is not None`, all mock records in tests
must explicitly set `record_mock.field = None` or the branch will execute unexpectedly.

Prefer `spec=` to constrain mocks:
```python
record_mock = MagicMock(spec=Record)
record_mock.id = 1
record_mock.parent_record_id = None  # explicit — MagicMock default is NOT None
```

### Identity Map Caching

`expire_on_commit=False` is set globally (both production and tests). After creating
M2M links and `commit()` in the same session, `selectinload` will NOT reload a
relationship that is already cached in the identity map.

Fix: call `session.expire_all()` (or `session.expire(entity)`) before re-fetching:
```python
session.add(link)
await session.commit()
session.expire_all()  # clear cached empty collection

result = await session.execute(
    select(Model).options(selectinload(Model.links).selectinload(Link.child))
)
```

This only affects tests — production endpoints get a fresh session per request.

**Reconciler tests:** When calling `reconcile_record_types()` twice in a row
(e.g. create then update), `FileDefinition` attributes cached from the first
pass will be stale. Call `session.expire_all()` between passes:
```python
await reconcile_record_types(config_v1, test_session)
test_session.expire_all()  # flush cached FileDefinition from first reconcile
await reconcile_record_types(config_v2, test_session)
```

### `fresh_session` Fixture

Use `fresh_session` (from `conftest.py`) instead of `test_session` when you need to
verify eager loading works correctly. It provides an empty identity map, simulating
production behavior and catching `MissingGreenlet` errors that `test_session` masks.

### Module-level Singletons in Tests

Calling `shutdown()` on module-level singletons (thread pools, brokers, DB engines)
breaks subsequent `lifespan()` invocations in the same test process.

Two solutions:
1. **`_reset_singletons` fixture** — save and restore originals around each test
   (see `tests/integration/test_app_startup.py:62`):
   ```python
   @pytest.fixture(autouse=True)
   def _reset_singletons():
       import clarinet.some_module as mod
       orig = mod._singleton
       yield
       mod._singleton = orig
   ```
2. **Re-create in shutdown** — the shutdown function itself replaces the resource
   (see `clarinet/utils/fs.py:shutdown_fs_executor`):
   ```python
   def shutdown_resource():
       global _resource
       _resource.shutdown()
       _resource = _make_resource()  # ready for next lifespan
   ```

## API Test Patterns

### URL Constants

Use `tests/utils/urls.py` instead of hardcoded URL strings. Full reference in `clarinet/api/CLAUDE.md`.

```python
from tests.utils.urls import RECORDS_BASE, RECORD_TYPES

resp = await client.post(RECORD_TYPES, json={...})
resp = await client.get(f"{RECORDS_BASE}/{record_id}")
resp = await client.patch(f"{RECORD_TYPES}/{name}", json={...})
```

### Model Factories

Two modules serve different purposes:

| Module | Style | DB? | Use when |
|---|---|---|---|
| `tests/utils/factories.py` | Sync functions (`make_patient()`) | No — returns instance | Building model objects for repo-level tests, seeding fixtures |
| `tests/utils/test_helpers.py` | Async Factory classes (`PatientFactory.create_patient()`) | Yes — adds + commits | Need a fully persisted entity with DB-generated fields |

```python
# Lightweight instance (not persisted)
from tests.utils.factories import make_patient, make_user, seed_record

pat = make_patient("PAT_001", "Alice")
session.add(pat)
await session.commit()

# Async factory (persisted automatically)
from tests.utils.test_helpers import PatientFactory

pat = await PatientFactory.create_patient(session, patient_id="PAT_001")
```

### Fixture Hierarchy

| Fixture | Scope | Source | Purpose |
|---|---|---|---|
| `test_engine` | session | `conftest.py` | Async SQLAlchemy engine (one per worker, StaticPool) |
| `test_session` | function | `conftest.py` | Async SQLAlchemy session (DELETE cleanup per test) |
| `fresh_session` | function | `conftest.py` | Clean identity map — simulates production |
| `client` | function | `conftest.py` | `httpx.AsyncClient` bound to test app |

### Expected Status Codes

| Pattern | Status |
|---|---|
| `POST` create (records, types, patients, studies, series, users, roles) | 201 |
| `DELETE` entity / bulk operations | 204 |
| `GET`, `PATCH`, `PUT`, other `POST` | 200 |
| Entity not found | 404 |
| Duplicate / conflict | 409 |
| Validation error / business rule | 422 |

## Parallel Test Execution

Tests support parallel execution via pytest-xdist. Each worker runs in a
separate process with its own in-memory SQLite database.

### Test Commands

| Command | What runs | Parallel | Use when |
|---|---|---|---|
| `make test-fast` | All tests except schema (default) | `-n auto` | Default — includes all service groups |
| `make test-unit` | DB-only tests | `-n auto` | No RabbitMQ/DICOM/Slicer available |
| `make test` | All tests | sequential | Debugging test order issues |
| `make test-integration` | `tests/integration/` | sequential | Integration subset only |

### Service Groups & Isolation

Tests are safe to run in parallel across all groups:

| Group | Marker | External service | Why parallel-safe |
|---|---|---|---|
| DB-only | _(none)_ | SQLite in-memory | Each xdist worker gets its own DB (StaticPool) |
| Pipeline | `pipeline` | RabbitMQ | Unique exchange/queue names per session (`uuid4`) |
| DICOM | `dicom` | PACS server | Read-only queries |
| Slicer | `slicer` | 3D Slicer | Auto-skipped if unreachable |

Unreachable services auto-skip via `_check_rabbitmq` / `_check_slicer` fixtures.

### Session-Scoped Engine

The test engine is session-scoped: schema is created once per worker, data is
cleaned via `DELETE FROM` after each test (autouse `clear_database` fixture).

Important: `StaticPool` is required for in-memory SQLite with session-scoped
engine — without it, each new connection creates a new empty database.

## Background and CI Test Runs

All `make test-*` targets use `scripts/run_tests.sh` which prints a `=== Test Summary ===`
line with pass/fail/skip counts parsed from the JSON report via `jq`.

- JSON report: `/tmp/clarinet-test-report.json` — written **atomically at session end**
- During a background run the file contains **stale data from the previous run**
- To get results from a background run: wait for completion, then read the summary
- pynetdicom loguru errors ("I/O operation on closed file") at end of output are **noise**, not test failures — suppressed via `_suppress_pynetdicom_logging` fixture in `conftest.py`

## Debugging Test Failures

Always capture output on the first run — never re-run tests just to see logs.

### Run tests (JSON output is automatic via addopts)

```bash
make test-fast                    # JSON report → /tmp/clarinet-test-report.json
CLARINET_LOG_DIR=/tmp make test-fast  # + app logs → /tmp/clarinet.log
make test-debug                   # both at once
```

### Analyze test failures (jq)

```bash
# Failed tests — names + error messages
jq '.tests[] | select(.outcome == "failed") | {nodeid, message: .call.longrepr}' /tmp/clarinet-test-report.json

# Just the names of failed tests
jq -r '.tests[] | select(.outcome == "failed") .nodeid' /tmp/clarinet-test-report.json

# Test durations (slowest first)
jq '[.tests[] | {nodeid, duration}] | sort_by(-.duration) | .[:10]' /tmp/clarinet-test-report.json

# Summary
jq '.summary' /tmp/clarinet-test-report.json
```

### Analyze app logs (jq)

App logs are written to `/tmp/clarinet.log` in JSON-lines format when `CLARINET_LOG_DIR=/tmp`.

```bash
# App errors
jq 'select(.l == "ERROR")' /tmp/clarinet.log

# Errors with tracebacks
jq 'select(.exc != null)' /tmp/clarinet.log

# Filter by module
jq 'select(.mod | startswith("clarinet.services.pipeline"))' /tmp/clarinet.log
```

### JSON log keys (app logger)

| Key | Content |
|-----|---------|
| `t` | ISO timestamp |
| `l` | Level (INFO, ERROR, ...) |
| `mod` | Module name |
| `fn` | Function name |
| `line` | Line number |
| `msg` | Log message |
| `exc` | Traceback (only on exceptions) |

## Schema Tests (Schemathesis)

Property-based API testing using Schemathesis. Generates requests from OpenAPI schema
and validates response conformance, status codes, and absence of 500 errors.

### Running

```bash
make test-schema              # Quick run (max_examples=10)
make test-schema-verbose      # Verbose with tracebacks
```

### Architecture

- `tests/schema/conftest.py` — session-scoped fixtures: in-memory SQLite, auth overrides, no-op lifespan
- `tests/schema/test_api_schema.py` — parametrized tests over all API endpoints
- `schemathesis.toml` — Schemathesis configuration (project root)
- Marker: `@pytest.mark.schema` — excluded from `make test-unit` and `make test-fast`

### Key design decisions

- **ASGI mode** (no running server): `schemathesis.openapi.from_dict(app.openapi())` + `loaded.app = app`
- **No-op lifespan**: real lifespan uses `db_manager` directly (not DI), which conflicts with test DB.
  Schema tests replace it with `_noop_lifespan` and manage their own DB via `test_engine`.
- **Schema loaded via `app.openapi()`**: avoids triggering lifespan that `from_asgi()` would trigger.
- **Per-request sessions**: `override_get_session` creates a fresh session per request from a shared
  session factory. Prevents `PendingRollbackError` cascading across schemathesis requests.
- **fastapi-users endpoints excluded**: `/api/auth/login`, `/logout`, `/register` — auto-generated, not under our control.

### Interpreting results

Schemathesis subtests show as `u` (pass) or `F` (fail) within a single parametrized test.
Common failure categories:
- **500 errors**: real bugs — fix the endpoint handler
- **Undocumented status codes**: add `responses=` to the router
- **Response schema violations**: fix `response_model` or serialization
