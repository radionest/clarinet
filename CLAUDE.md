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

- All Python tools run through **uv**: `uv run <command>`
- Use **ruff** for both formatting and linting (configured in `.ruff.toml`)
  - Formatting: `uv run ruff format src/ tests/`
  - Linting: `uv run ruff check src/ tests/ --fix`
  - Line length: 100 characters
  - Target: Python 3.14
  - Includes isort functionality for import sorting
- Use **mypy** for type checking: `uv run mypy src/`
- **Pre-commit hooks** configured in `.pre-commit-config.yaml`:
  - Automatically runs ruff and mypy before commits
  - Install with: `uv run pre-commit install`

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

## Frontend Development

### Technology Stack

- **Gleam**: Functional programming language with excellent type safety
- **Lustre**: Elm-inspired web framework for Gleam (~> 5.4)
- **Modem**: Client-side routing (~> 2.1)
- **Formosh**: Form handling (private: `git@github.com:radionest/gleam_formosh.git`)
- **Plinth**: DOM manipulation (~> 0.7.2)
- **gleam_fetch**: HTTP requests with automatic cookie handling (~> 1.3)
- **MVU Architecture**: Model-View-Update pattern for predictable state management

### Frontend Structure

The frontend is embedded within the package at `src/frontend/`. The entry point is `clarinet.gleam`.

```
src/frontend/src/
├── clarinet.gleam       # Entry point (configured in gleam.toml)
├── main.gleam           # Application initialization
├── router.gleam         # Client-side routing
├── store.gleam          # Global state management
├── api/                 # API client and HTTP
│   ├── auth.gleam       # Authentication API
│   ├── http_client.gleam # HTTP client wrapper
│   ├── models.gleam     # Data models
│   └── types.gleam      # Type definitions
├── components/          # Reusable UI components
│   ├── layout.gleam     # Layout component
│   └── forms/           # Form components
│       ├── base.gleam
│       ├── study_form.gleam
│       ├── user_form.gleam
│       └── patient_form.gleam
├── pages/               # Application pages
│   ├── home.gleam
│   ├── login.gleam
│   ├── register.gleam
│   ├── records/         # Record management pages
│   │   ├── list.gleam
│   │   ├── detail.gleam
│   │   ├── new.gleam
│   │   ├── design.gleam
│   │   └── execute.gleam
│   ├── studies/
│   │   ├── list.gleam
│   │   └── detail.gleam
│   └── users/
│       ├── list.gleam
│       └── profile.gleam
└── utils/
    └── dom.gleam        # DOM utilities
```

### Building Frontend

**Note about dependencies**: The `formosh` library currently references a private Git repository (`git@github.com:radionest/gleam_formosh.git`). You may need to either:
- Have access to the private repository
- Replace it with a public alternative
- Build your own form handling solution

1. **Development Build**:
   ```bash
   cd src/frontend
   gleam deps download  # Install dependencies
   gleam build --target javascript
   ```

2. **Production Build**:
   ```bash
   make frontend-build
   # Or directly:
   ./scripts/build_frontend.sh
   ```

3. **Output Structure**:
   - Built files are placed in `dist/` directory
   - FastAPI automatically serves these files when `frontend_enabled=True`
   - Files are served from `/` with SPA routing support

### Frontend Configuration

- Frontend is enabled by default in `settings.py`
- Set `frontend_enabled=False` to run API-only mode
- Static files are served from `dist/` when available
- Custom static files can be placed in `clarinet_custom/` directory

### Authentication Architecture

**Backend (FastAPI-users with database sessions):**
- Session-based authentication using fastapi-users library
- Sessions stored in database via `AccessToken` model (UUID4 tokens)
- Cookies configured as httpOnly, secure (in production), and SameSite=lax
- Cookie name: `clarinet_session` (configurable)
- Session expiry: 24 hours by default (`session_expire_hours` setting)
- Sliding session refresh, absolute timeout, and idle timeout supported
- Password hashing via bcrypt

