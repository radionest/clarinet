# CLAUDE.md - Code Style Guide and Best Practices for Clarinet

## About the Project

Clarinet is a framework for conducting clinical-radiological studies, built on FastAPI, SQLModel, and asynchronous architecture.

## General Principles

### Architecture

- Follow KISS, SOLID, DRY, YAGNI principles
- Use modular architecture with clear separation of responsibilities
- Prefer composition over inheritance
- Each module should have a single purpose
- Use dependency injection for managing dependencies

### Asynchronous Programming

- Use `async/await` for all I/O operations
- Avoid blocking calls in asynchronous code
- Use `asyncio.gather()` for parallel execution of independent tasks
- Always handle exceptions in asynchronous functions
- Prefer AsyncSession for database operations in API endpoints
- Use `get_async_session` from `src.utils.database` to get a session

## Code Style

### Formatting and Linting

- Use **ruff** for both formatting and linting (configured in `.ruff.toml`)
  - Formatting: `ruff format src/ tests/`
  - Linting: `ruff check src/ tests/ --fix`
  - Line length: 100 characters
  - Includes isort functionality for import sorting
- Use **mypy** for type checking: `mypy src/`
- **Pre-commit hooks** configured in `.pre-commit-config.yaml`:
  - Automatically runs ruff and mypy before commits
  - Install with: `pre-commit install`

### Type Annotations

- Use type hints for all functions and methods
- mypy settings in pyproject.toml (strict mode enabled)
- Fix all mypy errors before committing
- Use TypeVar and Generic for generic types
- Use Optional[T] instead of Union[T, None]

### Imports

- Group imports: standard library, third-party packages, local modules
- Use relative imports within package for development
- Avoid circular imports
- Sort imports using ruff (includes isort functionality)

## Best Practices

### Error Handling

- Don't wrap large blocks of code in try-except
- Try block should contain no more than two function calls
- Use custom exceptions from `src.exceptions` (CONFLICT, NOT_FOUND)
- Always log errors before handling them
- Avoid bare except - always specify the exception type

```python
from src.exceptions import NOT_FOUND
from src.utils.logger import logger

try:
    item = await session.get(Model, item_id)
    await session.commit()
except IntegrityError as e:
    await session.rollback()
    logger.error(f"Database integrity error: {e}")
    raise CONFLICT.with_context("Item already exists")
```

### Logging

- Import logger from `src.utils.logger`: `from src.utils.logger import logger`
- DO NOT import loguru directly - use the configured logger
- Log at appropriate levels:
  - `DEBUG`: detailed information for debugging
  - `INFO`: important events in normal flow
  - `WARNING`: unexpected events that don't interrupt operation
  - `ERROR`: errors requiring attention
  - `CRITICAL`: critical errors threatening system operation

```python
from src.utils.logger import logger

logger.debug(f"Processing message with length {len(message)}")
logger.info(f"User {user_id} created new task")
logger.warning(f"API rate limit approaching: {remaining} requests")
logger.error(f"Failed to connect to database: {error}")
```

### Configuration

- Main configuration file: `src/settings.py`
- Use `Settings` class based on `pydantic_settings.BaseSettings`
- Import settings: `from src.settings import settings`
- Environment variables with `CLARINET_` prefix
- Supports TOML files: `settings.toml` and `settings.custom.toml`
- Validate configuration on startup

```python
from src.settings import settings

# Using settings
database_url = settings.database_url
jwt_secret = settings.jwt_secret_key
storage_path = settings.storage_path

# Admin settings
admin_username = settings.admin_username
admin_email = settings.admin_email
admin_auto_create = settings.admin_auto_create
```

### Admin User Management

The framework includes built-in admin user management with automatic creation and CLI tools.

#### Configuration Settings

