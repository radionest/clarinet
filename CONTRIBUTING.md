# Contributing to Clarinet

## Development setup

Clarinet uses **`uv`** as the canonical Python toolchain. Install it from
<https://docs.astral.sh/uv/>.

Then bootstrap the dev environment:

```bash
make dev-setup        # install dependencies + pre-commit hooks
```

## Pre-commit hooks

The mypy pre-commit hook (`.pre-commit-config.yaml`) runs `uv run mypy clarinet`
against the project venv. This means **`uv` must be on `PATH`** wherever
pre-commit is invoked (your shell, your IDE's git hook). Without `uv` the hook
fails with `uv: command not found`.

Mypy is **not** run in GitHub Actions CI — type errors are caught only by the
pre-commit hook locally or by an explicit `make typecheck` invocation in a PR
checklist. Please run `make check` (format + lint + typecheck) before pushing.
