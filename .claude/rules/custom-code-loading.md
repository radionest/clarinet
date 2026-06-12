---
paths:
  - "clarinet/config/plan_package.py"
  - "clarinet/config/python_loader.py"
  - "clarinet/config/custom_registry.py"
  - "clarinet/services/schema_hydration.py"
  - "clarinet/services/slicer/context_hydration.py"
  - "clarinet/services/record_data_validation.py"
  - "clarinet/services/recordflow/flow_loader.py"
  - "clarinet/services/pipeline/worker.py"
---

# Custom Code Loading from `plan/`

How downstream-project Python files (`plan/` a.k.a. `settings.config_tasks_path`)
are imported at startup. Single owner: the anchor package in
`clarinet/config/plan_package.py` + `CustomCodeRegistry` in
`clarinet/config/custom_registry.py`. **Never open-code an importlib dance and
never touch `sys.path` in a loader** — that's how the hydrators-silently-missing
incident happened.

## `plan/` is the single `clarinet_plan` root

At startup an in-memory anchor package `clarinet_plan` is installed whose
`__path__` is the ONE root — `settings.config_tasks_path`. Every plan file is an
ordinary submodule imported only from that root:

```python
from clarinet_plan.record_types import master_model
from clarinet_plan.utils.study_type import classify
from clarinet_plan.workflows.ct_flow import ...
from .callbacks import notify          # relative imports also work
```

No directory is ever placed on `sys.path`. Consequences: the "wrong directory on
sys.path" bug class is inexpressible; stdlib shadowing (`plan/logging.py`) cannot
happen; a file has exactly one dotted name; cross-flow imports work in both
directions (native module cache ⇒ exactly-once execution, no sorted-order limit).

Sibling-by-stem imports (`from record_types import ...`, `from tasks import ...`)
do **not** exist — a leftover one raises `ConfigLoadError` with a migration hint
naming the `clarinet_plan.`-prefixed spelling.

## Anchor machinery (`plan_package.py`)

- `activate_plan_package(root)` — startup / worker entry. Guards against a real
  installed `clarinet_plan` distribution → purges any old anchor + submodules →
  installs a fresh anchor rooted at `root` → `invalidate_caches()`. The anchor is
  always recreated (a stale anchor holds submodule attributes).
- `ensure_plan_root(folder)` — first line of every loader. No anchor → activate;
  `folder` == root or a descendant → no-op (+ `invalidate_caches()`); `folder`
  outside root → `ConfigLoadError` (this enforces *recordflow_paths inside
  config_tasks_path*).
- `deactivate_plan_package()` — purge anchor + submodules (test sanitation).
- `module_name_for(path)` — path → canonical `clarinet_plan.*` dotted name.
  **Owns validation**: every path segment must be a valid non-keyword identifier
  (errors name the *file/dir* to rename); rejects a `X.py` + `X/` collision and
  paths outside the root.
- `import_plan_module(dotted, *, path_hint=)` — `import_module` + error
  classification → `ConfigLoadError` (plan-module-missing vs transitive
  third-party miss vs module/dir collision), with the migration hint.

Threading invariant: activate/ensure/deactivate mutate global import state — call
only from startup or test setup, never from request handlers / background tasks.
Running a plan file directly (`python plan/validators.py`) is unsupported (no
anchor in that process).

## `CustomCodeRegistry[T]` (`custom_registry.py`)

One instance per decorator family; the decorator and `load_*` function in each
domain module are thin shims over it:

| Module | Instance | File setting | Default file → `sys.modules` name |
|---|---|---|---|
| `services/schema_hydration.py` | `_HYDRATOR_REGISTRY` | `config_schema_hydrators_file` | `schema_hydrators.py` → `clarinet_plan.schema_hydrators` |
| `services/slicer/context_hydration.py` | `_SLICER_HYDRATOR_REGISTRY` | `config_context_hydrators_file` | `slicer_hydrators.py` → `clarinet_plan.slicer_hydrators` |
| `services/record_data_validation.py` | `_VALIDATOR_REGISTRY` | `config_validators_file` | `validators.py` → `clarinet_plan.validators` |