| Setting | Type | Default | Environment Variable | Description |
|---------|------|---------|---------------------|-------------|
| `admin_username` | str | "admin" | `CLARINET_ADMIN_USERNAME` | Default admin username |
| `admin_email` | str | "admin@clarinet.local" | `CLARINET_ADMIN_EMAIL` | Default admin email |
| `admin_password` | str | None | `CLARINET_ADMIN_PASSWORD` | Admin password (required in production) |
| `admin_auto_create` | bool | True | `CLARINET_ADMIN_AUTO_CREATE` | Auto-create admin on initialization |
| `admin_require_strong_password` | bool | False | `CLARINET_ADMIN_REQUIRE_STRONG_PASSWORD` | Enforce password strength policy |

#### Admin Creation Function

Use the `create_admin_user` function from `src.utils.bootstrap`:

```python
from src.utils.bootstrap import create_admin_user

# Create admin user programmatically
admin = await create_admin_user(
    username="superadmin",
    email="admin@hospital.org",
    password="SecurePassword123!"
)
```

#### Admin Utility Functions

Utility functions available in `src.utils.admin`:

```python
from src.utils.admin import (
    reset_admin_password,
    list_admin_users,
    ensure_admin_exists
)

# Reset admin password
success = await reset_admin_password("admin", "NewPassword123!")

# List all admin users
async with get_async_session() as session:
    admins = await list_admin_users(session)

# Ensure at least one admin exists
await ensure_admin_exists()
```

#### Security Best Practices for Admin Users

1. **Production Configuration**:
   - Always set `CLARINET_ADMIN_PASSWORD` explicitly
   - Enable `CLARINET_ADMIN_REQUIRE_STRONG_PASSWORD=true`
   - Use minimum 12 characters with mixed case, numbers, and symbols

2. **Password Management**:
   - Passwords are hashed using bcrypt
   - Never log or display passwords
   - Use secure password input (getpass) in CLI

3. **Access Control**:
   - Admin users have `is_superuser=True` flag
   - Full system privileges - protect credentials carefully
   - Consider implementing admin action audit logging

4. **Initialization**:
   - Admin created automatically on `clarinet db init`
   - Idempotent creation - safe to run multiple times
   - System validates admin existence on API startup

### Type Definitions

- Common type aliases in `src/types.py`
- Use type aliases for better code readability and consistency
- Import types: `from src.types import JSONDict, TaskResult`

```python
from src.types import JSONDict, TaskResult, SlicerArgs

# Using type aliases
async def process_task(args: SlicerArgs) -> TaskResult:
    result: JSONDict = {"status": "success", "data": {}}
    return result
```

Available type aliases:
- `JSONDict`: JSON-compatible dictionary
- `TaskResult`, `SlicerArgs`, `SlicerResult`: Task-related types
- `AuthResponse`, `TokenResponse`: Authentication types
- `FormData`, `ValidationSchema`: Form and validation types

### Database Operations

- Use SQLModel for models
- Asynchronous operations through AsyncSession
- Async CRUD operations in `src/utils/async_crud.py`
- Database manager in `src/utils/db_manager.py`

```python
from sqlalchemy.ext.asyncio import AsyncSession
from src.utils.database import get_async_session
from src.utils.async_crud import add_item_async, get_item_async

async def create_user(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_async_session)
):
    user = User(**user_data.model_dump())
    return await add_item_async(user, session)
```

### Project Structure (Current)

```tree
src/
├── __init__.py
├── __main__.py          # CLI entry point
├── settings.py          # Application configuration
├── exceptions.py        # Custom exceptions
├── types.py             # Common type definitions
├── api/                 # FastAPI application
│   ├── __init__.py
│   ├── app.py          # Main application file
│   ├── dependencies.py  # Dependencies (auth, etc)
│   ├── security.py     # JWT and security
│   └── routers/        # API endpoints
│       ├── auth.py
│       ├── study.py
│       ├── task.py
│       ├── user.py
│       └── slicer.py
├── cli/                 # CLI interface
│   ├── __init__.py
│   └── main.py
├── models/              # SQLModel models
│   ├── __init__.py
│   ├── base.py         # Base models
│   ├── user.py
│   ├── study.py
│   ├── task.py
│   └── patient.py
├── services/            # Business logic
│   ├── dicom/          # DICOM processing
│   ├── image/          # Image processing
│   └── slicer/         # Slicer integration
└── utils/               # Helper utilities
    ├── __init__.py
    ├── logger.py       # Loguru setup
    ├── database.py     # Database connection
    ├── db_manager.py   # Database management
    ├── async_crud.py   # Async CRUD operations
    ├── admin.py        # Admin user management utilities
    ├── bootstrap.py    # Data initialization and admin creation
    ├── common.py       # Common utility functions
    ├── migrations.py   # Migration helper functions
    ├── slicer.py       # Slicer utilities
    ├── study.py        # Study-related utilities
    └── validation.py   # Validation utilities
```

