# Quality targets for a clarinet downstream project.
# Installed by `clarinet quality init`. Requires the dev dependency group from
# the pyproject fragment that command prints.

.PHONY: help format lint typecheck check test

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

_require-tools:
	@uv run ruff --version > /dev/null 2>&1 || { \
	  echo "ERROR: ruff is not installed. Merge the [dependency-groups] dev block"; \
	  echo "       printed by 'clarinet quality init' into pyproject.toml, then 'uv sync'."; \
	  exit 1; }
	@uv run mypy --version > /dev/null 2>&1 || { \
	  echo "ERROR: mypy is not installed. Merge the [dependency-groups] dev block"; \
	  echo "       printed by 'clarinet quality init' into pyproject.toml, then 'uv sync'."; \
	  exit 1; }

format: _require-tools ## Format with ruff
	@uv run ruff format plan/ tests/ scripts/

lint: _require-tools ## Lint with ruff (autofix)
	@uv run ruff check plan/ tests/ scripts/ --fix

typecheck: _require-tools ## Type-check with mypy
	@uv run mypy plan/ scripts/

check: format lint typecheck ## Format + lint + typecheck in one pass

test: ## Run the test suite
	@uv run pytest
