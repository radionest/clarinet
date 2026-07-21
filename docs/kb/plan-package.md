---
type: Subsystem
title: Project configuration and the clarinet_plan package
description: How a downstream project declares record types and custom Python code, how those files are imported through the single clarinet_plan anchor, and why loading fails fast.
tags: [config, plan, importlib, reconciler, startup]
timestamp: 2026-07-21T19:46:32Z
---

Clarinet is a framework: the interesting declarations live in the *project*, not
in the framework. A project directory (`plan/`, historically `tasks/`) holds
record type definitions, JSON schemas, validators, hydrators, Slicer scripts and
workflow files. This page covers how those reach the running server.

```
my_project/
  settings.toml              # dev config; settings.custom.toml overrides it
  plan/                      # = config_tasks_path = the clarinet_plan root
    definitions/
      record_types.py        # RecordDef instances
      files_catalog.py       # FileDef instances (optional)
    schemas/                 # JSON Schema for record data
    validators/              # record-data validators
    slicer_hydrators.py      # Slicer context hydrators
    scripts/                 # 3D Slicer scripts
    workflows/
      pipeline_flow.py       # RecordFlow DSL
```

Scaffold one with `clarinet init <name> --template research|demo`. For an
existing project, `clarinet agent init` installs the framework's agent docs into
`.claude/rules/clarinet/`.

## Two config modes

Mutually exclusive, set by `settings.config_mode`:

| Mode | Source of truth | API mutations |
|---|---|---|
| `toml` (default) | TOML files, synced bidirectionally with the DB | allowed; edits trigger background TOML export |
| `python` | the Python files | blocked — `require_mutable_config` raises 403 on `POST`/`PATCH`/`DELETE /types` |

Python-mode definitions use the primitives re-exported from `clarinet.flow`:

```python
from clarinet.flow import FileDef, FileRef, RecordDef

seg_mask = FileDef(pattern="seg.nrrd", level="SERIES")
defect_seg = RecordDef(name="defect-seg", files=[FileRef(seg_mask, "input")])
```

**A RecordType name must be a lowercase kebab-case slug** — `validate_slug`
enforces `^[a-z][-a-z0-9]{0,29}$`, so underscores are rejected at construction
and a bad name fails startup, not first use.

### Output paths must discriminate coexisting records

`validate_output_path_uniqueness` (`clarinet/config/path_uniqueness.py`) runs on
every `RecordTypeCreate` construction — Python load, TOML load, and the API
PATCH guard, which re-validates the *merged* effective state so a patch that
stops satisfying its own OUTPUT patterns is rejected at PATCH time rather than
at the next restart. Every non-collection OUTPUT `FileRef` must embed the
placeholder that tells coexisting records of the type apart, or it raises
`RecordConstraintViolationError`:

| Declaration | Pattern must contain |
|---|---|
| anything | `{id}` always satisfies it — a record's id is globally unique |
| `"user"` in `unique_by` | `{user_id}` |
| `"parent"` in `unique_by` + `parent_required=True` | `{parent_id}` |
| an OUTPUT file whose own `level` is coarser than the RecordType's | the RecordType's own level-UID placeholder |
| `unique_by=None` with `max_records` allowing 2+ records | `{id}` — nothing else distinguishes the rows |

`{parent_id}` is a passthrough of `record.parent_record_id`, so it renders
whether or not the parent was loaded. `{origin_type}` names only the parent's
*type*, never a same-type instance, so it never satisfies the parent rule. A
single binding opts out with `FileRef(..., allow_path_collision=True)`.

This is the natural consequence of [`unique_by`](/domain-model.md): if two
records may legitimately coexist, their output files need distinct paths, and
the framework refuses to start a project whose *declaration proves* they would
collide. The guard is only as strong as that declaration: the `parent` rule is
conjoined with `parent_required`, so a type that leaves `parent_required=False`
while still receiving a `parent_record_id` is never forced to carry
`{parent_id}` — two of its records under different parents can still collide on
disk.

`level` and `role` accept plain strings and are coerced to enums. `File` and
`RecordTypeDef` are backward-compatible aliases for `FileDef` and `RecordDef` —
when grepping for usages, search for both spellings. If no `files_catalog.py`
exists, FileDef names are derived from the variable names in `record_types.py`.

## Reconciliation

