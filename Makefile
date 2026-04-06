# Makefile for Clarinet Framework
# Following KISS and YAGNI principles - minimal, practical implementation

.PHONY: help
help: ## Show this help message
	@echo "Clarinet Framework - Build and Development Commands"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Frontend commands:"
	@grep -E '^(frontend|run-)[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""
	@echo "Code quality commands:"
	@grep -E '^(format|lint|typecheck|check|pre-commit)[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""
	@echo "Testing commands:"
	@grep -E '^test[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""
	@echo "Database commands:"
	@grep -E '^db-[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""
	@echo "Build and install commands:"
	@grep -E '^(build|install|dev-setup)[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""
	@echo "Utility commands:"
	@grep -E '^clean[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""
	@echo "VM deploy commands:"
	@grep -E '^vm-[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

# =============================================================================
# Frontend Commands
# =============================================================================

.PHONY: frontend-build
frontend-build: ## Build frontend for production
	@echo "Building Clarinet frontend..."
	@if [ -f "scripts/build_frontend.sh" ]; then \
		bash scripts/build_frontend.sh; \
	else \
		cd clarinet/frontend && \
		rm -rf build/ && \
		gleam deps download && \
		gleam run -m lustre/dev build --minify && \
		if [ -d "public" ]; then \
			cp -r public/* ../../clarinet/static/; \
		fi && \
		echo "Frontend build complete! Output in clarinet/static/"; \
	fi

.PHONY: frontend-deps
frontend-deps: ## Install frontend dependencies
	@echo "Installing frontend dependencies..."
	@cd clarinet/frontend && gleam deps download

.PHONY: frontend-test
frontend-test: ## Run frontend tests
	@echo "Running frontend tests..."
	@cd clarinet/frontend && gleam test

.PHONY: frontend-clean
frontend-clean: ## Clean frontend build artifacts
	@echo "Cleaning frontend artifacts..."
	@rm -rf clarinet/frontend/build
	@rm -rf clarinet/static

.PHONY: run-dev
run-dev: ## Run development server with frontend (default)
	@echo "Starting development server with frontend..."
	@uv run clarinet run

.PHONY: run-api
run-api: ## Run API server only (no frontend)
	@echo "Starting API server..."
	@uv run clarinet run --headless

# =============================================================================
# Code Quality Commands
# =============================================================================

.PHONY: format
format: ## Format code with ruff
	@echo "Formatting code with ruff..."
	@uv run ruff format clarinet/ tests/

.PHONY: lint
lint: ## Check code with ruff (with fixes)
	@echo "Checking code with ruff..."
	@uv run ruff check clarinet/ tests/ --fix

.PHONY: typecheck
typecheck: ## Type check with mypy
	@echo "Type checking with mypy..."
	@uv run mypy clarinet/

.PHONY: pre-commit
pre-commit: ## Run pre-commit hooks (via prek)
	@echo "Running pre-commit hooks..."
	@uv run prek run --all-files

.PHONY: check
check: format lint typecheck ## Format + lint + typecheck in one pass

.PHONY: pre-commit-install
pre-commit-install: ## Install pre-commit hooks (via prek)
	@echo "Installing pre-commit hooks..."
	@uv run prek install

# =============================================================================
# Testing Commands
# =============================================================================

.PHONY: test
test: ## Run backend tests
	@echo "Running backend tests..."
	@./scripts/run_tests.sh

.PHONY: test-cov
test-cov: ## Run tests with coverage
	@echo "Running tests with coverage..."
	@./scripts/run_tests.sh --cov=clarinet tests/

.PHONY: test-all
test-all: test frontend-test ## Run all tests (backend + frontend)

.PHONY: test-fast
test-fast: ## Run all tests in parallel (excludes schema tests)
	@echo "Running all tests in parallel..."
	@./scripts/run_tests.sh -n "$(PYTEST_WORKERS)" --dist loadgroup -m "not schema" -q

.PHONY: test-unit
test-unit: ## Run DB-only tests in parallel (no external services)
	@echo "Running DB-only tests in parallel..."
	@./scripts/run_tests.sh -n "$(PYTEST_WORKERS)" --dist loadgroup -m "not pipeline and not dicom and not slicer and not schema" -q

.PHONY: test-schema
test-schema: ## Run API schema tests (Schemathesis property-based)
	@echo "Running API schema tests..."
	@./scripts/run_tests.sh tests/schema/ -m schema --no-header -q

.PHONY: test-schema-verbose
test-schema-verbose: ## Run schema tests with verbose output
	@echo "Running API schema tests (verbose)..."
	@./scripts/run_tests.sh tests/schema/ -m schema -v --tb=long

.PHONY: test-debug
test-debug: ## Run tests with full JSON diagnostics (test report + app logs)
	@echo "Running tests with full diagnostics..."
	@CLARINET_LOG_DIR=/tmp ./scripts/run_tests.sh -n "$(PYTEST_WORKERS)" --dist loadgroup

.PHONY: test-integration
test-integration: ## Run integration tests only
	@echo "Running integration tests..."
	@./scripts/run_tests.sh tests/integration/

# Marker expression for tests that don't require external services
PYTEST_UNIT_MARKERS := not pipeline and not dicom and not slicer and not schema

# Max xdist workers (override: PYTEST_WORKERS=4 make test-fast)
PYTEST_WORKERS ?= 10

.PHONY: test-migration
test-migration: ## Run migration tests (SQLite; set CLARINET_TEST_DATABASE_URL for PG)
	@echo "Running migration tests..."
	@./scripts/run_tests.sh tests/migration/ -m migration -v

.PHONY: test-py312
test-py312: ## Run unit tests on Python 3.12 (requires uv + python3.12)
	@command -v uv >/dev/null 2>&1 || { echo "Error: uv is required but not installed"; exit 1; }
	@echo "Running unit tests on Python 3.12..."
	@uv run --python 3.12 pytest tests/ -n "$(PYTEST_WORKERS)" --dist loadgroup -m "$(PYTEST_UNIT_MARKERS)" -q

.PHONY: test-all-stages
test-all-stages: ## Full pipeline: lint → unit → schema‖VM → fast → PG → E2E (40min timeout)
	@timeout 2400 $(MAKE) _test-all-stages-impl

.PHONY: _test-all-stages-impl
_test-all-stages-impl:
	@echo ""
	@echo "=========================================="
	@echo "  Stage 1/8: lint + typecheck + frontend  "
	@echo "=========================================="
	@$(MAKE) lint & LINT_PID=$$!; \
	$(MAKE) typecheck & TC_PID=$$!; \
	$(MAKE) frontend-test & FE_PID=$$!; \
	FAIL=0; \
	wait $$LINT_PID || FAIL=1; \
	wait $$TC_PID || FAIL=1; \
	wait $$FE_PID || FAIL=1; \
	if [ $$FAIL -ne 0 ]; then echo "Stage 1 FAILED"; exit 1; fi
	@echo ""
	@echo "=========================================="
	@echo "  Stage 2/8: test-unit (DB-only, xdist)   "
	@echo "=========================================="
	@./scripts/run_tests.sh -n "$(PYTEST_WORKERS)" --dist loadgroup -m "not pipeline and not dicom and not slicer and not schema" -q
	@echo ""
	@echo "=========================================="
	@echo "  Stage 3/8: schema tests ‖ VM provision  "
	@echo "=========================================="
	@if [ "$${SKIP_VM}" != "1" ]; then \
		echo "Starting VM provision in background..."; \
		( bash $(VM_SH) reimage && \
		  rm -rf dist/ && \
		  $(MAKE) build && \
		  bash $(VM_SH) deploy dist/*.whl && \
		  echo "VM provisioned and deployed." >&2 \
		) > /tmp/clarinet-vm-provision.log 2>&1 & \
		VM_PID=$$!; \
	else \
		echo "SKIP_VM=1 — VM provision skipped"; \
		VM_PID=""; \
	fi; \
	SCHEMA_EXIT=0; \
	if [ "$${SKIP_SCHEMA}" = "1" ]; then \
		echo "SKIP_SCHEMA=1 — schema tests skipped"; \
	else \
		./scripts/run_tests.sh tests/schema/ -m schema --no-header -q || SCHEMA_EXIT=$$?; \
	fi; \
	if [ -n "$$VM_PID" ]; then \
		echo "Waiting for VM provision to finish..."; \
		wait $$VM_PID || { echo "VM provision FAILED (see /tmp/clarinet-vm-provision.log)"; exit 1; }; \
		echo "Waiting for services to start (15s)..."; \
		sleep 15; \
	fi; \
	if [ $$SCHEMA_EXIT -ne 0 ]; then echo "⚠ Schema tests FAILED (non-blocking — property-based tests are flaky)"; fi
	@echo ""
	@echo "=========================================="
	@echo "  Stage 4/8: vm-test-lib (deploy scripts)  "
	@echo "=========================================="
	@uv run pytest deploy/test/test_deploy_lib.py -v
	@if [ "$${SKIP_VM}" = "1" ]; then \
		echo ""; \
		echo "=========================================="; \
		echo "  Stage 5/8: test-fast — no VM, skip ext  "; \
		echo "=========================================="; \
		./scripts/run_tests.sh -n "$(PYTEST_WORKERS)" --dist loadgroup -m "not pipeline and not dicom and not slicer and not schema" -q; \
	else \
		echo ""; \
		echo "=========================================="; \
		echo "  Stage 5/8: test-fast (all, xdist + VM)  "; \
		echo "=========================================="; \
		VM_IP=$$(bash $(VM_SH) ip 2>/dev/null); \
		CLARINET_TEST_PACS_HOST="$$VM_IP" ./scripts/run_tests.sh -n "$(PYTEST_WORKERS)" --dist loadgroup -m "not schema" -q; \
	fi
	@if [ "$${SKIP_VM}" = "1" ]; then \
		echo ""; \
		echo "=========================================="; \
		echo "  Stages 6-8: VM — SKIPPED                "; \
		echo "=========================================="; \
	else \
		EXIT_CODE=0; \
		echo ""; \
		echo "=========================================="; \
		echo "  Stage 6/8: VM tests (PostgreSQL)         "; \
		echo "=========================================="; \
		bash scripts/vm-run-tests.sh || { rc=$$?; echo "Stage 6 FAILED (exit $$rc)"; EXIT_CODE=$$rc; }; \
		if [ $$EXIT_CODE -eq 0 ]; then \
			echo ""; \
			echo "=========================================="; \
			echo "  Stage 7/8: VM smoke + acceptance + e2e   "; \
			echo "=========================================="; \
			bash deploy/test/smoke-test.sh || { rc=$$?; echo "Stage 7 smoke FAILED (exit $$rc)"; EXIT_CODE=$$rc; }; \
			VM_IP=$$(bash $(VM_SH) ip 2>/dev/null); \
			. deploy/vm/vm.conf; \
			ADMIN_PASS=$$(ssh -o StrictHostKeyChecking=no -i "$$SSH_KEY_PATH" clarinet@$$VM_IP \
				"python3 -c \"import tomllib; print(tomllib.load(open('/opt/clarinet/settings.toml','rb'))['admin_password'])\""); \
			CLARINET_TEST_URL="https://$$VM_IP$${PATH_PREFIX}" \
			CLARINET_TEST_ADMIN_PASSWORD="$$ADMIN_PASS" \
			uv run pytest deploy/test/acceptance/ -v \
				|| { rc=$$?; echo "Stage 7 acceptance FAILED (exit $$rc)"; EXIT_CODE=$$rc; }; \
			CLARINET_TEST_URL="https://$$VM_IP$${PATH_PREFIX}" \
			CLARINET_TEST_ADMIN_PASSWORD="$$ADMIN_PASS" \
			uv run --group e2e pytest deploy/test/e2e/ -v --browser chromium \
				|| { rc=$$?; echo "Stage 7 e2e FAILED (exit $$rc)"; EXIT_CODE=$$rc; }; \
		fi; \
		echo ""; \
		echo "=========================================="; \
		echo "  Stage 8/8: VM cleanup                    "; \
		echo "=========================================="; \
		if [ "$${KEEP_VM}" = "1" ]; then \
			echo "KEEP_VM=1 — VM will not be destroyed"; \
		else \
			bash $(VM_SH) destroy || { rc=$$?; echo "VM destroy failed (exit $$rc)"; if [ $$EXIT_CODE -eq 0 ]; then EXIT_CODE=$$rc; fi; }; \
		fi; \
		if [ $$EXIT_CODE -ne 0 ]; then \
			echo ""; \
			echo "=========================================="; \
			echo "  Pipeline FAILED                         "; \
			echo "=========================================="; \
			exit $$EXIT_CODE; \
		fi; \
	fi
	@echo ""
	@echo "=========================================="
	@echo "  All stages passed!                      "
	@echo "=========================================="

# =============================================================================
# Build and Install Commands
# =============================================================================

.PHONY: build
build: frontend-build ## Build complete package (backend + frontend + deps)
	@echo "Building Clarinet package..."
	@uv build
	@$(MAKE) build-deps

.PHONY: build-deps
build-deps: ## Download dependency wheels for offline VM install
	@WHEEL=$$(ls -t dist/*.whl 2>/dev/null | head -1); \
	if [ -z "$$WHEEL" ]; then echo "No wheel found in dist/ — run 'make build' first"; exit 1; fi; \
	echo "Downloading dependency wheels for $$WHEEL..."; \
	mkdir -p dist/deps; \
	uv tool run --python 3.12 pip download \
		-d dist/deps "$$WHEEL[performance]"

.PHONY: dev-setup
dev-setup: ## Set up development environment
	@echo "Setting up development environment..."
	@uv sync --dev
	@echo "Installing frontend dependencies..."
	@cd clarinet/frontend && gleam deps download
	@echo "Installing pre-commit hooks..."
	@uv run prek install
	@echo "Development environment ready!"

# =============================================================================
# Database Commands
# =============================================================================

.PHONY: db-upgrade
db-upgrade: ## Apply database migrations
	@echo "Applying database migrations..."
	@uv run alembic upgrade head

.PHONY: db-downgrade
db-downgrade: ## Rollback last migration
	@echo "Rolling back last migration..."
	@uv run alembic downgrade -1

.PHONY: db-migration
db-migration: ## Create new migration from model changes
	@echo "Creating new migration..."
	@read -p "Enter migration message: " msg; \
	uv run alembic revision --autogenerate -m "$$msg"

# =============================================================================
# Utility Commands
# =============================================================================

.PHONY: clean
clean: frontend-clean ## Clean all build artifacts
	@echo "Cleaning all build artifacts..."
	@rm -rf build/
	@rm -rf *.egg-info
	@rm -rf .pytest_cache
	@rm -rf .mypy_cache
	@rm -rf .ruff_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@echo "Clean complete!"

.PHONY: clean-all
clean-all: clean ## Deep clean including virtual environment
	@echo "Performing deep clean..."
	@rm -rf .venv
	@rm -rf node_modules
	@echo "Deep clean complete!"

# =============================================================================
# VM Deploy Commands
# =============================================================================

VM_SH := deploy/vm/vm.sh

.PHONY: vm-setup
vm-setup: ## One-time host setup for VM creation (permissions + libvirt check)
	@bash $(VM_SH) setup

.PHONY: vm-bake
vm-bake: ## Create golden image (one-time, ~10 min). Usage: make vm-bake [DICOM=/path/to/dicoms]
	@bash $(VM_SH) bake "$(DICOM)"

.PHONY: vm-create
vm-create: ## Create test VM (uses golden image if available, otherwise plain cloud image)
	@bash $(VM_SH) create

.PHONY: vm-destroy
vm-destroy: ## Destroy test VM and all storage
	@bash $(VM_SH) destroy

.PHONY: vm-ssh
vm-ssh: ## SSH into test VM
	@bash $(VM_SH) ssh

.PHONY: vm-status
vm-status: ## Show test VM status
	@bash $(VM_SH) status

.PHONY: vm-deploy
vm-deploy: ## Download latest release wheel and deploy to VM
	@bash $(VM_SH) deploy

.PHONY: vm-deploy-local
vm-deploy-local: ## Build local wheel and deploy to VM
	@rm -rf dist/
	@$(MAKE) build
	@bash $(VM_SH) deploy dist/*.whl

.PHONY: vm-smoke
vm-smoke: ## Run smoke tests against running VM
	@bash deploy/test/smoke-test.sh

.PHONY: vm-acceptance
vm-acceptance: ## Run acceptance tests (pytest) against running VM
	@VM_IP=$$(bash $(VM_SH) ip 2>/dev/null); \
	. deploy/vm/vm.conf; \
	ADMIN_PASS=$$(ssh -o StrictHostKeyChecking=no clarinet@$$VM_IP \
		"python3 -c \"import tomllib; print(tomllib.load(open('/opt/clarinet/settings.toml','rb'))['admin_password'])\""); \
	CLARINET_TEST_URL="https://$$VM_IP$${PATH_PREFIX}" \
	CLARINET_TEST_ADMIN_PASSWORD="$$ADMIN_PASS" \
	uv run pytest deploy/test/acceptance/ -v

.PHONY: vm-e2e
vm-e2e: ## Run Playwright E2E tests against running VM
	@VM_IP=$$(bash $(VM_SH) ip 2>/dev/null); \
	. deploy/vm/vm.conf; \
	ADMIN_PASS=$$(ssh -o StrictHostKeyChecking=no clarinet@"$$VM_IP" \
		"grep '^admin_password' /opt/clarinet/settings.toml | head -1 | sed 's/.*= *\"//;s/\".*//'" 2>/dev/null); \
	CLARINET_TEST_URL="https://$$VM_IP$${PATH_PREFIX}" \
	CLARINET_TEST_ADMIN_PASSWORD="$$ADMIN_PASS" \
	uv run --group e2e pytest deploy/test/e2e/ -v --browser chromium