**Frontend (Cookie-based auth):**
- Authentication state tracked by user presence in store
- HTTP requests automatically include session cookies via gleam_fetch
- No manual token/session management in frontend code
- Login endpoint accepts multipart/form-data with username/password
- Logout clears session from database and cookie

## Best Practices

### Error Handling

- Don't wrap large blocks of code in try-except
- Try block should contain no more than two function calls
- Use custom exceptions from `src.exceptions.http` (CONFLICT, NOT_FOUND, etc.)
- Always log errors before handling them
- Avoid bare except - always specify the exception type

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
logger.info(f"User {user_id} created new record")
logger.warning(f"API rate limit approaching: {remaining} requests")
logger.error(f"Failed to connect to database: {error}")
```

### Configuration

- Main configuration file: `src/settings.py`
- Use `Settings` class based on `pydantic_settings.BaseSettings`
- Import settings: `from src.settings import settings`
- Environment variables with `CLARINET_` prefix
- Supports TOML files: `settings.toml` and `settings.custom.toml`
- Copy `settings.toml.example` to create your configuration
- Validate configuration on startup

```python
from src.settings import settings

# Using settings
database_url = settings.database_url
secret_key = settings.secret_key
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
| `admin_email` | str | "admin@clarinet.ru" | `CLARINET_ADMIN_EMAIL` | Default admin email |
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

4. **Initialization**:
   - Admin created automatically on `clarinet db init`
   - Idempotent creation - safe to run multiple times
   - System validates admin existence on API startup

### Type Definitions

- Common type aliases in `src/types.py`
- Use type aliases for better code readability and consistency
- Import types: `from src.types import JSONDict, RecordData`

```python
from src.types import JSONDict, RecordData, SlicerArgs

# Using type aliases
async def process_record(args: SlicerArgs) -> RecordData:
    result: JSONDict = {"status": "success", "data": {}}
    return result
```

Available type aliases:
- `JSONDict`: JSON-compatible dictionary
- `RecordData`, `SlicerArgs`, `SlicerResult`: Record-related types
- `RecordSchema`, `RecordContextInfo`: Record schema types
- `AuthResponse`, `TokenResponse`: Authentication types
- `PaginationParams`, `MessageResponse`: API response types
- `FormData`, `ValidationSchema`: Form and validation types

### Database Operations

- Use SQLModel for models
- Asynchronous operations through AsyncSession
- Repository pattern in `src/repositories/` for data access
- Database manager in `src/utils/db_manager.py`

```python
from sqlalchemy.ext.asyncio import AsyncSession
from src.utils.database import get_async_session
from src.repositories.user_repository import UserRepository

async def create_user(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_async_session)
):
    user_repo = UserRepository(session)
    user = User(**user_data.model_dump())
    return await user_repo.add(user)
```

### RecordFlow - Workflow Automation

RecordFlow is an event-driven workflow orchestration system that automatically creates or updates records based on status changes and conditional logic. Disabled by default (`recordflow_enabled = False`).

#### Core Concepts

- **FlowRecord**: Defines a trigger-activated workflow
- **FlowCondition**: Conditional blocks with associated actions
- **RecordFlowEngine**: Runtime execution engine
- **FlowResult**: Lazy evaluation of data field comparisons

#### Defining Workflows

Workflows are defined in Python files (naming convention: `*_flow.py`):

```python
from src.services.recordflow import record

# Create follow-up when doctor and AI disagree
record('doctor_report')
    .on_status('finished')
    .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
    .add_record('confirm_birads', info='BIRADS disagreement')
```

#### Key Methods

- `record('type_name')` - Create a flow for a record type
- `.on_status('status')` - Set trigger status
- `.if_(condition)` - Start conditional block
- `.or_(condition)` / `.and_(condition)` - Combine conditions
- `.add_record('type', **kwargs)` - Create new record
- `.update_record('name', status='new_status')` - Update record
- `.call(func)` - Execute custom function
- `.else_()` - Add else branch

#### Data Access

Access record data fields using dot notation:

