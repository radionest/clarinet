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
make frontend-check             # Type-check (gleam check)
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
- **Edit existing files, Write only new ones.** `Write` on a path that already exists fails with `File has not been read yet` and forces an extra `Read` round-trip. For ≥3 sequential edits on one file, prefer a single `Read` + several `Edit`s over multiple `Write` rewrites
- After a structural change (function signature, behaviour of a shared helper), `make test-fast -k <module>` BEFORE `make check` — type/lint pass doesn't catch behavioural regressions in tests that mocked the old contract

## Worktree Workflow

- Feature development: always enter a worktree via `EnterWorktree` before making changes
- **`Plan` / `python-developer` / `feature-dev:*` agents are blocked on main by `require-worktree-agent.sh`** (read-only `feature-dev:code-explorer` and `feature-dev:code-reviewer` are exempt). Enter the worktree before launching them — applies to research stages too (e.g. `feature-dev` phase 4 architecture spawns), not only when editing
- Quick fixes, typos, config changes — work directly in main, no worktree needed. **But** `require-worktree.sh` blocks `Edit`/`Write` on repo files when on main; `.claude/` is exempted (shared infra — edit in main), everything else under `clarinet/`, `tests/`, `alembic/`, `examples/` requires a worktree
- To resume work in an existing worktree — use `EnterWorktree` with the same name. Never `cd` into worktree path directly
- **"Goto worktree of branch X"** — first run `git worktree list`, find the worktree that has branch X, then `EnterWorktree(name=<worktree-name>)`. Do NOT pass a branch name to `EnterWorktree` — it creates a *new* worktree with prefix `worktree-{name}`, causing double-prefix bugs
- Worktrees contain only git-tracked files. **Checked-in** under `.claude/` (whitelisted in `.gitignore`, visible in every worktree, edit normally + commit): `rules/`, `agents/`, `commands/`. **Shared** via `$CLAUDE_PROJECT_DIR/.claude/` (NOT in git, edit only via the main project path): `hooks/`, `settings.json`, `settings.local.json`, plus runtime dirs (`plans/`, `projects/`, `worktrees/`). Build artifacts (formosh) are not copied
- **First `make`/`pytest`/`uv` command in a fresh worktree creates a new venv** — wrap with `timeout 300` (or omit `timeout` for the very first invocation). Subsequent runs share the venv and need only the usual `timeout 120`
- `ExitWorktree(remove)` requires `discard_changes=true` if there are commits not in main (even if already pushed)
- **`gh pr merge --delete-branch` from inside a worktree leaves it on a deleted branch** — `git status` later shows "no branch / detached". Run `gh pr merge` from main and then `ExitWorktree(remove, discard_changes=true)`, or skip `--delete-branch` and let `ExitWorktree(remove)` clean up locally
- **For PRs in review prefer `ExitWorktree(keep)` until merge** — review cycles need the same branch back; `EnterWorktree` only creates new branches, so resuming after `remove` means manual `git worktree add` (awkward, easy to violate "no branch switch in root")
- **`gh pr create` gated by pre-PR review hook — run `Agent(subagent_type=pr-diff-reviewer)` BEFORE the FIRST `gh pr create`, not before merge.** Issues found pre-create cost one review cycle; the same issues found mid-review cost N cycles (real incident: PR #257 issues 7/8). `SKIP_PR_REVIEW=1` is **forbidden** unless the user explicitly asks for it. Re-running before merge is only needed for substantive changes after review feedback — for ≤20 lines of edits to already-reviewed files the hook auto-updates the marker.
- The Stop hook blocks session end in a worktree — ask the user to choose:
  1. **Push + PR**: commit all → `git push -u origin <branch>` → `Agent(pr-diff-reviewer)` → `gh pr create` → `ExitWorktree(keep)` (remove only after PR merges)
  2. **Keep**: `ExitWorktree(keep)` — worktree stays for later
  3. **Discard**: `ExitWorktree(remove, discard_changes=true)`

## Pre-commit Checklist

- `make check` passes (format + lint + typecheck)
- **After `make check` — `Read` files again before any further `Edit`**: `ruff format` may have rewritten the source (line wrap, trailing commas, import order), and stale `Edit` strings will fail with "old_string not found"
- New untyped Python dependency → add it to a `[[tool.mypy.overrides]]` block with `ignore_missing_imports = true` in `pyproject.toml`. Otherwise `make typecheck` fails with `Library stubs not installed for "<pkg>"` after the first import
- Tests written and passing
- pr-diff-reviewer прогнан перед **первым** `gh pr create` (см. Worktree Workflow); не использовать `SKIP_PR_REVIEW=1` без явной просьбы пользователя
- Docstrings on non-trivial public functions
- No secrets in code
- Conventional commit messages
- DB migrations created for schema changes

## Documentation Structure

- **Scoped CLAUDE.md** in subdirectories — always-loaded when entering that directory
- **Path-scoped rules** in `.claude/rules/` — auto-loaded only when editing matching files. See [`.claude/rules/README.md`](.claude/rules/README.md) for the topic index.

Update the most specific file. Keep CLAUDE.md files minimal — move detailed reference to `.claude/rules/`.