### API Routers

- All routers use async/await
- Use `Depends` for dependency injection
- Handle errors with HTTPException or custom exceptions

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from src.utils.database import get_async_session
from src.api.dependencies import get_current_user

router = APIRouter(prefix="/items", tags=["Items"])

@router.get("/{item_id}")
async def get_item(
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user)
):
    # Implementation
    pass
```

### Testing

- Tests in `tests/` directory
- Use pytest and pytest-asyncio for async tests
- Configuration in `tests/conftest.py`
- Test structure:
  - `tests/integration/` - integration tests
  - `tests/utils/` - utility tests
- Mock external dependencies
- Use fixtures for code reuse

```python
import pytest
from httpx import AsyncClient
from src.api.app import app

@pytest.mark.asyncio
async def test_create_user(async_client: AsyncClient):
    response = await async_client.post(
        "/users/",
        json={"username": "test", "email": "test@test.com"}
    )
    assert response.status_code == 201

@pytest.mark.asyncio
async def test_admin_creation():
    """Test admin user creation."""
    from src.utils.bootstrap import create_admin_user

    admin = await create_admin_user(
        username="testadmin",
        email="test@example.com",
        password="TestPassword123!"
    )
    assert admin.is_superuser
    assert admin.is_active
```

### Development Commands

```bash
# Run application
uvicorn src.api.app:app --reload

# Format code
ruff format src/ tests/

# Check code
ruff check src/ tests/ --fix
mypy src/

# Pre-commit hooks
pre-commit install  # Install hooks (run once)
pre-commit run --all-files  # Run all hooks manually

# Run tests
pytest
pytest --cov=src tests/

# Database management
clarinet db init  # Initialize database with admin user
clarinet db upgrade  # Apply migrations
clarinet db downgrade  # Rollback migrations

# Admin management
clarinet admin create  # Create new admin user (interactive)
clarinet admin create --username superadmin --email admin@example.com
clarinet admin reset-password  # Reset admin password
clarinet admin reset-password --username admin

# Database migrations (Alembic)
alembic upgrade head  # Apply all migrations
alembic revision --autogenerate -m "Description"  # Create migration from model changes
alembic downgrade -1  # Rollback one migration
alembic current  # Show current migration
alembic history  # Show migration history
```

### Performance

- Use caching for frequent operations (@lru_cache)
- Apply connection pooling for DB (configured in SQLAlchemy)
- Avoid N+1 queries - use selectinload/joinedload
- Profile critical code sections

### Security

- Never log sensitive data (passwords, tokens)
- Validate all input data with Pydantic
- Use parameterized queries (SQLModel does this automatically)
- JWT tokens for authentication (src/api/security.py)
- Password hashing with bcrypt
- Rate limiting for API endpoints
- CORS settings in src/api/app.py
- Admin user management with secure defaults
- Strong password validation for production environments

### Git Workflow

- Make atomic commits with clear messages
- Use conventional commits (feat:, fix:, docs:, refactor:, test:, chore:)
- Don't commit:
  - Generated files (**pycache**, *.pyc)
  - Secrets and configuration files with passwords
  - .env files (use .env.example)
  - Database (*.db)
- Write meaningful PR descriptions

### Documentation

- Use docstrings for all public functions and classes
- Follow Google Style for docstrings
- Document complex logic with inline comments
- Update README.md when functionality changes
- Document API endpoints with FastAPI auto-documentation

```python
async def process_task(
    task_id: int,
    user_id: int,
    session: AsyncSession
) -> Optional[TaskResult]:
    """Process a task and return the result.
    
    Args:
        task_id: The ID of the task to process
        user_id: The ID of the user requesting processing
        session: Database session
        
    Returns:
        TaskResult object if processing succeeded, None otherwise
        
    Raises:
        NOT_FOUND: If task doesn't exist
        CONFLICT: If task is already being processed
    """
    # Implementation
    pass
