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
| `pattern` | `str` | Placeholders: `{id}`, `{patient_id}`, `{parent_id}`, `{data.FIELD}` (temporarily rejected — see [#552](https://github.com/radionest/clarinet/issues/552)), etc. |
| `description` | `str \| None` | Purpose description |
| `multiple` | `bool` | `True` = glob collection, `False` = singular |
| `level` | `DicomQueryLevel \| None` | Cross-level file access; `None` = same as RecordType |
| `grid_conform_to` | `str \| None` | Name of another bound `FileDefinition` whose on-disk voxel grid this file must match. `None` = no check (see below) |
| `on_grid_mismatch` | `str \| None` | `GridMismatchAction`: `conform` \| `delete` \| `reject`. Consulted only for OUTPUT files at submit time; `None` = `reject` |

**`RecordTypeFileLink`** (table) — M2M: RecordType ↔ FileDefinition:

| Field | Type | Notes |
|-------|------|-------|
| `role` | `FileRole` | `INPUT` / `OUTPUT` / `INTERMEDIATE` |
| `required` | `bool` | Whether file must exist |
| `allow_path_collision` | `bool` | Opt out of the output-path uniqueness guard (see below) — this binding may share its resolved path with another file of the record. Default `False` |

**`FileDefinitionRead`** (DTO) — flat merge of identity + binding for API.

**`RecordFileLink`** (table): M2M link between `Record` and `FileDefinition` with `filename` and optional `checksum`.
**`RecordFileLinkRead`** (DTO): per-file link with `name`, `filename`, `checksum`.

**Pattern rules** (`validate_file_pattern`, `clarinet/files/_template.py`, wired
as a `@model_validator(mode="after")` on `FileDefinitionRead` only —
`FileDefinition` is `table=True`, where SQLModel skips Pydantic validation).
Three families:

1. **Literal text** — no absolute prefix, backslash, NUL, `..`, trailing
   separator, dot-leading basename.
2. **Placeholder names** — every *name-shaped* `{name}` must be one the renderer
   can resolve (a brace group the renderer never substitutes, such as `{1}` or
   `{x:s}`, renders literally and is unaffected):
   `{id}`, `{parent_id}`, `{user_id}`, `{patient_id}`, `{study_uid}`,
   `{series_uid}`, `{origin_type}`, `{record_type.name}`. A typo such as
   `{studyuid}` rendered to `""` under LENIENT and then either failed at the
   join or — in `{studyuid}.nrrd` — silently produced the hidden file `.nrrd`.
   `{data.*}` is rejected by its own rule, see
   [#552](https://github.com/radionest/clarinet/issues/552).
3. **Worst-case render** — the pattern must still be a well-formed relative
   name when every **optional** placeholder is absent. Optional =
   `{parent_id}`, `{user_id}`, `{study_uid}`, `{series_uid}` — a parentless,
   unassigned, patient-level or study-level record legitimately has none. So
   `{parent_id}.txt` (→ `.txt`), `{user_id}` (→ `""`), `{study_uid}/mask.nrrd`
   (→ `/mask.nrrd`) and `{parent_id}.` (→ `.`) are rejected; give the segment
   literal text (`report_{parent_id}.txt`). `{id}`, `{patient_id}`,
   `{record_type.name}` and `{origin_type}` are never absent and need no prefix.

**Collections (`multiple=True`) are exempt from families 2 and 3** — they glob
rather than render (`{parent_id}.nrrd` → `*.nrrd`), which is exactly why the
check is a *model* validator: it must read `multiple` alongside `pattern`. In a
collection the placeholder's *name* is meaningless — `glob_file_paths`
substitutes `*` for every one of them — so `slice_{n}.dcm` → `slice_*.dcm` is a
positional-wildcard idiom, not a typo. Family 1 judges the literal text and
still applies, as does the `{data.*}` ban. Full rationale: the "Path-safety
guards" section of the framework's `docs/kb/files-and-anonymization.md`.

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

The guarantee is only as strong as the config proves: a `parent_required=False`
type whose records nevertheless receive a `parent_record_id` (e.g. a flow's
`create_record(parent_record_id=…)`) is not forced to carry `{parent_id}`, so
two such records under different parents can still collide on disk. The
validator demands a parent discriminator only when `parent_required=True`.

## Grid Conformance (`config/grid_conformance.py`)

Fail-fast, config-load-time check (Python/TOML load and RecordType
`POST`/PATCH — same `RecordTypeCreate` validator as Output-Path Uniqueness
above): a file that sets `grid_conform_to` must name a reference the runtime
can actually resolve and check, or `RecordConstraintViolationError` is
raised, naming the RecordType and the declaring file. Six rejection rules:
the reference isn't bound to *this* RecordType (an unknown name and a name
bound to a different RecordType raise the identical "unknown" error — lookup
is scoped to this RecordType's own registry); it's a self-reference; its
*effective* level (own `level`, or the RecordType's when unset) is finer
than the declaring file's; either side has `multiple=True` (singular files
only); or either pattern isn't a grid-readable format (`.nii`, `.nii.gz`,
`.nrrd` — `.seg.nrrd` is covered by the `.nrrd` check).

The declaration is a property of the *file*, not of a `RecordTypeFileLink`
binding — every RecordType binding a file inherits it. INPUT mismatches
always `blocked` the record (an input may be shared with sibling records, so
it is never auto-repaired or deleted); OUTPUT mismatches are enforced
pre-commit on submit per `on_grid_mismatch`. Full runtime behavior, the
decision table, and the adoption order:
[`docs/grid-workflows.md`](../../docs/grid-workflows.md#runtime-grid-conformance-enforcement).

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
