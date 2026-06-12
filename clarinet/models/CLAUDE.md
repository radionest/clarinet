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

`*Read` models are dumb data containers with no path logic. Use
`FileRepository(record).working_dir` / `resolve_file(...)`. Strict by default —
`AnonPathError` for not-yet-anonymized records. Full contract (Slicer-arg
rendering, `resolve_with_fallback`): `clarinet/CLAUDE.md` → "File path resolution".

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
- Parent existence validated in `RecordService.create_record()` (raises `RecordNotFoundError` → 404)
- `user_id` is inherited from parent only when the child's `RecordType.inherit_user_from_parent` is `True` and no explicit `user_id` is given (applied in `RecordService.create_record`)

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

`Patient.auto_id` — unique non-PK integer, **NOT NULL** at the DB level, typed
`int | None` in Python. Auto-assigned by `PatientRepository.create()` via a
monotonic counter that never decreases (PG: `patient_auto_id_seq` sequence;
SQLite: `AutoIdCounter` single-row table); explicit values advance the counter
to prevent collisions. Direct `session.add(Patient(...))` without `auto_id`
raises `IntegrityError` at flush — test code must provide it (or use factories).

## File Registry System

Detailed reference: `.claude/rules/file-registry.md` (auto-loaded when editing `file_schema.py`).

Key points:
- M2M: `FileDefinition` ↔ `RecordType` via `RecordTypeFileLink`, and `FileDefinition` ↔ `Record` via `RecordFileLink`
- **ORM** (`file_links`): for DB writes. **DTO** (`file_registry`): for API/logic reads
- All `RecordType`/`Record` queries must use `selectinload` for file links
- `RecordRead.files`/`file_checksums` are **deprecated** — use `file_links` instead

## Record Statuses: `preparing` / `blocked`

Three "record not available for work" statuses with distinct exit conditions:

| Status | Why unavailable | Who releases it |
|---|---|---|
| `preparing` | system is preparing the record (prefill, file/context generation) | flow/pipeline, via explicit status update only |
| `blocked` | prerequisites not met (currently: required input files) | automatic, via check-files |
| `pause` | administrative decision | human |

Lifecycle: `preparing → (blocked if files missing) → pending → inwork → finished/failed`.

`blocked` contract: "prerequisites not met". Today the only prerequisite is
required input files; completed sibling record types may be added later.
- Records with missing required input files get `blocked` status on creation (instead of raising)
- `POST /records/{id}/check-files` auto-unblocks when files appear → transitions to `pending`

`preparing` contract: "the system is actively preparing the record".
- Set via `RecordCreate(status="preparing")` or `update_status` / RecordFlow `update_record(status='preparing')`
- Creation-time auto-blocking is skipped for `preparing` records (files are checked on exit instead)
- `check_files` never touches `preparing` records — this is what removes the
  race between prefill and a concurrent check-files call
- On the explicit `preparing → pending` transition, `RecordService.update_status`
  re-validates input files: invalid → the record lands in `blocked`, not `pending`
  (linearizes both waits: preparation → file wait → ready)
- Prefill is allowed (like `blocked`); submit returns 409

Both `preparing` and `blocked` records cannot be assigned to users or accept
data submissions; `find_pending_by_user()` excludes both.

## Frontend Consistency

When changing `*Read`, `*Create`, or `*Optional` schemas — update corresponding Gleam types in `clarinet/frontend/clarinet/api/`.

## Primary keys after insert/get — `int | None` typing

All `*.id` fields on table models are typed `int | None` (populated only after
flush), so mypy flags passing `record.id` where `int` is expected. When an
upstream invariant guarantees the value (repository `get`/`find`, just flushed),
narrow at the call site — do **not** weaken the callee's signature:

```python
assert record.id is not None  # SQLModel PK after get
```

Real example: `clarinet/api/routers/dicom.py::_dispatch_background_anonymization` (PR #237).

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
Classes inheriting `SQLModel` (even `table=False` DTOs) take `schema_extra={...}`;
`json_schema_extra` is Pydantic's spelling. Using the wrong one silently does nothing.

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
```

`sql_expression.true()` / `.false()` are the only **dialect-aware** Boolean
literals: `true`/`false` on PG (no implicit int→bool cast — `DEFAULT 1` fails
even in `CREATE TABLE`), `1`/`0` on SQLite. **Do NOT use:** `text("1")` (raw
integer literal breaks PG — the PR #149 v1 trap, fixed in #150); `text("true")`
(SQLite rejects it inside `ALTER TABLE` in some versions); plain `"1"` (works on
PG via implicit cast but causes spurious alembic autogen diffs).

**Alternatives:** nullable `Optional[X]` — only if `None` is domain-meaningful;
or a hand-written add-nullable → backfill → `alter_column(nullable=False)`
migration for values inexpressible as a single SQL literal.

**Regression tests:** `tests/migration/test_schema_integrity.py::TestServerDefaultsForAdditiveMigrations`
(metadata scan) and `tests/migration/test_data_preservation.py::TestAddNotNullBooleanRequiresServerDefault`
(real `ALTER TABLE` on populated SQLite + PG; the PG leg = stage 6 of
`make test-all-stages`, or `make test-migration` with `CLARINET_TEST_DATABASE_URL`
pointing at any PG instance; see `tests/migration/conftest.py`).

## Type Aliases (`clarinet/types.py`)

`PortableJSON = JSON().with_variant(JSONB(), "postgresql")` — JSONB on PostgreSQL (supports GROUP BY / DISTINCT / equality), JSON on SQLite. Use for all JSON columns.
