# Clarinet Research Project

This is a clinical/radiology research project template built on the [clarinet](https://github.com/...) framework. This document is always loaded into the agent's context and gives a project overview; details for each section are in `.claude/rules/*.md` (auto-loaded when editing files in the corresponding folders).

> Replace this file's contents with your own study (name, description, specifics). The structure and API references below stay accurate.

## What this project is

`<Study name>` — `<short medical description>`. Goal: `<hypothesis / diagnostic endpoint>`.

Source of truth for project metadata — `settings.toml` (name, description, base URL, custom roles).

## Architecture in 30 seconds

Clarinet models a research pipeline through four entities:

- **DICOM hierarchy** Patient → Study → Series — the framework imports studies from PACS and anonymizes them itself.
- **RecordType** — a typed workflow step, bound to a hierarchy level (PATIENT/STUDY/SERIES). Describes: which doctor role sees the record, which files are created as input/output, what the data form looks like, which Slicer script to open.
- **RecordFlow DSL** — declarative orchestration of transitions between records: "when a study arrives → create a first-check", "when a segmentation is finished → run projection and comparison", "when the master model changes → invalidate dependent records".
- **Pipeline tasks** — async functions running in workers (RabbitMQ + TaskIQ): heavy DICOM work, conversion to NIfTI, image processing, GPU inference.

Files are bound to a DICOM hierarchy level and resolved via patterns with placeholders (`{study_uid}`, `{user_id}`, ...).

## Directory structure

```
plan/
├── definitions/        # FileDef + RecordDef — the only place types are declared
├── workflows/          # @pipeline_task functions + RecordFlow DSL
├── slicer_hydrators.py # Async functions injecting variables into Slicer scripts
├── scripts/            # Bare Python scripts for 3D Slicer (interactive work)
├── validators/         # Bare Python validators running after a Slicer task
├── schemas/            # JSON Schema for record.data (validation + UI forms)
└── utils/              # Project-specific helper modules
```

All files under `plan/` are imported as submodules of the `clarinet_plan` package (single root — `config_tasks_path`); `sys.path` is not used.

Each subfolder has a corresponding rule file in `.claude/rules/` with detailed conventions.

## clarinet API — what to import from where

| What you need | Where from |
|---|---|
| `FileDef`, `FileRef`, `RecordDef` | `clarinet.flow` |
| `pipeline_task`, `PipelineMessage`, `TaskContext`, `SyncTaskContext` | `clarinet.services.pipeline` |
| `record`, `series`, `study`, `patient`, `file`, `Field` | `clarinet.services.recordflow` |
| `slicer_context_hydrator`, `SlicerHydrationContext` | `clarinet.services.slicer.context_hydration` |
| `SlicerHelper` | `clarinet.services.slicer.helper` |
| `ClarinetClient` | `clarinet.client` |
| `RecordCreate`, `RecordRead`, `RecordStatus` | `clarinet.models` (`RecordStatus` — from `clarinet.models.base`) |
| `RecordSearchCriteria` | `clarinet.repositories.record_repository` |
| `Segmentation` (numpy/nrrd wrapper) | `clarinet.services.image` |
| `logger` | `clarinet.utils.logger` (never import loguru directly) |
| `settings` | `clarinet.settings` |

## Key `settings.toml` settings

```toml
config_mode = "python"                                 # Python config mode
config_tasks_path = "./plan/"                          # root folder (= root of the clarinet_plan package)
config_record_types_file = "definitions/record_types.py"
# config_context_hydrators_file defaults to "slicer_hydrators.py" (root of plan/)
recordflow_paths = ["./plan/workflows"]                # where to look for *_flow.py (inside config_tasks_path)

recordflow_enabled = true                              # enable the RecordFlow engine
pipeline_enabled = true                                # enable the TaskIQ broker (requires RabbitMQ)
frontend_enabled = true                                # serve the frontend SPA

extra_roles = ["doctor_CT", "surgeon"]                 # custom roles on top of admin/user
```

All paths are given **relative to `config_tasks_path`** (i.e. `plan/`). Any role mentioned in `RecordDef.role` must be in `extra_roles` (or be one of the standard ones: `admin`, `user`, `doctor`, `auto`, `expert`).

## Main commands

```bash
cp .env.example .env                          # fill in secrets
uv run clarinet db init                       # initialize the DB + create the admin
uv run clarinet run                           # start the API + frontend
uv run clarinet worker                        # pipeline worker (all queues)
uv run clarinet worker --queues clarinet.dicom  # specific queues
uv run clarinet ohif install                  # install OHIF Viewer (served at /ohif)
uv run clarinet rabbitmq status               # queue status
```

Full list — `uv run clarinet --help` and `make help` in the framework repository.

## Naming conventions

- **`RecordDef.name`** — kebab-case, 5-30 characters: `"first-check"`, `"segment-ct-single"`. This is the identifier used in the DSL and the URL.
- **Python variables** — snake_case: `first_check = RecordDef(name="first-check", ...)`. The variable name can differ from `name`.
- **scripts/validators files** — snake_case, validators suffixed with `_validator`: `segment.py`, `segment_validator.py`.
- **Schema files** — `{record-type-name}.schema.json`: `first-check.schema.json` (kebab-case matching `RecordDef.name`).
- **Hydrator injection key** — snake_case: `@slicer_context_hydrator("best_series_from_first_check")`.

## Cross-cutting rules

- **Pipeline task idempotency**. Every task must check `ctx.files.exists(output_file_def)` and return early if the result already exists. Reason: worker retries, manual restarts, and cascade invalidation can all trigger the task again.
- **Logging** — only `from clarinet.utils.logger import logger` with f-strings. Never `print()` and never `import loguru`.
- **Slicer scripts are bare Python** running inside the 3D Slicer environment. Globals (`slicer`, `working_folder`, `output_file`, ...) are injected by the framework; every script must start with a docstring listing the context vars.
- **Async vs sync in pipeline tasks**. Async — for I/O, HTTP, the DB (ClarinetClient). Sync (`SyncTaskContext`) — for CPU-bound work (skimage, SimpleITK, vtk); such functions run automatically in a thread.
- **`asyncio.gather` is forbidden on a shared `ClarinetClient`/AsyncSession** — concurrent requests block each other on the same connection. Use sequential `await` or create separate clients.

## Where to look for details

This project's rules (auto-loaded via the `paths` frontmatter):

- `.claude/rules/definitions.md` — `FileDef`, `RecordDef`, path patterns, links between sections
- `.claude/rules/workflows.md` — `@pipeline_task`, `TaskContext`, RecordFlow DSL
- `.claude/rules/slicer.md` — hydrators + Slicer scripts + validators (all linked via injection vars)
- `.claude/rules/schemas.md` — JSON Schema for record.data, conditional schemas, UI hints, shared `$defs` across files (`$ref`)
- `.claude/rules/utils.md` — helper modules, the `.seg.nrrd` format

Framework rules (full reference docs, living in the clarinet repository itself — useful as a reference):

- `<clarinet>/clarinet/.claude/rules/recordflow-dsl.md` — full DSL API with pattern matching
- `<clarinet>/clarinet/.claude/rules/slicer-helper-api.md` — all `SlicerHelper` methods + VTK pitfalls
- `<clarinet>/clarinet/.claude/rules/pipeline-ops.md` — pipeline settings, testing, queues
- `<clarinet>/clarinet/.claude/rules/file-registry.md` — file pattern-resolution details
- `<clarinet>/clarinet/.claude/rules/project-setup.md` — template overview, `clarinet init` options

Production example: the `clarinet_nir_liver` repository (if available) — the most complete real-world use of this template.
