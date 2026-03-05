# Models Guide

## Schema Naming Convention

| Variant | Purpose | Base class |
|---------|---------|------------|
| `{Model}Base` | Shared fields without relationships | `BaseModel` or `SQLModel` |
| `{Model}` (table=True) | ORM table model with relationships | `{Model}Base` |
| `{Model}Create` | Pydantic schema for creation | `{Model}Base` |
| `{Model}Read` | API response with nested relations | `{Model}Base` |
| `{Model}Find` | Search query (all optional) | `SQLModel` |
| `{Model}Optional` | Partial update (all optional) | `SQLModel` |

## Inheritance

`BaseModel` (base.py) provides `empty_to_none` validator on all fields:
- `""`, `"null"` → `None`; strips `\x00` characters
- Applied to **every** model inheriting from `BaseModel`

## Computed Fields & Eager Loading

Computed fields (`working_folder`, `radiant`, `slicer_args_formatted`, etc.) are defined on
**`RecordRead`** (Pydantic), not on `RecordBase`/`Record` (ORM). This prevents `MissingGreenlet`
errors from lazy-loading in async SQLAlchemy. Pattern (same as `SeriesRead.working_folder`):

```python
record = await repo.get_with_relations(record_id)
record_read = RecordRead.model_validate(record)
record_read.working_folder  # safe — all data is plain Pydantic fields
```

Always use `selectinload()` in repositories when fetching records for API responses:
```python
select(Record).options(
    selectinload(Record.patient),
    selectinload(Record.study),
    selectinload(Record.record_type),
)
```

## Event Listener: Record Timestamps

`@event.listens_for(Record.status, "set")` in `record.py` auto-updates:
- `started_at` ← when status becomes `RecordStatus.inwork`
- `finished_at` ← when status becomes `RecordStatus.finished`

## Record Level Validation

`RecordBase.validate_record_level` (model_validator) enforces:
- **PATIENT**: must have `patient_id`; `study_uid` and `series_uid` must be `None`
- **STUDY**: must have `patient_id` + `study_uid`; `series_uid` must be `None`
- **SERIES**: must have `patient_id` + `study_uid` + `series_uid`

## Search Models

`RecordFindResult` (in `record.py`) specifies a search criterion for JSON data fields:
- `result_name` — data field name
- `result_value` — expected value (str/bool/int/float)
- `comparison_operator` — `RecordFindResultComparisonOperator` enum (eq/ne/lt/gt/contains)
- `sql_type` — `@computed_field` that infers the SQL type from `result_value` (String/Boolean/Integer/Float) for use in SQLAlchemy JSON cast expressions

`PatientBase.anon_id` — `@computed_field` derived from `auto_id`:
```python
f"{settings.anon_id_prefix}_{auto_id}"  # Returns None if auto_id is None
```

## File Registry System

File definitions are stored in a normalized schema with M2M relationship:

- **`FileDefinition`** (`file_schema.py`, table): globally unique file definitions with `name`, `pattern`, `description`, `multiple`
- **`RecordTypeFileLink`** (`file_schema.py`, table): M2M link between `RecordType` and `FileDefinition` with per-binding `role` and `required`
- **`FileDefinitionRead`** (`file_schema.py`, DTO): flat Pydantic model merging identity + binding for API responses

`RecordType` has a `file_links` relationship (M2M) and a `get_file_registry()` method
that builds `list[FileDefinitionRead]` from the links. `RecordTypeRead` has `file_registry`
as a regular field, populated via `model_validator(mode="before")` from the ORM object.

- **`RecordFileLink`** (`file_schema.py`, table): M2M link between `Record` and `FileDefinition` with `filename` and optional `checksum`
- `FileRole`: `INPUT`, `OUTPUT`, `INTERMEDIATE`
- `multiple=True`: collection (glob), `multiple=False`: singular file
- Callers filter `file_registry` by role directly (no `input_files`/`output_files` computed properties)
- `RecordRead.files` / `RecordRead.file_checksums`: computed from `Record.file_links` via `model_validator(mode="before")`
- `RecordFileAccessor` (`src/services/file_accessor.py`): attribute-based file access
- `src/utils/file_checksums.py`: async SHA256 computation and change detection

### Eager Loading for File Links

All queries fetching `RecordType` must use eager loading for file_links:
```python
selectinload(RecordType.file_links).selectinload(RecordTypeFileLink.file_definition)
```
This is handled by `_file_links_eager_load()` in `RecordTypeRepository` and
`_record_type_with_files()` in `RecordRepository`.

