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
config_context_hydrators_file: str = "context_hydrators.py"  # slicer context hydrators
config_schema_hydrators_file: str = "hydrators.py"           # schema hydrators
```

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
- `RecordDef`: full RecordType definition; `role` is user-friendly alias for `role_name`; `level` accepts str or enum

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

- Uses `importlib.util.spec_from_file_location()` (same pattern as RecordFlow loader)
- `files_catalog.py` kept in `sys.modules` while `record_types.py` loads (for imports)
- File names auto-derived from variable names (in `files_catalog.py` or `record_types.py`)
- Resolves `data_schema`: dict as-is, `.json` path, or `{name}.schema.json` sidecar

## TOML Exporter (`toml_exporter.py`)

```python
async def export_record_type_to_toml(rt: RecordType, folder: Path) -> Path
async def export_data_schema_sidecar(rt: RecordType, folder: Path) -> Path | None
async def delete_record_type_files(name: str, folder: Path) -> list[Path]
```

- Uses `tomli_w` for TOML serialization
- `data_schema` → separate `{name}.schema.json` sidecar
- `file_registry` → `[[file_registry]]` array of tables in TOML

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
