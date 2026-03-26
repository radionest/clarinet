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

Test files follow naming convention: `test_{feature}.py` (unit), `integration/test_{feature}.py` (integration).
Major groups: recordflow DSL, pipeline (+ real RabbitMQ), dicomweb cache, config loader/reconciler/TOML sync, parent-child, schema hydration, app startup regression.
E2E: `tests/e2e/test_slicer_pacs_workflow.py` — Slicer ↔ PACS (C-GET/C-MOVE) without mocks. Requires running 3D Slicer + Orthanc PACS. Uses `xdist_group("slicer")`.

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

`shutdown()` on singletons breaks subsequent `lifespan()` calls. Two fixes:
1. `_reset_singletons` fixture — save/restore originals (see `test_app_startup.py:62`)
2. Re-create in shutdown — `_resource = _make_resource()` after shutdown (see `fs.py:shutdown_fs_executor`)

## API Test Patterns

### URL Constants

Use `tests/utils/urls.py` instead of hardcoded URL strings. Full endpoint table in `.claude/rules/api-urls.md`.

### Model Factories

- `tests/utils/factories.py` — sync, no DB: `make_patient()`, `make_user()`, `seed_record()`
- `tests/utils/test_helpers.py` — async, persisted: `PatientFactory.create_patient()`, etc.

Both share `next_auto_id()` counter — never create `Patient(...)` directly in tests.

### Fixture Hierarchy

`test_engine` (session) → `test_session` (function, DELETE cleanup) → `client` (function, httpx).
`fresh_session` — clean identity map, simulates production (catches MissingGreenlet).

### Expected Status Codes

POST create → 201, DELETE → 204, everything else → 200. Not found → 404, conflict → 409, validation → 422.

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
| Pipeline | `pipeline` | RabbitMQ | Unique exchange/queue names per session (`uuid4`) + `xdist_group("pipeline")` |
| DICOM | `dicom` | PACS server | Read-only queries |
| Slicer | `slicer` | 3D Slicer | `xdist_group("slicer")` — all slicer tests serialized on one worker (shared Slicer process) |

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

Detailed jq commands and log analysis: `.claude/rules/test-debugging.md` (auto-loaded for tests/).

Quick reference: `make test-debug` runs tests with JSON report + app logs. Analyze with `jq` on `/tmp/clarinet-test-report.json` and `/tmp/clarinet.log`.

## Schema Tests (Schemathesis)

Detailed guide: `.claude/rules/schemathesis.md` (auto-loaded for tests/schema/).

Quick reference: `make test-schema` (quick), `make test-schema-verbose` (verbose). Marker: `@pytest.mark.schema` — excluded from `make test-unit` and `make test-fast`.
