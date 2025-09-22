# Documentation Refactoring Proposal

## Executive Summary

This document outlines the inconsistencies found between the project documentation (README.md and CLAUDE.md) and the actual project structure, along with proposed fixes.

## Current Issues

### 1. Frontend Location Mismatch

**Issue**: Documentation shows frontend at root level, but it's actually inside `src/`

**Current Documentation**:
```
clarinet/
├── frontend/            # Gleam/Lustre frontend
│   ├── src/
│   ├── public/
│   └── gleam.toml
```

**Actual Structure**:
```
clarinet/
├── src/
│   ├── frontend/       # Frontend is inside src/
│   │   ├── src/
│   │   ├── public/
│   │   ├── static/     # Additional directory not documented
│   │   ├── build/      # Build artifacts
│   │   └── gleam.toml
```

### 2. Missing Documentation

The following directories/files exist but are not documented:

- **src/frontend/static/** - Static assets directory (separate from public/)

### 3. Examples Directory Structure

**Documented**: Generic "examples/" with usage examples
**Actual**:
```
examples/
├── .env.example
├── test/
└── test_front/
```

### 4. Frontend Migration in Progress

Git status shows:
- Many deleted frontend files from old location
- New untracked `frontend/` directory at root
- This suggests an ongoing refactoring that's not reflected in documentation

## Proposed Changes

### A. README.md Updates

#### 1. Update Project Structure Section (Line 24-52)

```markdown
## Project Structure

```
clarinet/
├── src/                    # Backend source code
│   ├── api/               # FastAPI application
│   │   ├── routers/       # API endpoints
│   │   ├── auth_config.py # Authentication configuration
│   │   └── app.py         # Main application
│   ├── models/            # SQLModel database models
│   ├── repositories/      # Data access layer
│   ├── services/          # Business logic
│   │   ├── dicom/         # DICOM processing
│   │   ├── image/         # Image processing
│   │   └── slicer/        # 3D Slicer integration
│   ├── exceptions/        # Custom exceptions
│   ├── utils/             # Utility functions
│   ├── cli/               # Command-line interface
│   └── frontend/          # Gleam/Lustre frontend (embedded)
│       ├── src/           # Gleam source code
│       ├── public/        # Public assets
│       ├── static/        # Static files (HTML, CSS)
│       ├── build/         # Build artifacts (generated)
│       └── gleam.toml     # Gleam configuration
├── frontend/              # Standalone frontend (if separated)
│   ├── src/              # Gleam source code
│   ├── public/           # Static assets
│   └── gleam.toml        # Gleam configuration
├── dist/                  # Built frontend distribution
│   ├── index.html        # SPA entry point
│   ├── js/               # Compiled JavaScript
│   └── css/              # Stylesheets
├── scripts/               # Build and utility scripts
│   └── build_frontend.sh # Frontend build script
├── tests/                 # Test suite
├── examples/              # Usage examples and templates
│   ├── .env.example      # Environment configuration template
│   ├── test/             # Test examples
│   └── test_front/       # Frontend test examples
├── data/                  # Data storage (gitignored)
├── .github/               # GitHub Actions workflows
├── Makefile              # Build automation
└── pyproject.toml        # Package configuration
```
```

#### 2. Update Frontend Development Section (Line 133-151)

Add note about current frontend location:

```markdown
### Frontend Development

**Note**: The frontend is currently located at `src/frontend/` but can be separated into a standalone `frontend/` directory at the root level.

```bash
# For embedded frontend (current structure)
cd src/frontend
gleam deps download
gleam build --target javascript

# For standalone frontend (after separation)
cd frontend
gleam deps download
gleam build --target javascript
```
```

#### 3. Add Makefile Documentation (New Section after Line 161)

```markdown
### Makefile Commands

The project includes a Makefile for common development tasks:

```bash
make help              # Show all available commands
make frontend-build    # Build production frontend
make frontend-clean    # Clean frontend artifacts
make frontend-test     # Run frontend tests
make run-dev          # Run full stack development
make build            # Build entire package
make test             # Run all tests
make lint             # Run linting
make format           # Format code
```
```

### B. CLAUDE.md Updates

#### 1. Update Project Structure Section (Line 206-244)

Replace with the same updated structure as in README.md, emphasizing the current location:

```markdown
### Project Structure (Current)

```tree
clarinet/
├── src/                 # Backend source code
│   ├── __init__.py
│   ├── __main__.py      # CLI entry point
│   ├── settings.py      # Application configuration
│   ├── exceptions/      # Custom exceptions module
│   │   ├── domain.py   # Domain exceptions
│   │   └── http.py     # HTTP exceptions
│   ├── types.py         # Common type definitions
│   ├── api/             # FastAPI application
│   │   ├── app.py      # Main application file
│   │   ├── routers/    # API endpoints
│   │   └── ...
│   ├── cli/             # CLI interface
│   ├── models/          # SQLModel models
│   ├── repositories/    # Repository pattern
│   ├── services/        # Business logic
│   ├── utils/           # Helper utilities
│   └── frontend/        # Embedded Gleam/Lustre frontend
│       ├── src/         # Gleam source code
│       │   ├── api/     # API client
│       │   ├── components/ # UI components
│       │   ├── pages/   # Application pages
│       │   └── main.gleam # Entry point
│       ├── public/      # Public assets
│       ├── static/      # Static HTML/CSS
│       ├── build/       # Build artifacts (generated)
│       └── gleam.toml   # Gleam configuration
├── frontend/            # Standalone frontend (optional)
│   └── [same structure as src/frontend]
├── dist/                # Built frontend (generated)
├── scripts/             # Build scripts
│   └── build_frontend.sh
├── tests/               # Test suite
├── examples/            # Examples and templates
├── data/                # Data storage
├── .github/             # CI/CD workflows
├── Makefile            # Build automation
└── pyproject.toml      # Package configuration
```
```

#### 2. Update Frontend Structure Section (Line 64-74)

```markdown
### Frontend Structure

The frontend can be located in two places:
- `src/frontend/` - Embedded within the package (current default)
- `frontend/` - Standalone at root level (for separation of concerns)

```
src/frontend/src/  OR  frontend/src/
├── api/              # API client and models
├── components/       # Reusable UI components
├── pages/           # Application pages
├── router.gleam     # Client-side routing
├── store.gleam      # Global state management
└── main.gleam       # Application entry
```
```

#### 3. Update Building Frontend Section (Line 76-95)

```markdown
### Building Frontend

1. **Development Build**:
   ```bash
   # For embedded frontend
   cd src/frontend
   gleam build --target javascript

   # For standalone frontend
   cd frontend
   gleam build --target javascript
   ```

2. **Production Build**:
   ```bash
   make frontend-build
   # Or directly:
   ./scripts/build_frontend.sh
   ```

   The build script automatically detects the frontend location.
```
