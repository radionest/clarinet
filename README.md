# Clarinet

A comprehensive framework for conducting clinical-radiological studies, built on FastAPI, SQLModel, and asynchronous architecture.

## Overview

Clarinet is a powerful framework designed to streamline the development of clinical-radiological research applications. It provides a robust foundation with built-in support for DICOM processing, image analysis, and integration with medical imaging tools like 3D Slicer.

## Features

- **Async-First Architecture**: Built entirely on async/await for optimal performance
- **FastAPI-based API**: Modern, fast, and fully documented REST API
- **SQLModel ORM**: Type-safe database operations with async support
- **Repository Pattern**: Clean data access layer with repository pattern implementation
- **DICOM Support**: Built-in DICOM processing and management
- **3D Slicer Integration**: Seamless integration with 3D Slicer for advanced image processing
- **Authentication & Authorization**: Secure session-based authentication with FastAPI-users using httpOnly cookies
- **Modular Design**: Clean separation of concerns with services, repositories, and models
- **Modern Frontend**: Gleam/Lustre-based SPA with type-safe functional programming
- **MVU Architecture**: Predictable state management with Model-View-Update pattern

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
│       │   ├── api/       # HTTP client with cookie support
│       │   ├── components/# UI components
│       │   ├── pages/     # Application pages
│       │   └── utils/     # Utilities (DOM, routing)
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

## Installation

### Prerequisites

- Python 3.11+
- PostgreSQL or SQLite (for development)
- 3D Slicer (optional, for advanced image processing)
- Gleam 1.5+ (for frontend development)

### Install from PyPI (when published)

```bash
pip install clarinet
```

### Install from source

```bash
git clone https://github.com/yourusername/clarinet.git
cd clarinet
pip install -e .
```

## Quick Start

### 1. Configure the Application

Copy the example configuration:

```bash
cp settings.toml.example settings.toml
```

Edit `settings.toml` with your database and application settings.

### 2. Initialize the Database

If using Alembic in your project:

```bash
alembic init alembic
alembic upgrade head
```

### 3. Build the Frontend (Optional)

The frontend needs to be built before running:

```bash
# Install Gleam (if not already installed)
curl -fsSL https://gleam.run/install.sh | sh

# Build the frontend
make frontend-build

# Or directly:
./scripts/build_frontend.sh
```

### 4. Run the Application

```bash
# Development mode with frontend
clarinet run --with-frontend

# Backend only (API mode)
uvicorn src.api.app:app --reload

# Or using the CLI
python -m src
```

The application will be available at `http://localhost:8000` with:
- Frontend: `/` (if built)
- API documentation: `/docs`
- Admin interface: `/admin` (if configured)

## Development

### Frontend Development

**Note**: The frontend is currently located at `src/frontend/`.

#### Key Dependencies

The frontend uses native Gleam libraries for better type safety:

- **lustre** (~5.3): Web framework with MVU architecture
- **gleam_fetch** (~1.3): HTTP client with automatic cookie support
- **plinth** (~0.7): Browser API bindings for DOM manipulation
- **modem** (~2.1): Client-side routing
- **formosh**: Dynamic form generation from JSON Schema
- **gleam_json** (~3.0): JSON encoding/decoding
- **gleam_http** (~4.2): HTTP types and utilities

```bash
# For embedded frontend (current structure)
cd src/frontend
gleam deps download  # Installs all Gleam dependencies
gleam build --target javascript

# For standalone frontend (after separation)
cd frontend
gleam deps download
gleam build --target javascript

# Production build (automatically detects location)
make frontend-build

# Clean build artifacts
make frontend-clean

# Run frontend tests
make frontend-test
```

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

## Usage Examples

### Creating a Study

```python
from clarinet.repositories.study_repository import StudyRepository
from clarinet.models.study import Study
from sqlalchemy.ext.asyncio import AsyncSession

async def create_study(session: AsyncSession):
    repo = StudyRepository(session)
    study = Study(
        name="Clinical Trial 2024",
        description="Phase II clinical trial"
    )
    return await repo.add(study)
```

### Processing DICOM Files

```python
from clarinet.services.dicom import DicomProcessor

async def process_dicom(file_path: str):
    processor = DicomProcessor()
    metadata = await processor.extract_metadata(file_path)
    return metadata
```

## Development

### Setting up Development Environment

```bash
# Install development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Run tests
pytest

# Format code
ruff format src/ tests/

# Lint code
ruff check src/ tests/ --fix

# Type checking
mypy src/
```

### Repository Pattern

Clarinet uses the repository pattern for data access:

```python
from clarinet.repositories.user_repository import UserRepository
from sqlalchemy.ext.asyncio import AsyncSession

async def get_user(user_id: int, session: AsyncSession):
    repo = UserRepository(session)
    return await repo.get(user_id)
```

### Service Layer

Business logic is encapsulated in service classes:

```python
from clarinet.services.study_service import StudyService

async def analyze_study(study_id: int):
    service = StudyService()
    results = await service.analyze(study_id)
    return results
```

## API Documentation

Once the application is running, visit:
- Interactive API docs: `http://localhost:8000/docs`
- Alternative API docs: `http://localhost:8000/redoc`

## Configuration

Clarinet uses a hierarchical configuration system:

1. Default settings in `src/settings.py`
2. TOML configuration files (`settings.toml`)
3. Environment variables (prefixed with `CLARINET_`)

### Key Configuration Options

- `database_url`: Database connection string
- `storage_path`: Path for file storage
- `slicer_path`: Path to 3D Slicer executable
- `secret_key`: Secret key for session encryption

## Database Migrations

Clarinet provides utilities to simplify Alembic integration in your projects:

```python
from clarinet.utils.migrations import initialize_database

# Initialize database with migrations
await initialize_database()
```

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src tests/

# Run specific test file
pytest tests/integration/test_api.py
```

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

## Documentation

- [Code Style Guide](CLAUDE.md) - Detailed development guidelines
- [API Reference](docs/api.md) - Complete API documentation
- [Examples](examples/) - Sample implementations

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

For issues, questions, or contributions, please visit our [GitHub repository](https://github.com/yourusername/clarinet).