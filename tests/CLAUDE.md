# Testing Conventions

## Stack

- **pytest** + **pytest-asyncio** for async tests
- Configuration in `tests/conftest.py`
- Run: `make test`, `make test-cov`, `make test-integration`

## Structure

- `tests/integration/` — integration tests (API endpoints, CRUD)
- `tests/e2e/` — end-to-end tests (auth workflows)
- `tests/utils/` — test helpers
- Root `tests/` — unit tests (client, file patterns, validation)

## Key Test Files

- `tests/test_recordflow_dsl.py` — unit tests for RecordFlow DSL (FlowResult, comparisons, FlowRecord builder, engine unit tests with mocked client)
- `tests/integration/test_recordflow.py` — integration tests for RecordFlow (engine with real DB, API-triggered flows, invalidation, direct invalidate endpoint)
- `tests/test_client.py` — ClarinetClient unit tests with mocked HTTP

## Guidelines

- Mock external dependencies
- Use fixtures (defined in `conftest.py`) for code reuse
- All async tests need `@pytest.mark.asyncio`
- Use `AsyncClient` from httpx for API testing
