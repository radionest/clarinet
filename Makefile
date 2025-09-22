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
		cd src/frontend && \
		rm -rf build/ && \
		gleam deps download && \
		gleam build --target javascript && \
		cd ../.. && \
		rm -rf dist && \
		mkdir -p dist/js dist/css dist/assets && \
		cp -r src/frontend/build/dev/javascript/* dist/js/ && \
		if [ -d "src/frontend/public" ]; then \
			cp -r src/frontend/public/* dist/; \
		fi && \
		echo "Frontend build complete! Output in dist/"; \
	fi

.PHONY: frontend-deps
frontend-deps: ## Install frontend dependencies
	@echo "Installing frontend dependencies..."
	@cd src/frontend && gleam deps download

.PHONY: frontend-test
frontend-test: ## Run frontend tests
	@echo "Running frontend tests..."
	@cd src/frontend && gleam test

.PHONY: frontend-clean
frontend-clean: ## Clean frontend build artifacts
	@echo "Cleaning frontend artifacts..."
	@rm -rf src/frontend/build
	@rm -rf dist

.PHONY: run-dev
run-dev: ## Run development server with frontend
	@echo "Starting development server with frontend..."
	@clarinet run --with-frontend

.PHONY: run-api
run-api: ## Run API server only (no frontend)
	@echo "Starting API server..."
	@uvicorn src.api.app:app --reload --host 127.0.0.1 --port 8000

# =============================================================================
# Code Quality Commands
# =============================================================================

.PHONY: format
format: ## Format code with ruff
	@echo "Formatting code with ruff..."
	@ruff format src/ tests/

.PHONY: lint
lint: ## Check code with ruff (with fixes)
	@echo "Checking code with ruff..."
	@ruff check src/ tests/ --fix

.PHONY: typecheck
typecheck: ## Type check with mypy
	@echo "Type checking with mypy..."
	@mypy src/

.PHONY: pre-commit
pre-commit: ## Run pre-commit hooks
	@echo "Running pre-commit hooks..."
	@pre-commit run --all-files

.PHONY: pre-commit-install
pre-commit-install: ## Install pre-commit hooks
	@echo "Installing pre-commit hooks..."
	@pre-commit install

# =============================================================================
# Testing Commands
# =============================================================================

.PHONY: test
test: ## Run backend tests
	@echo "Running backend tests..."
	@pytest

.PHONY: test-cov
test-cov: ## Run tests with coverage
	@echo "Running tests with coverage..."
	@pytest --cov=src tests/

.PHONY: test-all
test-all: test frontend-test ## Run all tests (backend + frontend)

.PHONY: test-integration
test-integration: ## Run integration tests only
	@echo "Running integration tests..."
	@pytest tests/integration/

# =============================================================================
# Build and Install Commands
# =============================================================================

.PHONY: build
build: frontend-build ## Build complete package (backend + frontend)
	@echo "Building Clarinet package..."
	@python -m build

.PHONY: install
install: ## Install package in development mode
	@echo "Installing package in development mode..."
	@pip install -e .

.PHONY: dev-setup
dev-setup: ## Set up development environment
	@echo "Setting up development environment..."
	@pip install -e ".[dev]"
	@echo "Installing frontend dependencies..."
	@cd src/frontend && gleam deps download
	@echo "Installing pre-commit hooks..."
	@pre-commit install
	@echo "Development environment ready!"

# =============================================================================
# Database Commands
# =============================================================================

.PHONY: db-upgrade
db-upgrade: ## Apply database migrations
	@echo "Applying database migrations..."
	@alembic upgrade head

.PHONY: db-downgrade
db-downgrade: ## Rollback last migration
	@echo "Rolling back last migration..."
	@alembic downgrade -1

.PHONY: db-migration
db-migration: ## Create new migration from model changes
	@echo "Creating new migration..."
	@read -p "Enter migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"

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

# Default target
.DEFAULT_GOAL := help