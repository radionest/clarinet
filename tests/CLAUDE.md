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

## Guidelines

- Mock external dependencies
- Use fixtures (defined in `conftest.py`) for code reuse
- All async tests need `@pytest.mark.asyncio`
- Use `AsyncClient` from httpx for API testing