```

## Anti-patterns to Avoid

- **God objects** - classes with too much responsibility
- **Callback hell** - use async/await instead of callbacks
- **Global variables** - use dependency injection through FastAPI
- **Magic numbers** - extract to constants or settings
- **Code duplication** - extract to functions/classes/utilities
- **Ignoring errors** - always handle exceptions explicitly
- **Hardcoded configuration** - use settings.py and environment variables
- **Direct loguru import** - use configured logger from src.utils.logger
- **Synchronous operations in async functions** - use async equivalents
- **Bare except** - always specify exception type

## Database Migrations (Alembic)

### Overview

The project uses Alembic for database schema migrations with full async support for SQLModel.

### Migration Workflow

1. **Initial Setup** (Already configured):
   - Alembic configuration in `alembic.ini`
   - Migration environment in `alembic/env.py`
   - Migrations directory in `alembic/versions/`

2. **Creating Migrations**:
   ```bash
   # Auto-generate migration from model changes
   alembic revision --autogenerate -m "Add new field to User model"
   
   # Create empty migration for custom SQL
   alembic revision -m "Custom migration description"
   ```

3. **Applying Migrations**:
   ```bash
   # Apply all pending migrations
   alembic upgrade head
   
   # Apply specific migration
   alembic upgrade +1  # Next migration
   alembic upgrade <revision_id>
   
   # Apply migrations up to specific revision
   alembic upgrade <revision_id>
   ```

4. **Rolling Back**:
   ```bash
   # Rollback one migration
   alembic downgrade -1
   
   # Rollback to specific revision
   alembic downgrade <revision_id>
   
   # Rollback all migrations
   alembic downgrade base
   ```

5. **Checking Status**:
   ```bash
   # Show current migration
   alembic current
   
   # Show migration history
   alembic history
   
   # Show pending migrations
   alembic history --indicate-current
   ```

### Migration Utilities

Use the helper functions from `src.utils.migrations`:

```python
from src.utils.migrations import (
    run_migrations,
    create_migration,
    get_current_revision,
    get_pending_migrations,
    initialize_database
)

# Apply migrations programmatically
await initialize_database()  # Check and apply pending migrations

# Create new migration
create_migration("Add user preferences table", autogenerate=True)

# Check migration status
current = get_current_revision()
pending = get_pending_migrations()
```

### Best Practices

1. **Always review auto-generated migrations** before applying them
2. **Test migrations** in development before production
3. **Back up database** before applying migrations in production
4. **Use descriptive messages** for migrations
5. **Don't edit applied migrations** - create new ones instead
6. **Keep migrations small and focused** on single changes
7. **Use transactions** where supported (PostgreSQL)

### Troubleshooting

- **Import errors**: Ensure all models are imported in `alembic/env.py`
- **Missing tables**: Run `alembic upgrade head` to apply migrations
- **Duplicate migrations**: Check `alembic_version` table and history
- **Failed migration**: Use `alembic downgrade -1` to rollback

## Pre-commit Checklist

- [ ] Code formatted with ruff (`ruff format src/ tests/`)
- [ ] No ruff errors (run `ruff check src/ tests/`)
- [ ] Pre-commit hooks pass (`pre-commit run --all-files`)
- [ ] No mypy errors (run `mypy src/`)
- [ ] Tests written for new functionality
- [ ] Tests pass successfully
- [ ] Docstrings added for public functions
- [ ] Documentation updated if necessary
- [ ] No secrets in code
- [ ] Commit follows conventional commits
- [ ] Database migrations created for schema changes
