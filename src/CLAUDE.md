# Backend Development Guide

## Async Programming

- Use `AsyncSession` for DB operations: `get_async_session` from `src.utils.database`

## Lifespan Shutdown Pattern

Resources initialized in `lifespan()` and shut down in its `finally` block must be **re-creatable** for test compatibility. Multiple tests may invoke `lifespan()` sequentially in the same process. If `shutdown()` destroys a module-level singleton without recreating it, subsequent lifespans fail.

Pattern: shutdown the old resource, then immediately replace it with a fresh instance:
```python
# src/utils/fs.py — reference implementation
def shutdown_fs_executor() -> None:
    global _fs_executor
    _fs_executor.shutdown(wait=False)
    _fs_executor = _make_executor()  # re-create for next lifespan
```

## Type Annotations & Imports

- Avoid circular imports; use relative imports within package
- Common type aliases in `src/types.py` — read file directly for full list

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
Legacy `TypeVar` in `src/repositories/base.py` and `src/utils/common.py` is pre-existing and exempt until refactored.

## Error Handling

- Try blocks: max two function calls; no large try-except blocks
- Always log errors before handling

```python
from src.exceptions.http import NOT_FOUND, CONFLICT
from src.utils.logger import logger

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
from src.utils.logger import logger

logger.info(f"User {user_id} created new record")
logger.error(f"Failed to connect to database: {error}")
```

## Configuration

- Env vars: `CLARINET_` prefix; TOML files: `settings.toml`, `settings.custom.toml`
- See `src/settings.py` for all available settings (admin, session, recordflow, etc.)

### RecordType Config System

Config modes (TOML / Python) — see `src/config/CLAUDE.md` for details.

Config loader: `src/utils/config_loader.py` (TOML/JSON discovery).
- TOML takes precedence when both formats exist for the same stem
- Sidecar schema: `{name}.schema.json` loaded automatically when `data_schema` absent
- `file_registry.toml`/`.json` and `*.schema.json` excluded from config discovery

Bootstrap uses `reconcile_config()` from `src/utils/bootstrap.py` — dispatches by mode.
Old `create_record_types_from_config()` preserved as deprecated alias.

## Database & API

- SQLModel for models; AsyncSession for operations — see `models/CLAUDE.md`
- Repository pattern in `src/repositories/` — see `repositories/CLAUDE.md`
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
| `services/study_service.py` | Study management |
| `services/session_cleanup.py` | Background stale session cleanup service |

`src/client.py` — `ClarinetClient`: HTTP client to own API (used by RecordFlow engine).

## Admin Management

- Auto-created on `clarinet db init` (idempotent)
- `create_admin_user()` from `src.utils.bootstrap`
- Utilities in `src.utils.admin`: `reset_admin_password`, `list_admin_users`, `ensure_admin_exists`
- CLI: `uv run clarinet admin create`, `uv run clarinet admin reset-password`
- See `src/settings.py` for admin config (username, email, password, auto_create, strong_password)

## Session Management

- Session-based auth via fastapi-users; `AccessToken` model (UUID4)
- Cookies: httpOnly, secure (production), SameSite=lax; name: `clarinet_session`
- Auto cleanup service in `src/services/session_cleanup.py`
- CLI: `uv run clarinet session stats`, `cleanup`, `revoke-user`, `list-user`
- See `src/settings.py` for session config (expiry, sliding refresh, timeouts, cleanup)

## Alembic Migrations

- `uv run clarinet init-migrations` to set up Alembic
- `uv run alembic revision --autogenerate -m "Description"` to create
- `uv run alembic upgrade head` / `downgrade -1` to apply/rollback
- Or use: `make db-upgrade`, `make db-downgrade`, `make db-migration`