`reconcile_config()` (`clarinet/utils/bootstrap.py`) dispatches by mode, then
`reconcile_record_types()` does: SELECT all → per config item create / update /
skip if identical → warn or delete orphans → single commit, returning a
`ReconcileResult`.

It compares fields explicitly set in config. Fields with a concrete non-`None`
default additionally **heal toward that default** when left unset, so a DB row
that drifted (a migration backfill, a past model/`server_default` mismatch)
converges on restart. Unset nullable fields keep "don't touch the DB".

## One import root, no sys.path

At startup an in-memory anchor package `clarinet_plan` is installed whose
`__path__` is exactly one directory: `settings.config_tasks_path`. Every plan
file is an ordinary submodule of it.

```python
from clarinet_plan.record_types import master_model
from clarinet_plan.workflows.ct_flow import ...
from .callbacks import notify          # relative imports work too
```

No directory is ever put on `sys.path`. Consequences: the "wrong directory on
sys.path" bug class is inexpressible, stdlib shadowing (`plan/logging.py`)
cannot happen, each file has exactly one dotted name, and cross-flow imports
work in either direction because the native module cache makes execution
exactly-once. Sibling-by-stem imports (`from record_types import ...`) do not
exist; a leftover one raises `ConfigLoadError` naming the correct spelling.

**Never open-code an importlib dance and never touch `sys.path` in a loader** —
that is how the hydrators-silently-missing incident happened.

Anchor machinery (`clarinet/config/plan_package.py`):

| Function | Role |
|---|---|
| `activate_plan_package(root)` | startup / worker entry: purge any old anchor, install a fresh one, invalidate caches |
| `ensure_plan_root(folder)` | first line of every loader; a folder outside the root raises `ConfigLoadError` (this is what forces `recordflow_paths` to live inside `config_tasks_path`) |
| `module_name_for(path)` | path → canonical dotted name; owns validation of identifiers and `X.py`/`X/` collisions |
| `import_plan_module(dotted)` | import + error classification into `ConfigLoadError` |
| `deactivate_plan_package()` | test sanitation |

These mutate global import state — call them from startup or test setup only,
never from a request handler or background task. Running a plan file directly
(`python plan/validators.py`) is unsupported: that process has no anchor.

## Custom code registries

`CustomCodeRegistry[T]` (`clarinet/config/custom_registry.py`) is the single
owner of the three decorator families; each domain module is a thin shim over it.

| Module | Setting | Default file |
|---|---|---|
| `services/schema_hydration.py` | `config_schema_hydrators_file` | `schema_hydrators.py` |
| `services/slicer/context_hydration.py` | `config_context_hydrators_file` | `slicer_hydrators.py` |
| `services/record_data_validation.py` | `config_validators_file` | `validators.py` |

RecordFlow's `RECORD_REGISTRY` / `ENTITY_REGISTRY` / `FILE_REGISTRY` are *not*
instances — they have a different lifecycle, cleared once per load cycle.

Load order at lifespan: `activate_plan_package()` →
`_ensure_record_types_imported()` → clear the three registries and re-register
built-ins → load the plan registries → **only then** `reconcile_config()`.
`_ensure_record_types_imported()` must run before anything that transitively
imports `record_types`, otherwise module-level reads of `FileDef.name` see `""`.
Workers follow the same shape in `run_worker`.

## Fail-fast contract

A broken plan file must crash startup, never degrade silently.

- Loaders raise `ConfigLoadError` (a `ConfigurationError` subclass; the original
  error stays on `__cause__`).
- Multi-file loaders attempt every file, then raise one
  `ConfigLoadError.aggregate(...)` with the individual errors on `.failures`.
- A file that imports cleanly but registers nothing logs a WARNING.
- `reconcile_config` validates `data_validators`, `slicer_context_hydrators` and
  `x-options.source` names against the populated registries and raises
  `ConfigurationError` on unknown names. The guard covers config-defined record
  types only; types mutated through the API in TOML mode and orphaned DB rows are
  caught by runtime logs alone.
- `app.py` converts `ConfigLoadError` → `StartupError`; `run_worker` converts it
  → `SystemExit(1)`.

Full loading contract, including the test-sanitation fixtures:
[`.claude/rules/custom-code-loading.md`](../../.claude/rules/custom-code-loading.md).
