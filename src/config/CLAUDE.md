# Config Package Guide

Two mutually exclusive config modes per project:
- **TOML mode** (default): Bidirectional sync — TOML files <-> DB via API
- **Python mode**: Python files = single source of truth, API mutations disabled

## Settings

```python
config_mode: Literal["toml", "python"] = "toml"
config_tasks_path: str = "./tasks/"
config_delete_orphans: bool = False
```

## Primitives (`primitives.py`)

User-facing Pydantic BaseModels for Python config files:

```python
from src.config import RecordType, File, FileRef
from src.models.file_schema import FileRole

seg_mask = File(pattern="seg.nrrd", description="Segmentation mask")

lesion_seg = RecordType(
    name="lesion_seg",
    description="Lesion segmentation",
    files=[FileRef(seg_mask, role=FileRole.INPUT)],
)
```

- `File`: pattern, multiple, level (`DicomQueryLevel | None`, persisted to DB), description, name (auto-derived from variable name)
- `FileRef(file, role, required)`: binds File to RecordType with role
- `RecordTypeDef` (exported as `RecordType`): full RecordType definition

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

Expected folder structure:
```
tasks/
    files_catalog.py   # File instances (optional)
    record_types.py    # RecordTypeDef instances
```

- Uses `importlib.util.spec_from_file_location()` (same pattern as RecordFlow loader)
- `files_catalog.py` kept in `sys.modules` while `record_types.py` loads (for imports)
- File names auto-derived from variable names in `files_catalog.py`
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
