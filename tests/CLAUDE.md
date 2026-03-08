# Testing Conventions

## Stack

- **pytest** + **pytest-asyncio** for async tests
- Configuration in `tests/conftest.py`
- Run: `make test`, `make test-fast`, `make test-cov`, `make test-integration`

## Structure

- `tests/integration/` — integration tests (API endpoints, CRUD)
- `tests/e2e/` — end-to-end tests (auth workflows)
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

## Guidelines

- Mock external dependencies
- Use fixtures (defined in `conftest.py`) for code reuse
- All async tests need `@pytest.mark.asyncio`
- Use `AsyncClient` from httpx for API testing
- `pytest.mark.pipeline` marker for tests requiring RabbitMQ (auto-skip when unreachable)

## Pitfalls

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

- `make test-fast` — parallel run (auto workers, excludes external services)
- `make test` — sequential run (all tests)

### Session-Scoped Engine

The test engine is session-scoped: schema is created once per worker, data is
cleaned via `DELETE FROM` after each test (autouse `clear_database` fixture).

Important: `StaticPool` is required for in-memory SQLite with session-scoped
engine — without it, each new connection creates a new empty database.
