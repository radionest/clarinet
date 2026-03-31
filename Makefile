# Makefile for Clarinet Framework
# Following KISS and YAGNI principles - minimal, practical implementation

.PHONY: help
help: ## Show this help message
	@echo "Clarinet Framework - Build and Development Commands"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Frontend commands:"
	@grep -E '^(frontend|run-dev)[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""
	@echo "Code quality commands:"
	@grep -E '^(format|lint|typecheck|pre-commit)[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
	@echo ""
	@echo "Testing commands:"
	@grep -E '^test[^:]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'
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

.PHONY: ohif-build
ohif-build: ## Download and install OHIF Viewer
	@uv run clarinet ohif install

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
test-fast: ## Run all tests in parallel (auto workers, excludes schema tests)
	@echo "Running all tests in parallel..."
	@./scripts/run_tests.sh -n auto --dist loadgroup -m "not schema"

.PHONY: test-unit
test-unit: ## Run DB-only tests in parallel (no external services, no schema)
	@echo "Running DB-only tests in parallel..."
	@./scripts/run_tests.sh -n auto --dist loadgroup -m "not pipeline and not dicom and not slicer and not schema"

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
	@CLARINET_LOG_DIR=/tmp ./scripts/run_tests.sh -n auto --dist loadgroup

.PHONY: test-integration
test-integration: ## Run integration tests only
	@echo "Running integration tests..."
	@./scripts/run_tests.sh tests/integration/

# Marker expression for tests that don't require external services
PYTEST_UNIT_MARKERS := not pipeline and not dicom and not slicer and not schema

.PHONY: test-py312
test-py312: ## Run unit tests on Python 3.12 (requires uv + python3.12)
	@command -v uv >/dev/null 2>&1 || { echo "Error: uv is required but not installed"; exit 1; }
	@echo "Running unit tests on Python 3.12..."
	@uv run --python 3.12 pytest tests/ -n auto --dist loadgroup -m "$(PYTEST_UNIT_MARKERS)" -q

# =============================================================================
# Build and Install Commands
# =============================================================================

.PHONY: build
build: frontend-build ## Build complete package (backend + frontend)
	@echo "Building Clarinet package..."
	@uv build

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

.PHONY: clean-rabbitmq
clean-rabbitmq: ## Clean orphaned test queues/exchanges from RabbitMQ
	@uv run clarinet rabbitmq clean

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

.PHONY: vm-create
vm-create: ## Create test VM from cloud image
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
		"grep '^admin_password' /opt/clarinet/settings.toml | head -1 | sed 's/.*= *\"//;s/\".*//'"); \
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

.PHONY: vm-reimage
vm-reimage: ## Destroy + recreate VM (clean slate)
	@bash $(VM_SH) reimage

# Default target
.DEFAULT_GOAL := help
