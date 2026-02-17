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

## Frontend Development

### Technology Stack

- **Gleam**: Functional programming language with excellent type safety
- **Lustre**: Elm-inspired web framework for Gleam
- **Modem**: Client-side routing
- **MVU Architecture**: Model-View-Update pattern for predictable state management

### Frontend Structure

The frontend can be located in two places:
- `src/frontend/` - Embedded within the package (current default)
- `frontend/` - Standalone at root level (for separation of concerns)

```
src/frontend/src/  OR  frontend/src/
├── api/              # API client and models
├── components/       # Reusable UI components
├── pages/           # Application pages
├── router.gleam     # Client-side routing
├── store.gleam      # Global state management
└── main.gleam       # Application entry
```

### Building Frontend

**Note about dependencies**: The `formosh` library currently references a private Git repository (`git@github.com:radionest/gleam_formosh.git`). You may need to either:
- Have access to the private repository
- Replace it with a public alternative
- Build your own form handling solution

1. **Development Build**:
   ```bash
   # For embedded frontend
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

   The build script automatically detects the frontend location.

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
- Supports TOML files: copy `settings.toml.example` to create your configuration
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

RecordFlow is an event-driven workflow orchestration system that automatically creates or updates records based on status changes and conditional logic.

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

#### File Structure

```
src/services/recordflow/
├── engine.py          # Runtime execution
├── flow_builder.py    # Builder exports
├── flow_condition.py  # Conditional logic
├── flow_loader.py     # Dynamic file loading
├── flow_record.py     # DSL builder
└── flow_result.py     # Comparison classes
```

### Project Structure (Current)

```tree
clarinet/
├── src/                 # Backend source code
│   ├── __init__.py
│   ├── __main__.py      # CLI entry point
│   ├── settings.py      # Application configuration
│   ├── client.py        # API client library
│   ├── exceptions/      # Custom exceptions module
│   │   ├── domain.py   # Domain exceptions
│   │   └── http.py     # HTTP exceptions
│   ├── types.py         # Common type definitions
│   ├── api/             # FastAPI application
│   │   ├── app.py      # Main application file
│   │   ├── auth_config.py # Authentication configuration
│   │   ├── dependencies.py  # Dependencies (auth, etc)
│   │   ├── exception_handlers.py # Exception handlers
│   │   └── routers/    # API endpoints
│   │       ├── auth.py
│   │       ├── study.py
│   │       ├── record.py  # Record/workflow endpoints
│   │       ├── user.py
│   │       └── slicer.py
│   ├── cli/             # CLI interface
│   │   ├── __init__.py
│   │   └── main.py
│   ├── models/          # SQLModel models
│   │   ├── __init__.py
│   │   ├── auth.py     # Authentication models (AccessToken)
│   │   ├── base.py     # Base models, RecordStatus enum
│   │   ├── user.py
│   │   ├── study.py
│   │   ├── record.py   # Record and RecordType models
│   │   ├── series.py
│   │   └── patient.py
│   ├── repositories/    # Repository pattern
│   │   ├── base.py     # Base repository class
│   │   ├── patient_repository.py
│   │   ├── series_repository.py
│   │   ├── study_repository.py
│   │   ├── user_repository.py
│   │   └── record.py   # Record repository
│   ├── services/        # Business logic
│   │   ├── user_service.py    # User business logic
│   │   ├── study_service.py   # Study business logic
│   │   ├── session_cleanup.py # Session management
│   │   ├── recordflow/        # Record workflow engine
│   │   │   ├── engine.py      # Runtime execution
│   │   │   ├── flow_builder.py
│   │   │   ├── flow_condition.py
│   │   │   ├── flow_loader.py
│   │   │   ├── flow_record.py # DSL builder
│   │   │   └── flow_result.py # Comparison classes
│   │   ├── providers/  # Service providers
│   │   ├── dicom/      # DICOM processing
│   │   ├── image/      # Image processing
│   │   └── slicer/     # Slicer integration
│   ├── utils/           # Helper utilities
│   │   ├── __init__.py
│   │   ├── logger.py   # Loguru setup
│   │   ├── database.py # Database connection
│   │   ├── db_manager.py # Database management
│   │   ├── admin.py    # Admin user management utilities
│   │   ├── bootstrap.py # Data initialization and admin creation
│   │   ├── common.py   # Common utility functions
│   │   ├── migrations.py # Migration helper functions
│   │   ├── slicer.py   # Slicer utilities
│   │   ├── study.py    # Study-related utilities
│   │   └── validation.py # Validation utilities
│   └── frontend/        # Embedded Gleam/Lustre frontend
│       ├── src/         # Gleam source code
│       │   ├── api/     # API client and HTTP
│       │   ├── components/ # UI components
│       │   │   ├── layout.gleam
│       │   │   └── forms/  # Form components
│       │   ├── pages/   # Application pages
│       │   │   ├── home.gleam
│       │   │   ├── login.gleam
│       │   │   ├── register.gleam
│       │   │   ├── records/  # Record management pages
│       │   │   ├── studies/
│       │   │   └── users/
│       │   ├── utils/   # Utility modules
│       │   ├── store.gleam # State management
│       │   ├── router.gleam # Client-side routing
│       │   └── main.gleam # Entry point
│       ├── public/      # Public assets
│       ├── static/      # Static HTML/CSS
│       ├── build/       # Build artifacts (generated)
│       └── gleam.toml   # Gleam configuration
├── dist/                # Built frontend (generated)
├── scripts/             # Build scripts
│   └── build_frontend.sh
├── tests/               # Test suite
│   ├── conftest.py
│   ├── test_client.py   # Client library tests
│   ├── integration/     # Integration tests
│   │   ├── test_app.py
│   │   ├── test_api_endpoints.py
│   │   ├── test_record_crud.py
│   │   ├── test_study_crud.py
│   │   └── test_user_crud.py
│   ├── e2e/             # End-to-end tests
│   │   └── test_auth_workflows.py
│   └── utils/
├── examples/            # Examples and templates
│   ├── test/
│   └── test_front/
├── data/                # Data storage (gitignored)
├── .github/             # CI/CD workflows
├── Makefile            # Build automation
├── pyproject.toml      # Package configuration
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

### Pure Gleam Frontend

The frontend is written entirely in pure Gleam without JavaScript FFI:
- All functionality uses native Gleam libraries
- HTTP requests via gleam_fetch (automatic cookie handling)
- DOM manipulation via plinth
- Client-side routing via modem
- No custom JavaScript required

### Development Commands

```bash
# Backend Development
uvicorn src.api.app:app --reload  # Run API server
clarinet run --with-frontend      # Run with frontend

# Frontend Development
cd src/frontend  # Frontend is embedded in src/
gleam deps download               # Install dependencies
gleam build --target javascript   # Build for browser
cd ../..

# Build Commands
make frontend-build               # Build production frontend
make frontend-clean               # Clean frontend artifacts
make frontend-test                # Run frontend tests
make run-dev                      # Run full stack development
make build                        # Build entire package

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

Clarinet provides utilities to simplify Alembic integration in your projects. As a framework, Clarinet doesn't include Alembic configuration directly, but offers helper functions to manage migrations in user projects.

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
