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
- Type hints on all functions
- Docstrings: required on public functions with non-obvious behavior. Skip on trivial CRUD where name + types suffice. Focus on "why", gotchas, raises
- Custom exceptions from `clarinet.exceptions.http` (NOT_FOUND, CONFLICT, etc.)
- Async/await for all I/O; `asyncio.gather()` for parallel independent tasks

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

Avoid: direct loguru import (use `from clarinet.utils.logger import logger`), sync blocking ops in async functions.

## Worktree Workflow

- Feature development: always enter a worktree via `EnterWorktree` before making changes
- Quick fixes, typos, config changes — work directly in main, no worktree needed
- To resume work in an existing worktree — use `EnterWorktree` with the same name. Never `cd` into worktree path directly
- Worktrees contain only git-tracked files. `hooks/`, `settings.json`, `settings.local.json` live in `$CLAUDE_PROJECT_DIR/.claude/` and are shared — edit them by the main project path. Build artifacts (formosh) are not copied
- `ExitWorktree(remove)` requires `discard_changes=true` if there are commits not in main (even if already pushed)
- The Stop hook blocks session end in a worktree — ask the user to choose:
  1. **Push + PR**: commit all, `git push -u origin <branch>`, `gh pr create`, then `ExitWorktree(remove, discard_changes=true)`
  2. **Keep**: `ExitWorktree(keep)` — worktree stays for later
  3. **Discard**: `ExitWorktree(remove, discard_changes=true)`

## Pre-commit Checklist

- `make format` + `make lint` pass
- `make typecheck` passes
- Tests written and passing
- Docstrings on non-trivial public functions
- No secrets in code
- Conventional commit messages
- DB migrations created for schema changes

## Documentation Structure

- **Scoped CLAUDE.md** in subdirectories — always-loaded when entering that directory
- **Path-scoped rules** in `.claude/rules/` — auto-loaded only when editing matching files:
  - `api-urls.md` — full endpoint URL table (for tests/ and routers/)
  - `api-deps.md` — DI aliases, RBAC, factory patterns, DICOMweb endpoints (for dependencies.py and routers/)
  - `pipeline-ops.md` — settings, testing, dependencies (for pipeline/)
  - `record-repo.md` — specialized methods, invalidation, auto_id (for record repos)
  - `slicer-context.md` — context builder & hydration (for context*.py and hydrators)
  - `slicer-helper-api.md` — SlicerHelper full API + VTK pitfalls (for helper.py)
  - `schemathesis.md` — property-based testing guide (for tests/schema/)
  - `file-registry.md` — file definition M2M system (for file_schema.py)
  - `test-debugging.md` — jq recipes for test/log analysis (for tests/)
  - `recordflow-dsl.md` — full DSL API reference (for recordflow/ and *_flow.py)

Update the most specific file. Keep CLAUDE.md files minimal — move detailed reference to `.claude/rules/`.