```python
record('report').data.findings.tumor_size  # Nested field access
record('report').d.field_name              # Shorthand
```

#### Comparison Operators

Supports: `==`, `!=`, `<`, `<=`, `>`, `>=`

#### Engine Setup

```python
from src.services.recordflow import RecordFlowEngine, discover_and_load_flows
from pathlib import Path

engine = RecordFlowEngine(client)
discover_and_load_flows(engine, [Path('flows/')])

# Trigger on status change
await engine.handle_record_status_change(record, old_status)
```

#### RecordFlow Configuration

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `recordflow_enabled` | bool | False | Enable RecordFlow workflow engine |
| `recordflow_paths` | list[str] | [] | Directories containing `*_flow.py` files |

#### File Structure

```
src/services/recordflow/
├── __init__.py        # Package exports
├── engine.py          # Runtime execution
├── flow_builder.py    # Builder exports
├── flow_condition.py  # Conditional logic
├── flow_loader.py     # Dynamic file loading
├── flow_record.py     # DSL builder
└── flow_result.py     # Comparison classes
```

### Session Management

Clarinet includes comprehensive session management with automatic cleanup.

#### Session Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `cookie_name` | str | "clarinet_session" | Session cookie name |
| `session_expire_hours` | int | 24 | Session expiration time |
| `session_sliding_refresh` | bool | True | Auto-extend on activity |
| `session_absolute_timeout_days` | int | 30 | Maximum session age |
| `session_idle_timeout_minutes` | int | 60 | Inactivity timeout |
| `session_concurrent_limit` | int | 5 | Max sessions per user (0 = unlimited) |
| `session_ip_check` | bool | False | Validate IP consistency |
| `session_secure_cookie` | bool | True | HTTPS only in production |

#### Session Cleanup Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `session_cleanup_enabled` | bool | True | Enable automatic cleanup |
| `session_cleanup_interval` | int | 3600 | Cleanup interval in seconds |
| `session_cleanup_batch_size` | int | 1000 | Batch size for cleanup |
| `session_cleanup_retention_days` | int | 30 | Days to retain sessions |

### Project Structure (Current)

