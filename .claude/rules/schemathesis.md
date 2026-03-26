---
paths:
  - "tests/schema/**"
  - "schemathesis.toml"
---

# Schema Tests (Schemathesis)

Property-based API testing using Schemathesis. Generates requests from OpenAPI schema
and validates response conformance, status codes, and absence of 500 errors.

## Running

```bash
make test-schema              # Quick run (max_examples=10)
make test-schema-verbose      # Verbose with tracebacks
```

## Architecture

- `tests/schema/conftest.py` — session-scoped fixtures: in-memory SQLite, auth overrides, no-op lifespan
- `tests/schema/test_api_schema.py` — parametrized tests over all API endpoints (Phase 1 + 2)
- `tests/schema/test_critical_endpoints.py` — per-endpoint tests for 8 critical endpoints (Phase 3, max_examples=200)
- `schemathesis.toml` — Schemathesis configuration (project root)
- Marker: `@pytest.mark.schema` — excluded from `make test-unit` and `make test-fast`

## Test structure

| Test | What it checks | Phase |
|---|---|---|
| `test_api_conformance` | Full schema conformance: response schema, status codes, content-type | Phase 1 |
| `test_no_server_errors` | No 500 errors on any generated input (positive + negative) | Phase 1 |
| `test_api_stateful` | CRUD chains via state machine (POST → GET → PATCH → DELETE) | Phase 2 |
| `test_create_record` | POST /api/records/ — level-UID validation, slug, DicomUID | Phase 3 |
| `test_submit_record_data` | POST /api/records/{id}/data — free-form JSON, state machine | Phase 3 |
| `test_update_record_data` | PATCH /api/records/{id}/data — inverse state guard | Phase 3 |
| `test_create_record_type` | POST /api/records/types — nested schema, file registry | Phase 3 |
| `test_update_record_type` | PATCH /api/records/types/{id} — optional fields, JSON parsing | Phase 3 |
| `test_find_records` | POST /api/records/find — RecordSearchQuery body | Phase 3 |
| `test_invalidate_record` | POST /api/records/{id}/invalidate — unvalidated mode | Phase 3 |
| `test_create_series` | POST /api/series — DicomUID, series_number boundaries | Phase 3 |

## Key design decisions

- **ASGI mode** (no running server): `schemathesis.openapi.from_asgi("/openapi.json", app=schema_app)`
- **No-op lifespan**: real lifespan uses `db_manager` directly (not DI), which conflicts with test DB.
  Schema tests replace it with `_noop_lifespan` and manage their own DB via `test_engine`.
- **Per-request sessions**: `override_get_session` creates a fresh session per request from a shared
  session factory. Prevents `PendingRollbackError` cascading across schemathesis requests.
- **Stateful testing via link injection**: API doesn't define OpenAPI `links`, so `conftest.py`
  injects CRUD links (POST-201 → GET/PATCH/DELETE) into the schema dict for the state machine.
  `stateful_api_schema` fixture uses `from_dict()` because link injection requires schema modification.
- **fastapi-users endpoints excluded**: `/api/auth/login`, `/logout`, `/register` — auto-generated, not under our control.

## Interpreting results

Schemathesis subtests show as `,` (pass) or `F` (fail) within a single parametrized test.
Common failure categories:
- **500 errors**: real bugs — fix the endpoint handler
- **Undocumented status codes**: add `responses=` to the router
- **Response schema violations**: fix `response_model` or serialization

## Schemathesis 4.x API quick reference

Schema loading:
- `schemathesis.openapi.from_asgi("/openapi.json", app)` — ASGI (no server), preferred for FastAPI
- `schemathesis.openapi.from_dict(schema_dict)` — from dict (set `.app = app` for ASGI transport)
- `schemathesis.pytest.from_fixture("fixture_name")` — lazy load in pytest

Stateful testing:
- `schema.as_state_machine()` → `APIStateMachine` subclass — requires OpenAPI `links` in responses
- `run_state_machine_as_test(sm, settings=)` from `schemathesis.generation.stateful`
- Does NOT infer transitions from URL patterns — only explicit `links`

Generation modes (via `schemathesis.toml`):
- `[generation] mode = "all"` — both valid + invalid data (default in our config)
- `schemathesis.GenerationMode.POSITIVE / NEGATIVE` — enum at `schemathesis.generation.modes`
