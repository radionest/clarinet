---
paths:
  - "settings.toml"
  - "settings.custom.toml"
  - "plan/**"
  - "examples/**"
  - "clarinet/scaffold/**"
---

# Clarinet Project Setup

## Creating a Project

```bash
clarinet init my_project      # Full research scaffold (settings, plan/, agent docs)
```

`clarinet init` writes the project skeleton from the payload shipped inside the
clarinet package, then installs the framework agent docs — the same set
`clarinet agent init` delivers, into `.claude/rules/clarinet/`. Your own files are
never overwritten, so it is safe to re-run over a partially set-up directory; the
framework-managed docs under `.claude/rules/clarinet/` are refreshed each time.

`.claude/CLAUDE.md` is written once as a **seed you own**: replace its contents
with your own study description. Unlike the managed rules it is never rewritten,
so `clarinet agent update` leaves your edits intact.

For an existing project, run `clarinet agent init` to install the agent docs on
their own (re-run `clarinet agent update` after upgrading clarinet to refresh
them — that also removes managed docs the new version no longer ships). A full
worked example lives at `examples/demo/` in the clarinet repository — reading
material, not a scaffold.

## Project Structure

```
my_project/
  settings.toml              # Active config (debug=true); secrets via CLARINET_* env vars
  settings.custom.toml       # Production overrides — ships fully commented out
  .env.example               # Copy to .env for secrets
  .gitignore
  .claude/
    CLAUDE.md                # Project overview seed — yours to rewrite
    rules/clarinet/          # Framework agent docs (managed; refreshed by `agent update`)
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
port = 8111                                     # uvicorn binds this; keep it equal to
api_base_url = "http://127.0.0.1:8111/my_study/api"   # the port used here
extra_roles = ["inspector", "technician"]       # Custom roles beyond admin/user

config_mode = "python"                          # "toml" (default) or "python"
config_tasks_path = "./plan/"                   # Root for config files (this is the default)
config_record_types_file = "definitions/record_types.py"
# config_context_hydrators_file defaults to "slicer_hydrators.py" (plan root)
recordflow_paths = ["./plan/workflows"]         # RecordFlow DSL dirs (inside config_tasks_path)
recordflow_enabled = true
pipeline_enabled = true                         # Requires RabbitMQ
frontend_enabled = true
```

`config_tasks_path` defaults to `./plan/` (it was `./tasks/` in earlier
releases). A project that kept the old layout and never set the option must add
`config_tasks_path = "./tasks/"`, or rename the directory to `plan/`. A root
that does not exist aborts startup rather than starting with zero record types
— the error names the unresolved path and, when the value came from the
default, the setting that restores the previous layout.

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