```tree
clarinet/
├── src/                 # Backend source code
│   ├── __init__.py
│   ├── __main__.py      # CLI entry point (python -m clarinet)
│   ├── settings.py      # Application configuration
│   ├── client.py        # API client library
│   ├── types.py         # Common type definitions
│   ├── exceptions/      # Custom exceptions module
│   │   ├── __init__.py
│   │   ├── domain.py   # Domain exceptions
│   │   └── http.py     # HTTP exceptions
│   ├── api/             # FastAPI application
│   │   ├── __init__.py
│   │   ├── app.py      # Main application file
│   │   ├── auth_config.py # Authentication configuration
│   │   ├── dependencies.py  # Dependencies (auth, etc)
│   │   ├── exception_handlers.py # Exception handlers
│   │   └── routers/    # API endpoints
│   │       ├── __init__.py
│   │       ├── auth.py
│   │       ├── study.py   # Study, patient, series endpoints
│   │       ├── record.py  # Record/workflow endpoints
│   │       ├── user.py
│   │       └── slicer.py
│   ├── cli/             # CLI interface
│   │   ├── __init__.py
│   │   └── main.py
│   ├── models/          # SQLModel models
│   │   ├── __init__.py
│   │   ├── auth.py      # Authentication models (AccessToken)
│   │   ├── base.py      # Base models, RecordStatus, DicomQueryLevel enums
│   │   ├── user.py      # User, UserRole, UserRolesLink
│   │   ├── study.py     # Study, Series and related schemas
│   │   ├── record.py    # Record, RecordType and related schemas
│   │   ├── file_schema.py # FileDefinition model
│   │   └── patient.py   # Patient and related schemas
│   ├── repositories/    # Repository pattern
│   │   ├── __init__.py
│   │   ├── base.py      # Base repository class
│   │   ├── patient_repository.py
│   │   ├── series_repository.py
│   │   ├── study_repository.py
│   │   └── user_repository.py
│   ├── services/        # Business logic
│   │   ├── __init__.py
│   │   ├── user_service.py       # User business logic
│   │   ├── study_service.py      # Study business logic
│   │   ├── session_cleanup.py    # Session cleanup service
│   │   ├── file_validation.py    # File validation service
│   │   ├── recordflow/           # Record workflow engine
│   │   │   ├── __init__.py
│   │   │   ├── engine.py         # Runtime execution
│   │   │   ├── flow_builder.py
│   │   │   ├── flow_condition.py
│   │   │   ├── flow_loader.py
│   │   │   ├── flow_record.py    # DSL builder
│   │   │   └── flow_result.py    # Comparison classes
│   │   ├── providers/            # Service providers
│   │   │   ├── __init__.py
│   │   │   └── anonymous_name_provider.py
│   │   ├── dicom/                # DICOM processing
│   │   │   ├── dicom.py
│   │   │   └── anonimizer.py
│   │   ├── image/                # Image processing
│   │   │   ├── image.py
│   │   │   └── coco2nii.py
│   │   └── slicer/               # Slicer integration
│   │       ├── __init__.py
│   │       └── slicer.py
│   ├── utils/           # Helper utilities
│   │   ├── __init__.py
│   │   ├── logger.py        # Loguru setup
│   │   ├── database.py      # Database connection
│   │   ├── db_manager.py    # Database management
│   │   ├── admin.py         # Admin user management utilities
│   │   ├── auth.py          # Authentication utilities
│   │   ├── bootstrap.py     # Data initialization and admin creation
│   │   ├── common.py        # Common utility functions
│   │   ├── file_patterns.py # File pattern matching utilities
│   │   ├── migrations.py    # Migration helper functions
│   │   ├── session.py       # Session management utilities
│   │   ├── slicer.py        # Slicer utilities
│   │   ├── study.py         # Study-related utilities
│   │   └── validation.py    # Validation utilities
│   └── frontend/        # Embedded Gleam/Lustre frontend
│       ├── gleam.toml   # Gleam configuration
│       ├── src/         # Gleam source code
│       ├── public/      # Public assets
│       ├── static/      # Static HTML/CSS
│       └── build/       # Build artifacts (generated)
├── dist/                # Built frontend (generated)
├── scripts/             # Build scripts
│   └── build_frontend.sh
├── tests/               # Test suite
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_client.py          # Client library tests
│   ├── test_file_patterns.py   # File pattern tests
│   ├── test_file_validation.py # File validation tests
│   ├── integration/            # Integration tests
│   │   ├── __init__.py
│   │   ├── test_app.py
│   │   ├── test_api_endpoints.py
│   │   ├── test_record_crud.py
│   │   ├── test_study_crud.py
│   │   └── test_user_crud.py
│   ├── e2e/                    # End-to-end tests
│   │   └── test_auth_workflows.py
│   └── utils/
│       ├── __init__.py
│       └── test_helpers.py
├── examples/            # Examples and templates
│   ├── test/
│   └── test_front/
├── data/                # Data storage (gitignored)
├── .github/             # CI/CD workflows
├── Makefile            # Build automation
├── pyproject.toml      # Package configuration
├── .ruff.toml          # Ruff configuration
├── settings.toml.example # Configuration template
└── .pre-commit-config.yaml # Pre-commit hooks
```

### API Routers

- All routers use async/await
- Use `Depends` for dependency injection
- Handle errors with HTTPException or custom exceptions

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from src.utils.database import get_async_session
from src.api.dependencies import get_current_active_user

router = APIRouter(prefix="/items", tags=["Items"])

@router.get("/{item_id}")
async def get_item(
    item_id: int,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user)
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
  - `tests/e2e/` - end-to-end tests
  - `tests/utils/` - test helpers
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

### Pure Gleam Frontend

The frontend is written entirely in pure Gleam without JavaScript FFI:
- All functionality uses native Gleam libraries
- HTTP requests via gleam_fetch (automatic cookie handling)
- DOM manipulation via plinth
- Client-side routing via modem
- Form handling via formosh
- No custom JavaScript required

