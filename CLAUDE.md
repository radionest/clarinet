# Clarinet — Code Style Guide

Clarinet is a framework for clinical-radiological studies, built on FastAPI, SQLModel, and async architecture.

## Architecture

- Follow KISS, SOLID, DRY, YAGNI principles
- Composition over inheritance; each module has a single purpose
- **Repository pattern**: all DB access through `clarinet/repositories/`
- **Service layer**: `clarinet/services/` uses repositories; routers use services/repos
- **Dependency injection**: `Annotated[X, Depends()]` aliases in `clarinet/api/dependencies.py`
- **Exception flow**: repos raise domain exceptions (`clarinet/exceptions/domain.py`) → exception handlers convert to HTTP responses
- **Logger**: always `from clarinet.utils.logger import logger` — never import loguru directly
- **Settings**: `from clarinet.settings import settings` — env vars with `CLARINET_` prefix

## Code Style

- All Python tools run through **uv**: `uv run <command>`
- Type hints on all functions; `Optional[T]` not `Union[T, None]`
- Google-style docstrings for public functions
- Custom exceptions from `clarinet.exceptions.http` (NOT_FOUND, CONFLICT, etc.)
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
make test-fast                  # All tests in parallel, excludes schema (default)
make test-unit                  # DB-only tests in parallel (no external services)
make test                       # All tests sequential
make test-cov                   # With coverage
make test-integration           # Integration tests only

# Database
make db-upgrade                 # Apply migrations
make db-downgrade               # Rollback last migration
uv run clarinet db init         # Initialize DB with admin user

# Frontend
make frontend-build             # Production build
make frontend-deps              # Install dependencies

# OHIF Viewer (optional)
make ohif-build                 # Download and install OHIF Viewer (served at /ohif)

# Build & cleanup
make build                      # Full package build
make clean                      # Clean artifacts
make dev-setup                  # Set up dev environment
```

## Anti-patterns

Avoid: god objects, callback hell, global variables, magic numbers, code duplication,
ignoring errors, hardcoded config, direct loguru import, sync ops in async functions, bare except.

## Worktree Workflow

- Feature development: always enter a worktree via `EnterWorktree` before making changes
- Quick fixes, typos, config changes — work directly in main, no worktree needed
- The Stop hook blocks session end in a worktree — ask the user to choose:
  1. **Push + PR**: commit all, `git push -u origin <branch>`, `gh pr create`, then `ExitWorktree(remove)`
  2. **Keep**: `ExitWorktree(keep)` — worktree stays for later
  3. **Discard**: `ExitWorktree(remove, discard_changes=true)`

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

Scoped CLAUDE.md files exist in: `clarinet/`, `clarinet/models/`, `clarinet/repositories/`, `clarinet/api/`, `clarinet/frontend/`, `clarinet/frontend/build/packages/formosh/`, `clarinet/services/recordflow/`, `clarinet/services/pipeline/`, `clarinet/services/dicom/`, `clarinet/services/slicer/`, `clarinet/services/dicomweb/`, `tests/`.
Update the most specific file. Keep root CLAUDE.md minimal — move details to subdirectory files.
