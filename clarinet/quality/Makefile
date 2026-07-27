# Quality targets for a clarinet downstream project.
# Installed by `clarinet quality init`. Requires the dev dependency group from
# the pyproject fragment that command prints.

# Only the dirs that actually exist in this project. A freshly scaffolded
# project has plan/ but not necessarily tests/ or scripts/ -- hardcoding all
# three makes every recipe fail on arrival with a "no such file or directory"
# that has nothing to do with code quality.
QUALITY_PATHS := $(wildcard plan tests scripts)

.PHONY: help format lint typecheck check test _require-tools _require-paths

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

_require-paths:
	@if [ -z "$(QUALITY_PATHS)" ]; then \
	  echo "ERROR: none of plan/, tests/, scripts/ exist in $(CURDIR)."; \
	  echo "       Run make from your project root (the directory containing plan/)."; \
	  exit 1; \
	fi

format: _require-tools _require-paths ## Format with ruff
	@uv run ruff format $(QUALITY_PATHS)

lint: _require-tools _require-paths ## Lint with ruff (autofix)
	@uv run ruff check $(QUALITY_PATHS) --fix

# tests/ is excluded outright in mypy.ini; passing an explicitly-named
# directory that mypy's exclude wholly covers is a hard error ("There are no
# .py[i] files in directory 'tests'"), not a silent no-op -- so it is
# filtered back out of the shared path list here specifically.
typecheck: _require-tools _require-paths ## Type-check with mypy
	@uv run mypy $(filter-out tests,$(QUALITY_PATHS))

check: format lint typecheck ## Format + lint + typecheck in one pass

test: ## Run the test suite
	@uv run pytest
