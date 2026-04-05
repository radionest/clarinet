---
paths:
  - "settings.toml"
  - "settings.local.toml"
  - "settings.custom.toml"
  - "plan/**"
  - "examples/**"
---

# Clarinet Project Setup

## Creating a Project

```bash
clarinet init my_project --template bigliver   # Full template (Python config, workflows, pipeline)
clarinet init my_project --template demo        # Simple demo (JSON/TOML config)
clarinet init my_project                        # Bare skeleton
clarinet init --list-templates                  # Show available templates
```

Templates are copied from `examples/` in the Clarinet package.

## Project Structure

```
my_project/
  settings.toml              # Dev config (SQLite, debug=true)
  settings.local.toml        # Prod template (env var references)
  .env.example               # Copy to .env for secrets
  .gitignore
  plan/                      # Python config mode directory
    definitions/
      record_types.py        # RecordDef instances
    hydrators/
      context_hydrators.py   # Slicer context hydrators
    validators/              # Slicer result validators
    schemas/                 # JSON Schema files for record data
    scripts/                 # 3D Slicer scripts
    workflows/
      pipeline_flow.py       # RecordFlow DSL
```

## Key Settings (`settings.toml`)

```toml
project_name = "My Study"
root_url = "/my_study"                          # Sub-path prefix
api_base_url = "http://127.0.0.1:8111/my_study/api"
extra_roles = ["doctor", "surgeon"]             # Custom roles beyond admin/user

config_mode = "python"                          # "toml" (default) or "python"
config_tasks_path = "./plan/"                   # Root for config files
config_record_types_file = "definitions/record_types.py"
config_context_hydrators_file = "hydrators/context_hydrators.py"
recordflow_paths = ["./plan/workflows"]         # RecordFlow DSL dirs
recordflow_enabled = true
pipeline_enabled = true                         # Requires RabbitMQ
frontend_enabled = true
```

Config modes (TOML vs Python): see `clarinet/config/CLAUDE.md`.

## Running

```bash
clarinet db init              # Create schema + admin user
clarinet run                  # API + frontend
clarinet worker               # Pipeline workers (all queues)
clarinet worker --queues gpu  # Specific queue
```

## Reference Project

`~/Projects/clarinet_nir_liver/` — production Python-mode project with PostgreSQL, RabbitMQ, DICOM, RecordFlow workflows.