All queries fetching `Record` for API responses must also eager-load record file links:
```python
selectinload(Record.file_links).selectinload(RecordFileLink.file_definition)
```
This is handled by `_record_file_links_eager_load()` in `RecordRepository`.

### ORM vs DTO: file_links vs file_registry

Two representations of file definitions serve different architectural layers:

- **`file_links`** (ORM, on `RecordType`/`Record`): SQLAlchemy relationships to
  `RecordTypeFileLink`/`RecordFileLink`. Require eager loading (`selectinload`).
  Used internally for DB writes that need `FileDefinition.id` as FK
  (e.g. `set_files()` creating `RecordFileLink` rows).

- **`file_registry`** (DTO, on `RecordTypeRead`): `list[FileDefinitionRead]` — flat merge of
  FileDefinition identity + binding metadata. Computed by `get_file_registry()` via
  `model_validator`. Used for API responses, file validation (`FileValidator`),
  file access (`RecordFileAccessor`), and config export.

**Rule of thumb:**
- Writing to DB (creating/deleting links) → use `file_links` (ORM layer)
- Reading file metadata for logic/API → use `file_registry` (DTO layer via `RecordTypeRead`)

### Project-level File Registry

A `file_registry.toml` (preferred) or `file_registry.json` in the tasks folder defines shared file definitions.
TOML takes precedence when both exist.
```toml
[segmentation]
pattern = "seg.nrrd"
description = "Segmentation mask"
```

Task configs use `"files"` references instead of inline `"file_registry"`:
```json
{"files": [{"name": "segmentation", "role": "input", "required": true}]}
```

Resolution happens at **bootstrap time** via `src/utils/file_registry_resolver.py`.
The reconciler creates `FileDefinition` rows and `RecordTypeFileLink` rows from the resolved definitions.
Backward-compatible: inline `"file_registry"` in task JSONs still works.

## Record Status: `blocked`

`RecordStatus.blocked` — record created but required input files not yet available.
- Records with missing required input files get `blocked` status on creation (instead of raising)
- `POST /records/{id}/check-files` auto-unblocks when files appear → transitions to `pending`
- Blocked records cannot be assigned to users or accept data submissions
- `find_pending_by_user()` excludes blocked records

## JSON Columns

Use `sa_column=Column(JSON)` for dict/list fields:
- `Record.data` — dynamic data per RecordType.data_schema
- `RecordType.slicer_script_args`, `data_schema`

Note: `Record.files` and `Record.file_checksums` are no longer JSON columns — they are
stored in the `record_file_link` M2M table via `RecordFileLink`.

## Custom Types

- `DicomUID` = `Annotated[str, StringConstraints(pattern=r"^[0-9\.]*$", min_length=5, max_length=64)]`

## Frontend Consistency

Backend models (`src/models/`) and frontend models (`src/frontend/src/api/`) must stay in sync.
When changing `{Model}Read`, `{Model}Create`, or `{Model}Optional` schemas — update the
corresponding Gleam types in:
- `src/frontend/src/api/models.gleam` — shared data models
- `src/frontend/src/api/types.gleam` — type definitions
- `src/frontend/src/api/records.gleam`, `series.gleam`, `studies.gleam`, `users.gleam`, `admin.gleam` — API-specific types

Check field names, types, and optionality match between Python schemas and Gleam types.

## Pitfalls

**`from __future__ import annotations` is forbidden in `table=True` files.**
It turns type hints into strings, breaking SQLAlchemy `Relationship()` parsing.
Use manual string forward references: `list["ModelName"]`.

**Cannot override a parent field with `@computed_field` in Pydantic v2.**
`TypeError: Field 'X' overrides symbol of same name in a parent class`.
Pattern: use a method on ORM (`get_X()`) + a regular field on `*Read` DTO
populated via `model_validator(mode="before")`. See `RecordType.get_file_registry()`
→ `RecordTypeRead.file_registry`.

**`list`/`dict` fields in `table=True` models need `sa_column=Column(JSON)`.**
Without it, SQLModel raises `ValueError: <class 'list'> has no matching SQLAlchemy type`
because every inherited field becomes a DB column.

## Forward References

Models use `TYPE_CHECKING` to avoid circular imports. Schemas with forward refs
need `model_rebuild()` — called in `app.py` lifespan (e.g. `SeriesFind.model_rebuild()`).
