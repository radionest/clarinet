# Backend Development Guide

## Async Programming

- `async/await` for all I/O operations; avoid blocking calls
- `asyncio.gather()` for parallel independent tasks
- Always handle exceptions in async functions
- Use `AsyncSession` for DB operations: `get_async_session` from `src.utils.database`

## Type Annotations & Imports

- Type hints on all functions; mypy strict mode enabled (`pyproject.toml`)
- `Optional[T]` instead of `Union[T, None]`; use `TypeVar`/`Generic` for generics
- Group imports: stdlib → third-party → local; relative imports within package
- Sort with ruff (includes isort). Avoid circular imports.
- Common type aliases in `src/types.py` — read file directly for full list

## Error Handling

- Try blocks: max two function calls; no large try-except blocks
- Custom exceptions from `src.exceptions.http`: NOT_FOUND, CONFLICT, etc.
- Always log errors before handling; never bare except

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

- Always: `from src.utils.logger import logger` — never import loguru directly
- Levels: DEBUG (detail), INFO (events), WARNING (unexpected), ERROR (failures), CRITICAL (system)

```python
from src.utils.logger import logger

logger.info(f"User {user_id} created new record")
logger.error(f"Failed to connect to database: {error}")
```

## Configuration

- `from src.settings import settings` — `pydantic_settings.BaseSettings`
- Env vars: `CLARINET_` prefix; TOML files: `settings.toml`, `settings.custom.toml`
- See `src/settings.py` for all available settings (admin, session, recordflow, etc.)

## Database & API

- SQLModel for models; AsyncSession for operations — see `models/CLAUDE.md`
- Repository pattern in `src/repositories/` — see `repositories/CLAUDE.md`
- API DI aliases and router patterns — see `api/CLAUDE.md`
- Never query DB directly in routers; use repository/service layer

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

## Documentation

- Google-style docstrings for all public functions and classes
- Document API endpoints with FastAPI auto-documentation
- Inline comments only for complex logic

## Alembic Migrations

- `uv run clarinet init-migrations` to set up Alembic
- `uv run alembic revision --autogenerate -m "Description"` to create
- `uv run alembic upgrade head` / `downgrade -1` to apply/rollback
- Or use: `make db-upgrade`, `make db-downgrade`, `make db-migration`
