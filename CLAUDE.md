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
- Async/await for all I/O; `asyncio.gather()` for parallel independent tasks (except on shared `AsyncSession` — see `clarinet/CLAUDE.md`)

## Essential Commands

**Split**: Makefile = developer workflow (test, lint, build), CLI = operator/production tasks
(init, run, db, admin, worker, session, rabbitmq, ohif, deploy). `make help` lists all targets.

### Development (Makefile)

```bash
# Run servers
make run-dev                    # Full stack (API + frontend)
make run-api                    # API only (--headless)

# Code quality
make format                     # ruff format
make lint                       # ruff check --fix
make typecheck                  # mypy
make check                      # format + lint + typecheck in one pass
make pre-commit                 # All pre-commit hooks

# Running make/uv commands — never pipe (| tail, | tee) — truncates or buffers output
# For long-running commands, redirect to file:
# Use unique filenames when multiple worktrees may run in parallel:
#   timeout 120 make test-unit > /tmp/test-{worktree}.txt 2>&1
make test-fast                  # All tests in parallel, excludes schema (default)
make test-unit                  # DB-only tests in parallel (no external services)
make test                       # All tests sequential
make test-cov                   # With coverage
make test-integration           # Integration tests only
make test-all-stages            # Full pipeline (40min timeout): lint → unit → schema‖VM → fast → PG → E2E
                                # SKIP_VM=1 / SKIP_SCHEMA=1 to skip heavy stages, KEEP_VM=1 to keep VM

# Database (Alembic wrappers)
make db-upgrade                 # Apply migrations
make db-downgrade               # Rollback last migration
make db-migration               # Create new migration

# Frontend (gleam wrappers — faster than CLI)
make frontend-build             # Production build
make frontend-deps              # Install dependencies

# Build & cleanup
make build                      # Full package build
make clean                      # Clean artifacts
make dev-setup                  # Set up dev environment
```

### Operations (CLI)

```bash
uv run clarinet init [path]              # Scaffold a new project
uv run clarinet run [--headless]         # Start the server
uv run clarinet db init                  # Initialize DB with admin user
uv run clarinet admin create             # Create admin user
uv run clarinet admin reset-password     # Reset admin password
uv run clarinet worker [--queues ...]    # Run pipeline worker
uv run clarinet session stats            # Session statistics
uv run clarinet session cleanup          # Clean expired sessions (+ --days retention)
uv run clarinet session cleanup-once     # One-shot cleanup via SessionCleanupService
uv run clarinet session list-user UID    # List a user's sessions
uv run clarinet session revoke-user UID  # Revoke all sessions for a user
uv run clarinet session cleanup-all      # Delete ALL sessions (asks for confirmation)
uv run clarinet rabbitmq clean           # Delete orphaned test queues/exchanges
uv run clarinet rabbitmq status          # Show queue statistics
uv run clarinet ohif install             # Download/install OHIF Viewer (served at /ohif)
uv run clarinet deploy systemd           # Generate systemd unit files
```

## Anti-patterns

Avoid: direct loguru import (use `from clarinet.utils.logger import logger`), sync blocking ops in async functions.

### Token efficiency

- If a task requires code changes — `EnterWorktree` first, then explore. Don't read files on main only to re-read them in worktree
- Don't re-run a test without an `Edit` between runs. Flaky test — tell the user, don't retry silently
- `make check` instead of separate format/lint/typecheck calls
- Read related files in parallel (up to 5 Read calls in one message), not sequentially
- Don't use TaskCreate/TaskUpdate to break work into phases — only when the user explicitly asks for progress tracking

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

- `make check` passes (format + lint + typecheck)
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
  - `project-setup.md` — project init, settings, plan/ structure (for settings.toml and plan/)
  - `e2e-tests.md` — frontend stack, VM sub-path, selectors (for deploy/test/e2e/)

Update the most specific file. Keep CLAUDE.md files minimal — move detailed reference to `.claude/rules/`.
