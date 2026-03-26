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

- `tests/schema/conftest.py` — session-scoped fixtures: in-memory SQLite, auth overrides, no-op lifespan, CRUD link injection
- `tests/schema/test_api_schema.py` — parametrized tests over all API endpoints (Phase 1 + 2)
- `tests/schema/test_medium_endpoints.py` — medium-depth tests for read/list endpoints (Phase 1b, max_examples=50)
- `tests/schema/test_critical_endpoints.py` — per-endpoint tests for 21 critical endpoints (Phase 3, max_examples=200)
- `schemathesis.toml` — Schemathesis configuration (project root)
- Marker: `@pytest.mark.schema` — excluded from `make test-unit` and `make test-fast`

## Test structure

| Test | What it checks | Phase |
|---|---|---|
| `test_api_conformance` | Full schema conformance: response schema, status codes, content-type | Phase 1 |
| `test_no_server_errors` | No 500 errors on any generated input (positive + negative) | Phase 1 |
| `test_record_read_endpoints` | GET records (list, my, pending, available_types, by ID, schema) | Phase 1b |
| `test_record_type_read_endpoints` | GET record types (list, by ID) | Phase 1b |
| `test_study_read_endpoints` | GET patients/studies/series | Phase 1b |
| `test_user_read_endpoints` | GET/PUT users, roles | Phase 1b |
| `test_admin_read_endpoints` | GET admin stats, role-matrix | Phase 1b |
| `test_api_stateful` | CRUD chains via state machine (POST → GET → PATCH → DELETE) | Phase 2 |
| `test_create_record` | POST /api/records/ — level-UID validation, slug, DicomUID | Phase 3 |
| `test_submit_record_data` | POST /api/records/{id}/data — free-form JSON, state machine | Phase 3 |
| `test_update_record_data` | PATCH /api/records/{id}/data — inverse state guard | Phase 3 |
| `test_create_record_type` | POST /api/records/types — nested schema, file registry | Phase 3 |
| `test_update_record_type` | PATCH /api/records/types/{id} — optional fields, JSON parsing | Phase 3 |
| `test_find_records` | POST /api/records/find — RecordSearchQuery body | Phase 3 |
| `test_invalidate_record` | POST /api/records/{id}/invalidate — unvalidated mode | Phase 3 |
| `test_create_series` | POST /api/series — DicomUID, series_number boundaries | Phase 3 |
| `test_update_record_status` | PATCH /records/{id}/status — state machine transitions | Phase 3 |
| `test_assign_record_user` | PATCH /records/{id}/user — UUID, RBAC, RecordFlow | Phase 3 |
| `test_bulk_update_record_status` | PATCH /records/bulk/status — list body, RBAC | Phase 3 |
| `test_update_record` | PATCH /records/{id} — partial update, exclude_unset | Phase 3 |
| `test_check_record_files` | POST /records/{id}/check-files — checksums, auto-unblock | Phase 3 |
| `test_validate_record_files` | POST /records/{id}/validate-files — file validation | Phase 3 |
| `test_find_record_types` | POST /records/types/find — RecordTypeFind search | Phase 3 |
| `test_delete_record_type` | DELETE /records/types/{id} — cascade, 204 | Phase 3 |
| `test_find_series` | POST /series/find — SeriesFind with nested RecordFind | Phase 3 |
| `test_admin_assign_record` | PATCH /admin/records/{id}/assign — superuser-only | Phase 3 |
| `test_admin_update_record_status` | PATCH /admin/records/{id}/status — bypass guards | Phase 3 |
| `test_admin_unassign_record_user` | DELETE /admin/records/{id}/user — unassign + reset | Phase 3 |

## Key design decisions

- **ASGI mode** (no running server): `schemathesis.openapi.from_asgi("/openapi.json", app=schema_app)`
- **No-op lifespan**: real lifespan uses `db_manager` directly (not DI), which conflicts with test DB.
  Schema tests replace it with `_noop_lifespan` and manage their own DB via `test_engine`.
- **Per-request sessions**: `override_get_session` creates a fresh session per request from a shared
  session factory. Prevents `PendingRollbackError` cascading across schemathesis requests.
- **Stateful testing via link injection**: API doesn't define OpenAPI `links`, so `conftest.py`
  injects CRUD links (POST-201 → GET/PATCH/DELETE) into the schema dict for the state machine.
  `stateful_api_schema` fixture uses `from_dict()` because link injection requires schema modification.
  CRUD chains: RecordType (full CRUD), Patient (get/delete/anonymize), User (get/update/delete/roles),
  Role (get), Study (get/series/delete), Series (get), Record (get/status/update/invalidate/check-files).
- **Excluded endpoints**: `/api/auth/login`, `/logout`, `/register` (fastapi-users auto-generated);
  `/api/records/{id}/submit` (Slicer-dependent); `/api/dicom/*`, `/api/slicer/*`, `/dicom-web/*` (external services).

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
