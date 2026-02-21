# Clarinet — Code Style Guide

Clarinet is a framework for clinical-radiological studies, built on FastAPI, SQLModel, and async architecture.

## Architecture

- Follow KISS, SOLID, DRY, YAGNI principles
- Composition over inheritance; each module has a single purpose
- **Repository pattern**: all DB access through `src/repositories/`
- **Service layer**: `src/services/` uses repositories; routers use services/repos
- **Dependency injection**: `Annotated[X, Depends()]` aliases in `src/api/dependencies.py`
- **Exception flow**: repos raise domain exceptions (`src/exceptions/domain.py`) → exception handlers convert to HTTP responses
- **Logger**: always `from src.utils.logger import logger` — never import loguru directly
- **Settings**: `from src.settings import settings` — env vars with `CLARINET_` prefix

## Code Style

- All Python tools run through **uv**: `uv run <command>`
- **Ruff** for formatting + linting (config in `.ruff.toml`): line length 100, target Python 3.14
- **mypy** strict mode: `uv run mypy src/`
- Type hints on all functions; `Optional[T]` not `Union[T, None]`
- Group imports: stdlib → third-party → local (relative within package)
- Google-style docstrings for public functions
- Custom exceptions from `src.exceptions.http` (NOT_FOUND, CONFLICT, etc.)
- Async/await for all I/O; `asyncio.gather()` for parallel independent tasks
- No bare except — always specify exception type

## Essential Commands

```bash
# Development
make run-dev                    # Full stack (API + frontend)
make run-api                    # API only

# Code quality
make format                     # ruff format
make lint                       # ruff check --fix
make typecheck                  # mypy
make pre-commit                 # All pre-commit hooks

# Testing
make test                       # Backend tests
make test-cov                   # With coverage
make test-integration           # Integration tests only

# Database
make db-upgrade                 # Apply migrations
make db-downgrade               # Rollback last migration
uv run clarinet db init         # Initialize DB with admin user

# Frontend
make frontend-build             # Production build
make frontend-deps              # Install dependencies

# Build & cleanup
make build                      # Full package build
make clean                      # Clean artifacts
make dev-setup                  # Set up dev environment
```

## Anti-patterns

Avoid: god objects, callback hell, global variables, magic numbers, code duplication,
ignoring errors, hardcoded config, direct loguru import, sync ops in async functions, bare except.

## Pre-commit Checklist

- `make format` + `make lint` pass
- `make typecheck` passes
- Tests written and passing
- Docstrings on public functions
- No secrets in code
- Conventional commit messages
- DB migrations created for schema changes

## CLAUDE.md Maintenance

After completing any task, review and update CLAUDE.md files if your changes:
- Introduced new architectural patterns or conventions
- Added/changed CLI commands or build steps
- Modified project structure (new directories, moved files)
- Changed technology stack or dependencies
- Fixed bugs caused by outdated documentation

Scoped CLAUDE.md files exist in: `src/`, `src/models/`, `src/repositories/`, `src/api/`, `src/frontend/`, `src/services/recordflow/`, `tests/`.
Update the most specific file. Keep root CLAUDE.md minimal — move details to subdirectory files.