(`sys.modules` name is derived by `module_name_for` from the file's path under
the root — a file in a subdir like `definitions/record_types.py` becomes
`clarinet_plan.definitions.record_types`.)

API: `register(name, value, *, replace=True)` (hydrators replace; validators
pre-check duplicates in the decorator and use `replace=False`), `load_from(folder)`,
`get`, `names()`, `clear`, `snapshot()`/`restore()` (test fixtures). `load_from`
suppresses the "registered nothing" warning **only** on a cache hit against a
*non-empty* registry; a cache hit against an *empty* registry still warns (the
#352 silent-degradation shape). RecordFlow registries (`RECORD_REGISTRY` etc.)
are NOT instances — cleared once per load cycle, different lifecycle.

The `study_series` schema hydrator is the only built-in living in a registry.
`schema_hydration._register_builtin_hydrators()` registers it at import time and
again from the lifespan after `clear()`.

## Lifecycle

| Who | What |
|---|---|
| lifespan (`app.py`) | `activate_plan_package(config_tasks_path)` → `_ensure_record_types_imported()` → clear 3 registries + `_register_builtin_hydrators()` → `_load_plan_registries()`, all before `reconcile_config()` |
| `run_worker` (`worker.py`) | `activate_plan_package(config_tasks_path)` → `load_task_modules()` (which calls `_ensure_record_types_imported()` first) |
| each loader | `ensure_plan_root(its folder)` first (self-activation for direct test calls) |
| tests | autouse `_plan_package_sanitation` fixture → teardown `deactivate_plan_package()` |

`_ensure_record_types_imported()` (in `python_loader.py`) imports the catalog +
`record_types` modules and assigns FileDef names — must run **before** any file
that transitively imports `record_types` (e.g. `validators.py`), else module-level
reads of `FileDef.name` see `""`.

## Fail-fast contract

A broken plan file must crash startup, never degrade silently:

- loaders raise `ConfigLoadError` (`clarinet/exceptions/domain.py`, subclass of
  `ConfigurationError`; original error in `__cause__`)
- multi-file loaders (`load_and_register_flows`, `worker.load_task_modules`)
  attempt every file/path, then raise one `ConfigLoadError.aggregate(...)`
  (individual errors kept on `.failures`)
- a file that imports cleanly but registers nothing logs a WARNING
- `reconcile_config` (`bootstrap.py`) validates RecordType references against the
  populated registries via `_validate_registry_refs` — `data_validators` and
  `slicer_context_hydrators` raise `ConfigurationError` on unknown names; loaders
  therefore run BEFORE reconcile in the lifespan. Boundary: the guard covers
  config-defined RecordTypes only — types mutated via the API (TOML mode) and
  orphaned DB rows are caught only by the runtime ERROR log
- `app.py` lifespan converts `ConfigLoadError` → `StartupError(component="Config",
  disableable=False)`; `worker.run_worker` converts it → `SystemExit(1)`

## Test sanitation

- The autouse `_plan_package_sanitation` conftest fixture calls
  `deactivate_plan_package()` after every test — replaces all ad-hoc
  `monkeypatch.delitem(sys.modules, "...")` cleanups.
- snapshot/restore registries: `saved = REG.snapshot()` … `REG.restore(saved)`
  (validator version is the `isolated_validator_registry` conftest fixture).
- Loaders take a `folder`; tests that load flow files in a tmp dir monkeypatch
  **both** `config_tasks_path=tmp_path` and `recordflow_paths=[tmp_path]` (or a
  subdir), so the flow path lives inside the anchor root.
- Re-reading a changed plan file means re-importing it; production does this by
  re-activating the anchor at each app start. A test that loads, edits a file,
  then reloads must `deactivate_plan_package()` between the two loads.
