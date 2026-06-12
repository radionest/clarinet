---
paths:
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
are imported at startup. Single owner: primitives in
`clarinet/config/python_loader.py` + `CustomCodeRegistry` in
`clarinet/config/custom_registry.py`. **Never open-code the
importlib/sys.path dance in a loader** — that's how the
hydrators-silently-missing incident happened.

## `plan/` is a source root

Plan files may use BOTH import styles, regardless of which subdirectory the
file lives in (`config_*_file` settings may point to `hydrators/foo.py`):

- package imports from the config root: `from utils.study_type import ...`
- sibling imports: `from record_types import master_model`

Therefore every loader puts **two** directories on `sys.path` for the
duration of the import: the config root AND the file's parent. Use
`config_sys_path(root, file.parent)` — args are low-priority-first (each is
inserted at position 0, so the last argument wins lookup). Entries already
on `sys.path` are skipped and left in place; only inserted ones are removed.

The priority order is **per call**, not global: nested `config_sys_path`
contexts stack on top of outer ones. E.g. the flow loaders put the flow dir
on the path first, then `preload_record_types` inserts the config root and
the `record_types.py` parent *above* it — on a name collision between the
flow dir and the config root, the config root wins (matches the pre-refactor
behavior).

## Primitives (`python_loader.py`)

- `config_sys_path(*dirs)` — context manager described above.
- `load_module_from_file(name, path, *, keep_in_sys=False)` — importlib
  spec → module → `sys.modules[name]` → exec. **Raises `ConfigLoadError`**
  on any failure and pops the half-initialized module from `sys.modules`
  (prevents cross-test contamination). `keep_in_sys=True` when sibling files
  must import the module afterwards: `record_types.py` while flow files
  load, AND **the flow files themselves** — flow files may import each
  other, and without the cache entry Python re-executes the file from disk,
  double-registering its flows / `@pipeline_task`s (task-name collision
  kills the worker). Limitation (same as pre-refactor): flow files load in
  sorted order, so a flow file may only import siblings that sort **before**
  it — importing a later-sorted flow file re-executes it transitively and
  still collides.

## `CustomCodeRegistry[T]` (`custom_registry.py`)

One instance per decorator family; the decorator and `load_*` function in
each domain module are thin shims over it:

| Module | Instance | File setting | `sys.modules` name |
|---|---|---|---|
| `services/schema_hydration.py` | `_HYDRATOR_REGISTRY` | `config_schema_hydrators_file` | `clarinet_custom_hydrators` |
| `services/slicer/context_hydration.py` | `_SLICER_HYDRATOR_REGISTRY` | `config_context_hydrators_file` | `clarinet_custom_slicer_hydrators` |
| `services/record_data_validation.py` | `_VALIDATOR_REGISTRY` | `config_validators_file` | `clarinet_custom_validators` |

API: `register(name, value, *, replace=True)` (hydrators replace; validators
pre-check duplicates in the decorator and use `replace=False`),
`load_from(folder)`, `get`, `names()`, `clear`, `snapshot()`/`restore()`
(test fixtures). RecordFlow registries (`RECORD_REGISTRY` etc.) are NOT
instances — they're cleared on every load, different lifecycle.

## Fail-fast contract

A broken plan file must crash startup, never degrade silently:

- loaders raise `ConfigLoadError` (`clarinet/exceptions/domain.py`,
  subclass of `ConfigurationError`; original error in `__cause__`)
- multi-file loaders (`load_and_register_flows`, `worker.load_task_modules`)
  attempt every file/path, then raise one `ConfigLoadError.aggregate(...)`
  (individual errors kept on `.failures`)
- a file that imports cleanly but registers nothing logs a WARNING
  (missing decorator — same silent-degradation class)
- `app.py` lifespan converts `ConfigLoadError` → `StartupError(component="Config",
  disableable=False)` — no bogus "disable the component" hint
- `worker.run_worker` converts it → `SystemExit(1)`

## Test sanitation

- snapshot/restore registries: `saved = REG.snapshot()` … `REG.restore(saved)`
  (validator version is the `isolated_validator_registry` conftest fixture)
- pop plan-file modules loaded under their stem or package name:
  `monkeypatch.delitem(sys.modules, "utils", raising=False)`
- assert `sys.path` is restored after loader calls