### Development Commands

All Python commands run through **uv** to ensure correct virtual environment and dependencies.

```bash
# Backend Development
uv run uvicorn src.api.app:app --reload  # Run API server
uv run clarinet run --with-frontend      # Run with frontend

# Frontend Development
cd src/frontend  # Frontend is embedded in src/
gleam deps download               # Install dependencies
gleam build --target javascript   # Build for browser
cd ../..

# Build Commands (Makefile)
make frontend-build               # Build production frontend
make frontend-deps                # Install frontend dependencies
make frontend-clean               # Clean frontend artifacts
make frontend-test                # Run frontend tests
make run-dev                      # Run full stack development
make run-api                      # Run API server only
make build                        # Build entire package
make dev-setup                    # Set up development environment

# Code Quality (direct)
uv run ruff format src/ tests/             # Format code
uv run ruff check src/ tests/ --fix        # Lint code (with fixes)
uv run mypy src/                           # Type check

# Pre-commit hooks
uv run pre-commit install                  # Install hooks (run once)
uv run pre-commit run --all-files          # Run all hooks manually

# Run tests
uv run pytest                              # Run all tests
uv run pytest --cov=src tests/             # Run with coverage
uv run pytest tests/integration/           # Run integration tests only

# Database management
uv run clarinet db init                    # Initialize database with admin user

# Database migrations (Alembic)
uv run clarinet init-migrations            # Initialize Alembic in project
uv run alembic upgrade head                # Apply all migrations
uv run alembic revision --autogenerate -m "Description"  # Create migration
uv run alembic downgrade -1                # Rollback one migration
uv run alembic current                     # Show current migration
uv run alembic history                     # Show migration history

# Admin management
uv run clarinet admin create               # Create new admin user (interactive)
uv run clarinet admin create --username superadmin --email admin@example.com
uv run clarinet admin reset-password       # Reset admin password
uv run clarinet admin reset-password --username admin

# Frontend management (CLI)
uv run clarinet frontend install           # Install Gleam and dependencies
uv run clarinet frontend build             # Build frontend
uv run clarinet frontend build --watch     # Build with watch mode
uv run clarinet frontend clean             # Clean build artifacts

# Project initialization
uv run clarinet init [path]                # Initialize new Clarinet project
```

### Documentation

- Use docstrings for all public functions and classes
- Follow Google Style for docstrings
- Document complex logic with inline comments
- Update README.md when functionality changes
- Document API endpoints with FastAPI auto-documentation

```python
async def process_record(
    record_id: int,
    user_id: int,
    session: AsyncSession
) -> Optional[RecordData]:
    """Process a record and return the result.

    Args:
        record_id: The ID of the record to process
        user_id: The ID of the user requesting processing
        session: Database session

    Returns:
        RecordData dict if processing succeeded, None otherwise

    Raises:
        NOT_FOUND: If record doesn't exist (from src.exceptions.http)
        CONFLICT: If record is already being processed (from src.exceptions.http)
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

## Database Migrations (Alembic Integration)

### Overview

Clarinet provides utilities to simplify Alembic integration in your projects. As a framework, Clarinet doesn't include Alembic configuration directly, but offers helper functions to manage migrations in user projects. Use `clarinet init-migrations` to initialize Alembic, then use `alembic` commands or Makefile targets directly.

## Pre-commit Checklist

- [ ] Code formatted with ruff (`uv run ruff format src/ tests/`)
- [ ] No ruff errors (run `uv run ruff check src/ tests/`)
- [ ] Pre-commit hooks pass (`uv run pre-commit run --all-files`)
- [ ] No mypy errors (run `uv run mypy src/`)
- [ ] Tests written for new functionality
- [ ] Tests pass successfully
- [ ] Docstrings added for public functions
- [ ] Documentation updated if necessary
- [ ] No secrets in code
- [ ] Commit follows conventional commits
- [ ] Database migrations created for schema changes