.PHONY: vm-test-lib
vm-test-lib: ## Test deploy/lib/ scripts (logging, common)
	@uv run pytest deploy/test/test_deploy_lib.py -v

.PHONY: vm-test
vm-test: ## Full E2E: create VM -> deploy -> test -> cleanup
	@bash deploy/test/deploy-test.sh

.PHONY: vm-test-all
vm-test-all: ## Run full test suite against VM PostgreSQL
	@bash scripts/vm-test-all.sh

.PHONY: vm-test-pg
vm-test-pg: ## Run specific tests against VM PostgreSQL (FILE=tests/... or empty for all)
	@bash scripts/vm-test-all.sh $(FILE)

.PHONY: vm-reset-testdb
vm-reset-testdb: ## Drop and recreate clarinet_test database on VM
	@VM_IP=$$(bash $(VM_SH) ip 2>/dev/null); \
	echo "Resetting clarinet_test on $$VM_IP..."; \
	ssh -o StrictHostKeyChecking=no "clarinet@$$VM_IP" "\
		sudo -u postgres psql -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='clarinet_test' AND pid <> pg_backend_pid();\" 2>/dev/null; \
		sudo -u postgres dropdb --if-exists clarinet_test; \
		sudo -u postgres createdb --owner=clarinet clarinet_test" && \
	echo "Done."

.PHONY: vm-reimage
vm-reimage: ## Destroy + recreate VM (clean slate)
	@bash $(VM_SH) reimage

# Default target
.DEFAULT_GOAL := help
