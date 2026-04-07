# Backend Development Guide

## Async Programming

- Use `AsyncSession` for DB operations: `get_async_session` from `clarinet.utils.database`

### `asyncio.gather` + AsyncSession

`AsyncSession` is **not concurrency-safe**. All repositories in a single request share one session (and one DB connection) via FastAPI DI.

**Do NOT use `asyncio.gather()` with multiple queries on a shared session.** Even read-only queries deadlock on PostgreSQL: `asyncpg` connections handle one query at a time, so concurrent coroutines block each other. This appears to work on SQLite only because `aiosqlite` serializes operations through a dedicated thread.

Use sequential `await` for all queries on a shared session. If true parallelism is needed, each coroutine must create its own session (via session factory) to get a separate connection from the pool.

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

Log file format and jq recipes: `.claude/rules/test-debugging.md` (auto-loaded for tests/).

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

Each service has its own CLAUDE.md — see `services/*/CLAUDE.md` for details.

- `services/recordflow/` — Record workflow DSL engine
- `services/pipeline/` — Distributed task queue (TaskIQ + RabbitMQ)
- `services/dicomweb/` — DICOMweb proxy for OHIF
- `services/dicom/` — DICOM client (pynetdicom)
- `services/slicer/` — 3D Slicer integration
- `services/image/` — Image processing (numpy)
- `services/record_service.py` — Record mutations with RecordFlow triggers + `check_files()` (auto-unblock, checksum comparison, file-change notifications)
- `services/study_service.py` — Study management with entity-creation triggers

`clarinet/client.py` — `ClarinetClient`: HTTP client to own API (used by RecordFlow and pipeline tasks). See file for full method list.

## Admin Management

CLI: `uv run clarinet admin create`, `uv run clarinet admin reset-password`. Auto-created on `clarinet db init`.

## Session Management

Session-based auth (fastapi-users, `AccessToken`). Subcommands of `uv run clarinet session`:
`stats`, `cleanup [--days N]`, `cleanup-once`, `list-user UID`, `revoke-user UID`,
`cleanup-all` (asks for confirmation). Helpers live in `clarinet/utils/session.py`;
the long-running cleanup loop is `SessionCleanupService` in `clarinet/services/session_cleanup.py`.

## Alembic Migrations

`make db-upgrade`, `make db-downgrade`, `make db-migration`. Or: `uv run alembic revision --autogenerate -m "msg"`.
