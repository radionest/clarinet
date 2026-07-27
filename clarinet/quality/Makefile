# Quality targets for a clarinet downstream project.
# Installed by `clarinet quality init`. Requires the dev dependency group from
# the pyproject fragment that command prints.

# Only the dirs that actually exist in this project. A freshly scaffolded
# project has plan/ but not necessarily tests/ or scripts/ -- hardcoding all
# three makes every recipe fail on arrival with a "no such file or directory"
# that has nothing to do with code quality.
QUALITY_PATHS := $(wildcard plan tests scripts)

# mypy's own candidate list, derived separately from QUALITY_PATHS: tests/ is
# excluded outright in mypy.ini, so passing it explicitly is a hard error
# ("There are no .py[i] files in directory 'tests'"), not a silent no-op --
# dropped unconditionally here, mirroring mypy.ini's own exclusion. Of what's
# left, keep only directories that actually contain a .py file: an existing
# but non-Python directory (e.g. a scripts/ holding only shell scripts) hits
# that identical hard error. $(strip ...) is required, not cosmetic:
# $(foreach) joins per-element results with spaces, so when two or more
# candidates both fail the content check the unstripped value is " " (one
# space), not "" -- which slips right past a plain -z emptiness check. The
# typecheck recipe below guards this (stripped) list for emptiness so
# `make check` still exits 0 when nothing is left to type-check, instead of
# invoking `mypy` with zero arguments ("Missing target module, package,
# files, or command.").
MYPY_PATHS := $(strip $(foreach d,$(filter-out tests,$(QUALITY_PATHS)),$(if $(shell find $(d) -name '*.py' -print -quit 2>/dev/null),$(d))))

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

typecheck: _require-tools _require-paths ## Type-check with mypy
	@if [ -z "$(MYPY_PATHS)" ]; then \
	  echo "No Python sources to type-check."; \
	else \
	  uv run mypy $(MYPY_PATHS); \
	fi

check: format lint typecheck ## Format + lint + typecheck in one pass

test: ## Run the test suite
	@uv run pytest
