---
paths:
  - "settings.toml"
  - "settings.custom.toml"
  - "plan/**"
  - "examples/**"
---

# Clarinet Project Setup

## Creating a Project

```bash
clarinet init my_project --template research    # Research scaffold with .claude/ docs for agents (Python config, plan/)
clarinet init my_project --template bigliver    # Full liver template (Python config, workflows, pipeline)
clarinet init my_project --template demo        # Simple demo (JSON/TOML config)
clarinet init my_project                        # Bare skeleton
clarinet init --list-templates                  # Show available templates
```

Templates are copied from `examples/` in the Clarinet package. The `research`
template ships a `.claude/CLAUDE.md` and `.claude/rules/*.md` covering
definitions / workflows / slicer / schemas / utils — agents working in a
generated project get section-specific guidance auto-loaded.

For an existing project (or one not made from the `research` template), run
`clarinet agent init` to install framework agent docs into `.claude/rules/clarinet/`
(re-run `clarinet agent update` after upgrading clarinet to refresh them).

## Project Structure

```
my_project/
  settings.toml              # Dev config (SQLite, debug=true)
  settings.custom.toml       # Prod template (env var references)
  .env.example               # Copy to .env for secrets
  .gitignore
  plan/                      # Python config mode dir (= clarinet_plan package root)
    definitions/
      record_types.py        # RecordDef instances
    slicer_hydrators.py      # Slicer context hydrators (config_context_hydrators_file default)
    validators/              # Slicer result validators
    schemas/                 # JSON Schema files for record data
    scripts/                 # 3D Slicer scripts
    workflows/
      pipeline_flow.py       # RecordFlow DSL
```

Every plan file imports as a `clarinet_plan.` submodule off this single root
(no `sys.path`). See `.claude/rules/custom-code-loading.md`.

## Key Settings (`settings.toml`)

```toml
project_name = "My Study"
root_url = "/my_study"                          # Sub-path prefix
api_base_url = "http://127.0.0.1:8111/my_study/api"
extra_roles = ["inspector", "technician"]       # Custom roles beyond admin/user

config_mode = "python"                          # "toml" (default) or "python"
config_tasks_path = "./plan/"                   # Root for config files
config_record_types_file = "definitions/record_types.py"
# config_context_hydrators_file defaults to "slicer_hydrators.py" (plan root)
recordflow_paths = ["./plan/workflows"]         # RecordFlow DSL dirs (inside config_tasks_path)
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
