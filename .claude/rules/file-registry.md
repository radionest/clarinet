---
paths:
  - "clarinet/models/file_schema.py"
  - "clarinet/repositories/file_definition_repository.py"
  - "clarinet/utils/file_patterns.py"
  - "clarinet/utils/file_checksums.py"
  - "clarinet/utils/file_registry_resolver.py"
---

# File Registry System

File definitions are stored in a normalized schema with M2M relationship.

## Models (`file_schema.py`)

**`FileDefinition`** (table) — globally unique file definitions:

| Field | Type | Notes |
|-------|------|-------|
| `name` | `str` | Unique, valid Python identifier |
| `pattern` | `str` | Placeholders: `{id}`, `{patient_id}`, `{data.FIELD}`, etc. |
| `description` | `str \| None` | Purpose description |
| `multiple` | `bool` | `True` = glob collection, `False` = singular |
| `level` | `DicomQueryLevel \| None` | Cross-level file access; `None` = same as RecordType |

**`RecordTypeFileLink`** (table) — M2M: RecordType ↔ FileDefinition:

| Field | Type | Notes |
|-------|------|-------|
| `role` | `FileRole` | `INPUT` / `OUTPUT` / `INTERMEDIATE` |
| `required` | `bool` | Whether file must exist |

**`FileDefinitionRead`** (DTO) — flat merge of identity + binding for API.

**`RecordFileLink`** (table): M2M link between `Record` and `FileDefinition` with `filename` and optional `checksum`.
**`RecordFileLinkRead`** (DTO): per-file link with `name`, `filename`, `checksum`.

- `FileDefinition` and `FileDefinitionRead` both define identical `validate_name_is_identifier` — update both when changing.
- `RecordRead.file_links`: `list[RecordFileLinkRead]` — structured M2M data, preferred over dict fields.
- `RecordRead.files` / `RecordRead.file_checksums`: **deprecated** dict fields (use `file_links` instead).

## ORM vs DTO: file_links vs file_registry

- **`file_links`** (ORM): SQLAlchemy relationships. Used for DB writes needing `FileDefinition.id` as FK.
- **`file_registry`** (property/field): `list[FileDefinitionRead]` — flat merge. Used for API, validation, path resolution.

**Rule:** Writing to DB → `file_links` (ORM). Reading file metadata → `file_registry` (DTO via `RecordTypeRead`).

## Eager Loading

All `RecordType` queries must eagerly load file links:
```python
selectinload(RecordType.file_links).selectinload(RecordTypeFileLink.file_definition)
```
Handled by `_file_links_eager_load()` in `RecordTypeRepository` and `_record_type_with_files()` in `RecordRepository`.

All `Record` queries for API must also eager-load record file links:
```python
selectinload(Record.file_links).selectinload(RecordFileLink.file_definition)
```
Handled by `_record_file_links_eager_load()` in `RecordRepository`.

## Project-level File Registry

`file_registry.toml` (preferred) or `.json` in tasks folder defines shared file definitions.
Resolution at bootstrap time via `clarinet/utils/file_registry_resolver.py`.
Backward-compatible: inline `"file_registry"` in task JSONs still works.
