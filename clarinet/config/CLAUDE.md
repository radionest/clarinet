# Config Package Guide

Two mutually exclusive config modes per project:
- **TOML mode** (default): Bidirectional sync — TOML files <-> DB via API
- **Python mode**: Python files = single source of truth, API mutations disabled

## Settings

```python
config_mode: Literal["toml", "python"] = "toml"
config_tasks_path: str = "./tasks/"
config_delete_orphans: bool = False

# Config file locations (relative to config_tasks_path)
config_record_types_file: str = "record_types.py"
config_files_catalog_file: str = "files_catalog.py"
config_context_hydrators_file: str = "slicer_hydrators.py"  # slicer context hydrators
config_schema_hydrators_file: str = "schema_hydrators.py"   # schema hydrators
config_validators_file: str = "validators.py"              # record-data validators
```

All paths are relative to `config_tasks_path` and are imported as
`clarinet_plan.`-prefixed submodules off that single root — never via `sys.path`.
See `.claude/rules/custom-code-loading.md`.

## Primitives (`primitives.py`)

User-facing Pydantic BaseModels for Python config files:

```python
from clarinet.flow import FileDef, FileRef, RecordDef

seg_mask = FileDef(pattern="seg.nrrd", level="SERIES", description="Segmentation mask")

lesion_seg = RecordDef(
    name="lesion_seg",
    description="Lesion segmentation",
    files=[FileRef(seg_mask, "input")],
)
```

- `FileDef`: pattern, multiple, level (str or `DicomQueryLevel`, **required**), description, name (auto-derived)
- `FileRef(file, role, required)`: binds FileDef to RecordDef with role; role accepts str (`"input"`) or `FileRole` enum
- `RecordDef`: full RecordType definition; `role` is user-friendly alias for `role_name`; `level` accepts str or enum; `unique_per_user`: one record per user (default True); `editable`: non-superusers may update a finished record (default True); `shared_editing`: any role-holder may edit any record of this type — each edit reassigns ownership to the editor (requires `unique_per_user=False`, default False)

**String coercion:** `level` and `role` fields accept plain strings (`"SERIES"`, `"input"`) — validators coerce to enums automatically.

**Backward compat aliases:** `File = FileDef`, `RecordTypeDef = RecordDef` (old names still work).
When searching for usages of `FileDef` or `RecordDef`, always search for both the canonical
name and the alias (e.g., `(FileDef|File)\(`) — tests and user configs may use either.

**Single-file mode:** If no `files_catalog.py` exists, FileDef names are auto-derived from `record_types.py`.

## Reconciler (`reconciler.py`)

```python
async def reconcile_record_types(
    config_items: list[RecordTypeCreate],
    session: AsyncSession,
    *,
    delete_orphans: bool = False,
) -> ReconcileResult
```

Algorithm: SELECT all → for each config: CREATE if new, UPDATE if changed, skip if identical → orphans warned/deleted → single commit.

`ReconcileResult`: created, updated, unchanged, orphaned, errors.

Only compares fields explicitly set in config (via `model_fields_set` — missing = unchanged).

## Python Loader (`python_loader.py`)

```python
async def load_python_config(folder: Path) -> list[RecordTypeCreate]
```

Expected folder structure (default):
```
tasks/
    files_catalog.py   # FileDef instances (optional)
    record_types.py    # RecordDef instances
```

Or single-file mode:
```
tasks/
    record_types.py    # Both FileDef and RecordDef instances
```

Custom file locations via settings (paths relative to `config_tasks_path`):
```
tasks/
    definitions/
        files_catalog.py    # config_files_catalog_file = "definitions/files_catalog.py"
        record_types.py     # config_record_types_file = "definitions/record_types.py"
```

- Imports through the `clarinet_plan` anchor (`plan_package.py`) — catalog +
  `record_types` via `_ensure_record_types_imported(folder)`, no `sys.path`
  (full contract: `.claude/rules/custom-code-loading.md`)
- Fail-fast: a broken `record_types.py`/`files_catalog.py` raises `ConfigLoadError`
  (→ `StartupError` in lifespan) instead of silently reconciling zero record types
- `files_catalog.py` imports as a `clarinet_plan.` submodule, cached so
  `record_types.py` can import it (e.g. `from clarinet_plan.files_catalog import seg`)
- File names auto-derived from variable names (in `files_catalog.py` or `record_types.py`)
- Resolves `data_schema`: dict as-is, `.json` path, or `{name}.schema.json` sidecar

`plan_package.py` — the `clarinet_plan` anchor machinery (activate/ensure/
deactivate, `module_name_for`, `import_plan_module`). `custom_registry.py` —
`CustomCodeRegistry[T]`: single owner for the three decorator registries (schema
hydrators, slicer context hydrators, record validators). See
`.claude/rules/custom-code-loading.md`.

## TOML Exporter (`toml_exporter.py`)

```python
async def export_record_type_to_toml(rt: RecordType, folder: Path) -> Path
async def export_data_schema_sidecar(rt: RecordType, folder: Path) -> Path | None
async def export_ui_schema_sidecar(rt: RecordType, folder: Path) -> Path | None
async def delete_record_type_files(name: str, folder: Path) -> list[Path]
```

- Uses `tomli_w` for TOML serialization
- `data_schema` → separate `{name}.schema.json` sidecar
- `ui_schema` → separate `{name}.ui_schema.json` sidecar (formosh presentation hints)
- `file_registry` → `[[file_registry]]` array of tables in TOML

**TOML round-trip is sidecar-authoritative.** `_record_type_to_toml_dict`
intentionally omits `data_schema` and `ui_schema` — when an admin edits a
RecordType through the API in TOML mode, the rewritten `{name}.toml` carries
scalars/file_registry only, and both schemas come from their sidecars.
Operators who keep `data_schema = { ... }` or `ui_schema = { ... }` inline in
TOML will see those inline values disappear from the body after the first API
edit (the sidecar files now hold the canonical value). This mirrors the
pre-existing behavior for `data_schema`.

## API Guards

`require_mutable_config(request)` in `dependencies.py`:
- Raises `AuthorizationError` (→ 403) if `config_mode == "python"`
- Applied to `POST /types`, `PATCH /types/{id}`, `DELETE /types/{id}`

In TOML mode, these endpoints also trigger background TOML export/delete.

## Bootstrap Integration

`reconcile_config()` in `bootstrap.py`:
- Dispatches by `settings.config_mode`
- TOML: discover files → load → resolve file refs → reconcile
- Python: `load_python_config()` → reconcile
- Called from `app.py` lifespan, stores mode in `app.state.config_mode`
