# Backend Development Guide

## Async Programming

- Use `AsyncSession` for DB operations: `get_async_session` from `clarinet.utils.database`

### `asyncio.gather` + AsyncSession

`AsyncSession` is **not concurrency-safe**. All repositories in a single request share one session via FastAPI DI.

`asyncio.gather` with **read-only SELECT queries** (count, list, scalar aggregates) is safe because:
- Connection is pre-provisioned in `get_async_session` (`await session.connection()`)
- No identity map mutations, no flush, no state transitions

`asyncio.gather` with **write operations** (add, flush, delete, update) on the same session is **NOT safe** — will cause `InvalidRequestError` or `IllegalStateChangeError`. Use sequential `await` for writes.

## Lifespan Shutdown Pattern

Resources initialized in `lifespan()` and shut down in its `finally` block must be **re-creatable** for test compatibility. Multiple tests may invoke `lifespan()` sequentially in the same process. If `shutdown()` destroys a module-level singleton without recreating it, subsequent lifespans fail.

Pattern: shutdown the old resource, then immediately replace it with a fresh instance:
```python
# clarinet/utils/fs.py — reference implementation
def shutdown_fs_executor() -> None:
    global _fs_executor
    _fs_executor.shutdown(wait=False)
    _fs_executor = _make_executor()  # re-create for next lifespan
```

## Type Annotations & Imports

- Avoid circular imports; use relative imports within package
- Common type aliases in `clarinet/types.py` — read file directly for full list

### Type Parameters (PEP 695)

Use PEP 695 syntax for generics — Ruff UP040 flags old-style `TypeVar`:
```python
# Good — PEP 695
def func[T](x: T) -> T: ...
type Alias[T] = list[T]

# Bad — flagged by UP040
T = TypeVar("T")
def func(x: T) -> T: ...
```
Legacy `TypeVar` in `clarinet/repositories/base.py` and `clarinet/utils/common.py` is pre-existing and exempt until refactored.

## Error Handling

- Try blocks: max two function calls; no large try-except blocks
- Always log errors before handling

```python
from clarinet.exceptions.http import NOT_FOUND, CONFLICT
from clarinet.utils.logger import logger

try:
    item = await session.get(Model, item_id)
    await session.commit()
except IntegrityError as e:
    await session.rollback()
    logger.error(f"Database integrity error: {e}")
    raise CONFLICT.with_context("Item already exists")
```

## Logging

- Levels: DEBUG (detail), INFO (events), WARNING (unexpected), ERROR (failures), CRITICAL (system)

```python
from clarinet.utils.logger import logger

logger.info(f"User {user_id} created new record")
logger.error(f"Failed to connect to database: {error}")
```

### Log File Format

File logs default to JSON-lines (`log_serialize=True`). Each line has short keys:

| Key | Content |
|-----|---------|
| `t` | ISO timestamp |
| `l` | Level (INFO, ERROR, …) |
| `mod` | Module name |
| `fn` | Function name |
| `line` | Line number |
| `msg` | Log message |
| `exc` | Traceback (only on exceptions) |

Set `CLARINET_LOG_SERIALIZE=false` for plain-text file logs.

### Searching JSON Logs

```bash
# All errors
jq 'select(.l == "ERROR")' clarinet.log

# Errors with tracebacks
jq 'select(.exc != null)' clarinet.log

# Filter by module
jq 'select(.mod | startswith("clarinet.services"))' clarinet.log

# Plain grep still works
grep '"l":"ERROR"' clarinet.log
```

## Configuration

- Env vars: `CLARINET_` prefix; TOML files: `settings.toml`, `settings.custom.toml`
- See `clarinet/settings.py` for all available settings (admin, session, recordflow, etc.)

### RecordType Config System

Config modes (TOML / Python) — see `clarinet/config/CLAUDE.md` for details.

Config loader: `clarinet/utils/config_loader.py` (TOML/JSON discovery).
- TOML takes precedence when both formats exist for the same stem
- Sidecar schema: `{name}.schema.json` loaded automatically when `data_schema` absent
- `file_registry.toml`/`.json` and `*.schema.json` excluded from config discovery

Bootstrap uses `reconcile_config()` from `clarinet/utils/bootstrap.py` — dispatches by mode.

## Database & API

- SQLModel for models; AsyncSession for operations — see `models/CLAUDE.md`
- Repository pattern in `clarinet/repositories/` — see `repositories/CLAUDE.md`
- API DI aliases and router patterns — see `api/CLAUDE.md`
- Never query DB directly in routers; use repository/service layer

## Service Layer Overview

| Directory | Purpose |
|---|---|
| `services/recordflow/` | Record processing DSL engine; reacts to Record status/data changes |
| `services/pipeline/` | Distributed task queue (TaskIQ + RabbitMQ); long ops (GPU, DICOM chains) |
| `services/dicomweb/` | DICOMweb proxy with two-tier cache (memory + disk) for OHIF |
| `services/dicom/` | DICOM client (pynetdicom): C-FIND/C-GET/C-STORE; anonymization (`anonymizer.py`) |
| `services/slicer/` | 3D Slicer integration via HTTP API; DSL helper and PacsHelper |
| `services/image/` | Image processing utilities (numpy) |
| `services/admin_service.py` | Aggregated admin logic |
| `services/user_service.py` | User and role management |
| `services/study_service.py` | Study management (+ entity-creation RecordFlow triggers) |
| `services/record_service.py` | Record mutations with automatic RecordFlow triggers |
| `services/record_type_service.py` | RecordType CRUD and record data validation against schema |
| `services/session_cleanup.py` | Background stale session cleanup service |

`clarinet/client.py` — `ClarinetClient`: HTTP client to own API (used by RecordFlow engine and pipeline tasks).

Key methods:

| Method | Description |
|---|---|
| `create_record(RecordCreate)` | Create a record |
| `get_record(record_id)` | Get record by ID |
| `find_records(**filters)` | Search records (query params: `record_type_name`, `study_uid`, `series_uid`, `record_status`, etc.) |
| `submit_record_data(record_id, data)` | Submit data + set finished |
| `update_record_data(record_id, data)` | Update data on finished record |
| `update_record_status(record_id, status)` | Change record status |
| `check_record_files(record_id)` | Compute checksums, auto-unblock if blocked |
| `get_study(study_uid)` | Get study |
| `anonymize_patient(patient_id)` | Trigger patient anonymization |

## Admin Management

- Auto-created on `clarinet db init` (idempotent)
- `create_admin_user()` from `clarinet.utils.bootstrap`
- Utilities in `clarinet.utils.admin`: `reset_admin_password`, `list_admin_users`, `ensure_admin_exists`
- CLI: `uv run clarinet admin create`, `uv run clarinet admin reset-password`
- See `clarinet/settings.py` for admin config (username, email, password, auto_create, strong_password)

## Session Management

- Session-based auth via fastapi-users; `AccessToken` model (UUID4)
- Cookies: httpOnly, secure (production), SameSite=lax; name: `clarinet_session`
- Auto cleanup service in `clarinet/services/session_cleanup.py`
- CLI: `uv run clarinet session stats`, `cleanup`, `revoke-user`, `list-user`
- See `clarinet/settings.py` for session config (expiry, sliding refresh, timeouts, cleanup)

## Alembic Migrations

- `uv run clarinet init-migrations` to set up Alembic
- `uv run alembic revision --autogenerate -m "Description"` to create
- `uv run alembic upgrade head` / `downgrade -1` to apply/rollback
- Or use: `make db-upgrade`, `make db-downgrade`, `make db-migration`
