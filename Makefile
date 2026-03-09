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
		gleam build --target javascript && \
		cd ../.. && \
		rm -rf dist && \
		mkdir -p dist/js dist/css dist/assets && \
		cp -r clarinet/frontend/build/dev/javascript/* dist/js/ && \
		if [ -d "clarinet/frontend/public" ]; then \
			cp -r clarinet/frontend/public/* dist/; \
		fi && \
		echo "Frontend build complete! Output in dist/"; \
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
	@rm -rf dist

.PHONY: ohif-build
ohif-build: ## Download and install OHIF Viewer
	@uv run clarinet ohif install

.PHONY: run-dev
run-dev: ## Run development server with frontend
	@echo "Starting development server with frontend..."
	@uv run clarinet run --with-frontend

.PHONY: run-api
run-api: ## Run API server only (no frontend)
	@echo "Starting API server..."
	@uv run uvicorn clarinet.api.app:app --reload --host 127.0.0.1 --port 8000

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
pre-commit: ## Run pre-commit hooks
	@echo "Running pre-commit hooks..."
	@uv run pre-commit run --all-files

.PHONY: pre-commit-install
pre-commit-install: ## Install pre-commit hooks
	@echo "Installing pre-commit hooks..."
	@uv run pre-commit install

# =============================================================================
# Testing Commands
# =============================================================================

.PHONY: test
test: ## Run backend tests
	@echo "Running backend tests..."
	@uv run pytest

.PHONY: test-cov
test-cov: ## Run tests with coverage
	@echo "Running tests with coverage..."
	@uv run pytest --cov=clarinet tests/

.PHONY: test-all
test-all: test frontend-test ## Run all tests (backend + frontend)

.PHONY: test-fast
test-fast: ## Run all tests in parallel (auto workers, all service groups)
	@echo "Running all tests in parallel..."
	@uv run pytest -n auto

.PHONY: test-unit
test-unit: ## Run DB-only tests in parallel (no external services)
	@echo "Running DB-only tests in parallel..."
	@uv run pytest -n auto -m "not pipeline and not dicom and not slicer"

.PHONY: test-integration
test-integration: ## Run integration tests only
	@echo "Running integration tests..."
	@uv run pytest tests/integration/

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
	@uv run pre-commit install
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

# Default target
.DEFAULT_GOAL := help