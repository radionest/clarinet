---
paths:
  - "clarinet/models/file_schema.py"
  - "clarinet/repositories/file_definition_repository.py"
  - "clarinet/files/_patterns.py"
  - "clarinet/files/_checksums.py"
  - "clarinet/utils/file_registry_resolver.py"
---

# File Registry System

File definitions are stored in a normalized schema with M2M relationship.

## Models (`file_schema.py`)

**`FileDefinition`** (table) — globally unique file definitions:

| Field | Type | Notes |
|-------|------|-------|
| `name` | `str` | Unique, valid Python identifier |
| `pattern` | `str` | Placeholders: `{id}`, `{patient_id}`, `{parent_id}`, `{data.FIELD}`, etc. |
| `description` | `str \| None` | Purpose description |
| `multiple` | `bool` | `True` = glob collection, `False` = singular |
| `level` | `DicomQueryLevel \| None` | Cross-level file access; `None` = same as RecordType |

**`RecordTypeFileLink`** (table) — M2M: RecordType ↔ FileDefinition:

| Field | Type | Notes |
|-------|------|-------|
| `role` | `FileRole` | `INPUT` / `OUTPUT` / `INTERMEDIATE` |
| `required` | `bool` | Whether file must exist |
| `allow_path_collision` | `bool` | Opt out of the output-path uniqueness guard (see below) — this binding may share its resolved path with another file of the record. Default `False` |

**`FileDefinitionRead`** (DTO) — flat merge of identity + binding for API.

**`RecordFileLink`** (table): M2M link between `Record` and `FileDefinition` with `filename` and optional `checksum`.
**`RecordFileLinkRead`** (DTO): per-file link with `name`, `filename`, `checksum`.

- `FileDefinition` and `FileDefinitionRead` both define identical `validate_name_is_identifier` — update both when changing.
- `RecordRead.file_links`: `list[RecordFileLinkRead]` — structured M2M data, preferred over dict fields.
- `RecordRead.files` / `RecordRead.file_checksums`: **deprecated** dict fields (use `file_links` instead).

## Output-Path Uniqueness (`config/path_uniqueness.py`)

Fail-fast, config-load-time check (Python/TOML load and RecordType `POST`/PATCH):
every non-collection OUTPUT file must resolve to a distinct path per coexisting
record, or two records silently overwrite each other's file. A pattern passes
if it embeds `{id}` (always unique) or the placeholder its `RecordType`
actually needs — `{user_id}` when `"user"` is in `unique_by`, `{parent_id}`
when `"parent"` is in `unique_by` **and** `parent_required=True` (`{origin_type}`
only distinguishes parent *types*, never two same-type parents, so it never
satisfies this), or the RecordType's own level-UID placeholder when the file's
`level` is coarser than the RecordType's. Per-`FileRef` `allow_path_collision=True`
opts a single binding out (the author guarantees uniqueness some other way);
every other OUTPUT file on the RecordType is still checked.

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
