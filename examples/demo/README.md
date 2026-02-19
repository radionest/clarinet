# Clarinet Demo Project

A demo project showcasing the core functionality of the Clarinet framework:
patient/study/series management, record types with JSON schemas, record workflow,
and RecordFlow automation.

## Structure

```
examples/demo/
├── settings.toml              # Server configuration (SQLite, debug mode)
├── record_flow.py             # RecordFlow workflow definitions
├── tasks/                     # Record type definitions
│   ├── doctor_review.json     # Doctor review record type
│   ├── doctor_review.schema.json
│   ├── ai_analysis.json       # AI analysis record type
│   ├── ai_analysis.schema.json
│   ├── expert_check.json      # Expert check record type
│   └── expert_check.schema.json
├── scripts/
│   ├── generate_test_data.py  # Create test patients, studies, series, records
│   └── test_functionality.py  # Comprehensive test suite
├── data/                      # Storage directory (created automatically)
└── README.md
```

## RecordFlow Workflows

Three workflows are defined in `record_flow.py`:

1. **doctor_review -> ai_analysis**: When a doctor review finishes, always create an AI analysis
2. **doctor_review -> expert_check**: When doctor confidence < 70, create an expert check
3. **ai_analysis -> expert_check**: When AI and doctor diagnoses disagree, create an expert check

## Quick Start

### 1. Start the server

From the `examples/demo/` directory:

```bash
cd examples/demo
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

This will:
- Create a SQLite database (`clarinet_demo.db`)
- Load record types from `tasks/`
- Load RecordFlow definitions from `record_flow.py`
- Create an admin user (admin / admin123)

### 2. Generate test data

In a separate terminal:

```bash
cd examples/demo
python scripts/generate_test_data.py
```

Creates 5 patients with Russian names, studies, series, and doctor_review records.

### 3. Run tests

```bash
cd examples/demo
python scripts/test_functionality.py
```

Tests all core functionality:
- Authentication (login, session validation, logout)
- Patient CRUD and anonymization
- Study CRUD and anonymized UIDs
- Series CRUD, search, and random selection
- Record type management
- Record CRUD, assignment, status updates, and data submission
- Advanced record search with multiple filters
- Study hierarchy retrieval
- Batch operations (studies, series, patient with studies)
- RecordFlow automation (auto-creation of records on status changes)
- Structured data submission matching JSON schemas

## Configuration

The `settings.toml` uses SQLite for simplicity. Key settings:

| Setting | Value | Description |
|---------|-------|-------------|
| `database_driver` | sqlite | SQLite for local development |
| `database_name` | clarinet_demo | Database file name |
| `admin_password` | admin123 | Default admin password |
| `recordflow_enabled` | true | Enable workflow automation |
| `recordflow_paths` | ["."] | Look for `*_flow.py` in current dir |
| `frontend_enabled` | false | API-only mode |

## Record Types

### doctor_review
- **Level**: SERIES
- **Role**: doctor
- **Schema**: diagnosis (string), confidence (0-100), requires_expert (bool), notes (string)

### ai_analysis
- **Level**: SERIES
- **Role**: auto
- **Schema**: ai_diagnosis (string), ai_confidence (0.0-1.0), findings (string)

### expert_check
- **Level**: SERIES
- **Role**: expert
- **Schema**: final_diagnosis (string), agrees_with_doctor (bool), agrees_with_ai (bool), expert_notes (string)
