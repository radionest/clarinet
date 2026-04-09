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

### Working Folder Contract

`RecordRead.working_folder` and `SeriesRead.working_folder` return `str` (never `None`).
Guaranteed by: exhaustive `DicomQueryLevel` enum, `validate_record_level` enforcing
required UIDs per level, FK cascades preventing orphans, eager loading in all API paths.

Two `_format_path` variants exist on `RecordRead` and `SeriesRead`:
- `_format_path_strict(template) -> str` — raises on failure (system templates)
- `_format_path(template) -> str | None` — returns None on failure (user-defined slicer templates)

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

## Parent-Child Relationships

Record supports optional parent-child links via `parent_record_id` — fully independent of RecordType.

**Record**: `parent_record_id` (FK → `record.id`, ON DELETE SET NULL) links to a specific parent record.
- Self-referencing FK with `Relationship` (parent_record / child_records)
- Validated in `RecordRepository.validate_parent_record()`: parent must exist
- `user_id` is inherited from parent if not explicitly set on child (API-level)

**RecordFlow**: `CreateRecordAction` supports explicit `parent_record_id` kwarg and inherits `user_id` from the triggering record if `inherit_user=True`.

## Search Models

`RecordFindResult` (in `record.py`) specifies a search criterion for JSON data fields:
- `result_name` — data field name
- `result_value` — expected value (str/bool/int/float)
- `comparison_operator` — `RecordFindResultComparisonOperator` enum (eq/ne/lt/gt/contains)
- `sql_type` — `@computed_field` that infers the SQL type from `result_value` (String/Boolean/Integer/Float) for use in SQLAlchemy JSON cast expressions

`PatientBase.anon_id` — `@computed_field` + `@property` derived from `auto_id`:
```python
f"{settings.anon_id_prefix}_{auto_id}"  # Returns None if auto_id is None
```
**Do NOT remove `@property`** — without it mypy sees the return type as
`Callable[[], str | None]` instead of `str | None` (upstream mypy bug, pydantic#11687).

`Patient.auto_id` — unique non-PK integer, **NOT NULL** at the DB level.
Auto-assigned by `PatientRepository.create()` via `MAX(auto_id) + 1`.
The `sa_column` has `nullable=False, unique=True` but **no** `autoincrement`
(SQLite ignores autoincrement on non-PK columns).

The Python type is `int | None` (default `None`) so that `PatientRepository.create()`
can accept a `Patient` with `auto_id=None` and assign it before flush.
Direct `session.add(Patient(...))` without `auto_id` will raise `IntegrityError` at flush.
Test code creating patients directly must always provide an explicit `auto_id`.

## File Registry System

Detailed reference: `.claude/rules/file-registry.md` (auto-loaded when editing `file_schema.py`).

Key points:
- M2M: `FileDefinition` ↔ `RecordType` via `RecordTypeFileLink`, and `FileDefinition` ↔ `Record` via `RecordFileLink`
- **ORM** (`file_links`): for DB writes. **DTO** (`file_registry`): for API/logic reads
- All `RecordType`/`Record` queries must use `selectinload` for file links
- `RecordRead.files`/`file_checksums` are **deprecated** — use `file_links` instead

## Record Status: `blocked`

`RecordStatus.blocked` — record created but required input files not yet available.
- Records with missing required input files get `blocked` status on creation (instead of raising)
- `POST /records/{id}/check-files` auto-unblocks when files appear → transitions to `pending`
- Blocked records cannot be assigned to users or accept data submissions
- `find_pending_by_user()` excludes blocked records

## Frontend Consistency

When changing `*Read`, `*Create`, or `*Optional` schemas — update corresponding Gleam types in `clarinet/frontend/clarinet/api/`.

## Pitfalls

**`from __future__ import annotations` is forbidden in `table=True` files.**
It turns type hints into strings, breaking SQLAlchemy `Relationship()` parsing.
Use manual string forward references: `list["ModelName"]`.

**Cannot override a parent field with `@computed_field` in Pydantic v2.**
`TypeError: Field 'X' overrides symbol of same name in a parent class`.
Pattern: use a `@property` on ORM + a regular field on `*Read` DTO
populated via `model_validator(mode="before")`. See `RecordType.file_registry`
(property) → `RecordTypeRead.file_registry` (field).

**`list`/`dict` fields in `table=True` models need `sa_column=Column(JSON)`.**
Without it, SQLModel raises `ValueError: <class 'list'> has no matching SQLAlchemy type`
because every inherited field becomes a DB column.

**`SQLModel.Field()` uses `schema_extra`, not `json_schema_extra`.**
SQLModel DTO classes (`table=False`, no `table=True`) still inherit from `SQLModel`, not
`pydantic.BaseModel`. SQLModel's `Field()` accepts `schema_extra={"key": "value"}` for
JSON Schema metadata, while Pydantic's `Field()` uses `json_schema_extra`. Using the wrong
one silently does nothing. Rule: if the class inherits `SQLModel` → use `schema_extra`;
if it inherits `pydantic.BaseModel` → use `json_schema_extra`.

## Additive migrations on populated tables

**Every new non-nullable column on an existing table must declare `server_default`.**

Without it, alembic autogenerate emits `ALTER TABLE ... ADD COLUMN ... NOT NULL`,
which PostgreSQL rejects with `column "..." of relation "..." contains null values`
on any populated database. SQLite is more lenient and silently accepts the same DDL,
so SQLite-only test runs do **not** catch this — the bug surfaces only against PG.

**Pattern (booleans):**
```python
mask_patient_data: bool = Field(
    default=True,
    sa_column_kwargs={"server_default": text("1")},
)
```

Use the literal `text("1")` (not `text("true")` or `expression.true()`):
SQLite stores `BOOLEAN` as `INTEGER` and rejects `'true'` inside `ALTER TABLE`,
PostgreSQL accepts `'1'` for `BOOLEAN` via implicit cast — `"1"` is the only
literal that survives both dialects.

**Alternatives (when `server_default` is not appropriate):**
- Make the column nullable (`Optional[X]`) — only if `None` is meaningful at the
  domain level, not just to bypass this check.
- Hand-write a multi-step migration: add column nullable → backfill via
  `op.execute("UPDATE ...")` → `alter_column(..., nullable=False)`. Reserve for
  values that cannot be expressed as a single SQL literal.

**Regression tests:** `tests/migration/test_schema_integrity.py::TestServerDefaultsForAdditiveMigrations`
(metadata scan) and `tests/migration/test_data_preservation.py::TestAddNotNullBooleanRequiresServerDefault`
(real ALTER TABLE on populated SQLite + PostgreSQL via `db_backend` parametrization,
runs in stage 6 of `make test-all-stages`).

## Type Aliases (`clarinet/types.py`)

`PortableJSON = JSON().with_variant(JSONB(), "postgresql")` — JSONB on PostgreSQL (supports GROUP BY / DISTINCT / equality), JSON on SQLite. Use for all JSON columns.
