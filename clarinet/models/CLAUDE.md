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

Computed fields (e.g. `radiant`, `context_info_html`) are defined on **`RecordRead`** (Pydantic),
not on `RecordBase`/`Record` (ORM). This prevents `MissingGreenlet` errors from lazy-loading in
async SQLAlchemy. Pattern:

```python
record = await repo.get_with_relations(record_id)
record_read = RecordRead.model_validate(record)
record_read.radiant  # safe — all data is plain Pydantic fields
```

### Path resolution lives in `FileRepository`

`RecordRead` / `SeriesRead` / `StudyRead` / `PatientRead` are dumb data
containers — they carry no path-resolution logic. Use
`FileRepository(record).working_dir` (or `resolve_file(...)`) to compute
on-disk paths. Slicer-arg rendering lives in
`clarinet.services.slicer.args.render_slicer_args`.

```python
from clarinet.repositories import FileRepository
from clarinet.services.slicer.args import render_slicer_args

working_dir = FileRepository(record_read).working_dir
args = render_slicer_args(record_read)        # script args
validator_args = render_slicer_args(record_read, validator=True)
```

Strict by default — a record whose template needs `{anon_*}` but whose
study/series is not yet anonymized raises `AnonPathError`. UX routers
catch and serve `null`; reader-side backend services that must keep
working through the pre-anon flow use
`FileRepository.resolve_with_fallback` instead of catching the
exception themselves.

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
Auto-assigned by `PatientRepository.create()` via a **monotonic counter** that
never decreases, even after patient deletion:
- **PostgreSQL**: native `Sequence` (`patient_auto_id_seq`) — `nextval()`.
- **SQLite**: `AutoIdCounter` table (single-row counter, lazy-seeded from `MAX(auto_id)`).

When an explicit `auto_id` is provided, `_advance_counter()` advances the
sequence/counter to at least that value, preventing future collisions.

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

## Primary keys after insert/get — `int | None` typing

All `*.id` fields on table models are typed `int | None` (SQLModel default — populated only after flush). Mypy will flag any call site that passes `record.id` to a parameter typed `int`:

```text
Argument 2 to "_run_orchestrator_in_process" has incompatible type "int | None"; expected "int"  [arg-type]
```

When the value is guaranteed by an upstream invariant (record came from a repository `get`/`find`, or has just been flushed), narrow with an explicit assert at the call site — do **not** weaken the callee's signature to `int | None`:

```python
record = await record_repo.get(record_id)
assert record.id is not None  # SQLModel PK after get
_run_orchestrator_in_process(study_uid, record.id, ...)
```

Real example: `clarinet/api/routers/dicom.py::_dispatch_background_anonymization` (mypy regression in PR #237).

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
from sqlalchemy.sql import expression as sql_expression

mask_patient_data: bool = Field(
    default=True,
    sa_column_kwargs={"server_default": sql_expression.true()},
)
unique_per_user: bool = Field(
    default=False,
    sa_column_kwargs={"server_default": sql_expression.false()},
)
```

Use `sql_expression.true()` / `sql_expression.false()` — these are the only
**dialect-aware** Boolean literals in SQLAlchemy. They render as `true`/`false`
on PostgreSQL (required — PG has no implicit int→bool cast, so `DEFAULT 1`
fails even in `CREATE TABLE` on an empty DB with "default for column is of
type integer") and as `1`/`0` on SQLite (which stores BOOLEAN as INTEGER).

**Do NOT use:**
- `text("1")` / `text("0")` — bypasses the dialect visitor, emits raw integer
  literal on PG, breaks both `CREATE TABLE` and `ALTER TABLE`. This was the
  trap PR #149 v1 fell into; fixed in PR #150.
- `text("true")` / `text("false")` — portable SQL keywords in theory but
  SQLite rejects them inside `ALTER TABLE` in some versions.
- `"1"` as plain string — quoted string literal `'1'` works on PG via implicit
  cast but is indirect and produces a `DefaultClause` that alembic's autogen
  compares poorly against existing column defaults, causing spurious diffs.

**Alternatives (when `server_default` is not appropriate):**
- Make the column nullable (`Optional[X]`) — only if `None` is meaningful at the
  domain level, not just to bypass this check.
- Hand-write a multi-step migration: add column nullable → backfill via
  `op.execute("UPDATE ...")` → `alter_column(..., nullable=False)`. Reserve for
  values that cannot be expressed as a single SQL literal.

**Regression tests:** `tests/migration/test_schema_integrity.py::TestServerDefaultsForAdditiveMigrations`
(metadata scan — always runs) and `tests/migration/test_data_preservation.py::TestAddNotNullBooleanRequiresServerDefault`
(real `ALTER TABLE` on populated SQLite + PostgreSQL via `db_backend`
parametrization). The PG leg runs in stage 6 of `make test-all-stages`; to
reproduce locally without the full VM, point `CLARINET_TEST_DATABASE_URL` at
any PG instance and run `make test-migration` (see `tests/migration/conftest.py`).

## Type Aliases (`clarinet/types.py`)

`PortableJSON = JSON().with_variant(JSONB(), "postgresql")` — JSONB on PostgreSQL (supports GROUP BY / DISTINCT / equality), JSON on SQLite. Use for all JSON columns.
