---
type: Convention
title: Persistence conventions
description: How to write SQLModel models, repositories and migrations here — schema naming, eager loading, the server_default rule for additive migrations, and the pitfalls that only surface on PostgreSQL.
tags: [sqlmodel, repositories, migrations, alembic, postgres]
timestamp: 2026-07-21T19:46:32Z
---

The repository layer owns every DB access; see [Backend architecture](./architecture.md)
for how it sits under services and routers. This page is about writing the code
inside that layer correctly.

## Repositories

`BaseRepository[ModelT]` (`clarinet/repositories/base.py`) provides:

| Method | Returns | On not found |
|---|---|---|
| `get(id)` | `ModelT` | raises `EntityNotFoundError` |
| `get_optional(id)` | `ModelT \| None` | `None` |
| `get_by(**filters)` | `ModelT \| None` | `None` |
| `exists(**filters)` | `bool` | `False` |
| `get_all(skip, limit, **filters)` / `list_all(**filters)` | `Sequence[ModelT]` | empty list |
| `count(**filters)` | `int` | `0` |
| `create(entity)` / `create_many(entities)` | `ModelT` / `list[ModelT]` | flushes + refreshes, no commit |
| `update(entity, update_data, options=)` | `ModelT` | — |
| `delete(entity)` / `delete_by_id(id)` | `None` / `bool` | — |

Repositories raise **only** from `clarinet.exceptions.domain` — never import
`clarinet.exceptions.http` here; converting to HTTP is the API layer's job.

**NULL comparisons need the SQLAlchemy spelling.** `Record.user_id == None` does
not generate the SQL you want; use `col(Record.user_id).is_(None)` /
`.is_not(None)`.

## Eager loading

Async SQLAlchemy cannot lazy-load, so a missed `selectinload()` surfaces as
`MissingGreenlet` at response-serialisation time rather than as an N+1.

- Every `RecordType` query must eager-load file links:
  `selectinload(RecordType.file_links).selectinload(RecordTypeFileLink.file_definition)`.
  Helpers exist: `_file_links_eager_load()`, `_record_type_with_files()`,
  `_record_file_links_eager_load()`.
- `BaseRepository.update()` refreshes via `session.refresh()`, which does **not**
  load relationships. Pass `options=[selectinload(Model.rel)]` when the caller
  will touch a relationship afterwards, or re-fetch through a `get()` that
  already eager-loads.
- Updating an M2M set: delete existing links → `flush()` → add new link objects →
  `commit()` → re-fetch the parent with `selectinload`.
- For aggregates, batch-fetch instead of looping:
  `select(RecordType).where(RecordType.name.in_(names))` → build a dict.

## Model schema naming

| Variant | Purpose | Base |
|---|---|---|
| `{Model}Base` | shared fields, no relationships | `BaseModel` or `SQLModel` |
| `{Model}` (`table=True`) | ORM table with relationships | `{Model}Base` |
| `{Model}Create` | creation payload | `{Model}Base` |
| `{Model}Read` | API response with nested relations | `{Model}Base` |
| `{Model}Find` | search query, all optional | `SQLModel` |
| `{Model}Optional` | partial update, all optional | `SQLModel` |

`BaseModel` applies an `empty_to_none` validator to every field of every
subclass: `""` and `"null"` become `None`, and `\x00` is stripped. Deliberately
opt out where empty string is meaningful — `PipelineTaskRunCreate` does not
inherit it because workers legitimately send `queue=""`.

**Computed fields belong on `*Read`, not on the ORM model.** A `@computed_field`
on the Pydantic response model reads plain data and cannot trigger a lazy load;
the same field on the ORM class raises `MissingGreenlet`. Pydantic v2 also
refuses to let a `@computed_field` override a parent field — use a `@property`
on the ORM plus a regular field on the DTO populated by
`model_validator(mode="before")` (see `RecordType.file_registry` →
`RecordTypeRead.file_registry`).

When a `*Read`, `*Create` or `*Optional` schema changes, update the matching
Gleam types under `clarinet/frontend/src/api/`.

## Additive migrations on populated tables

**Every new non-nullable column on an existing table must declare a
`server_default`.** Without one, Alembic autogenerate emits
`ALTER TABLE … ADD COLUMN … NOT NULL`, which PostgreSQL rejects on any populated
table. SQLite silently accepts it, so SQLite-only test runs never catch this.

```python
from sqlalchemy.sql import expression as sql_expression

mask_patient_data: bool = Field(
    default=True,
    sa_column_kwargs={"server_default": sql_expression.true()},
)
```

A boolean column's `server_default` must render to the **same truth value** as
its model `default`. New rows take the Pydantic default, migration-backfilled
rows take the `server_default`; if they disagree, the row is born in a state the
config reconciler cannot converge (issue #389, where the then-current
`unique_per_user` — since replaced by `unique_by` — shipped `default=True`
with `server_default=false()`). A metadata guard
(`test_recordtype_bool_server_defaults_match_model_defaults`) enforces the match.

`sql_expression.true()` / `.false()` are the only dialect-aware boolean
literals. Do **not** use `text("1")` (breaks PG), `text("true")` (SQLite rejects
it inside some `ALTER TABLE`s), or plain `"1"` (causes spurious autogen diffs).

Alternatives: a nullable `Optional[X]` when `None` is domain-meaningful, or a
hand-written add-nullable → backfill → `alter_column(nullable=False)` migration.
Regression coverage lives in `tests/migration/test_schema_integrity.py` and
`tests/migration/test_data_preservation.py`; the PostgreSQL leg runs as stage 6
of `make test-all-stages`.

The framework itself ships **no** migrations — `alembic/` is generated per
downstream project by `clarinet/utils/migrations.py` via `clarinet init-migrations`
and `clarinet db migrate`. Downstream projects therefore need their own
migration for each new framework table (`record_event`, `pipeline_task_run`, …).

## Pitfalls

- **`from __future__ import annotations` is forbidden in `table=True` files.** It
  stringifies type hints and breaks SQLAlchemy's `Relationship()` parsing. Use
  manual forward references: `list["ModelName"]`.
- **`list`/`dict` fields in `table=True` models need `sa_column=Column(JSON)`** —
  every inherited field becomes a column, and SQLModel has no default SQL type
  for them. Use the `PortableJSON` alias from `clarinet/types.py`
  (`JSON().with_variant(JSONB(), "postgresql")`) so PostgreSQL gets JSONB and its
  GROUP BY / DISTINCT / equality support.
- **`SQLModel.Field()` takes `schema_extra`, not `json_schema_extra`.** The
  Pydantic spelling silently does nothing on SQLModel subclasses.
- **Primary keys are `int | None`** until flush, so mypy flags passing
  `record.id` where `int` is expected. Narrow at the call site
  (`assert record.id is not None`) rather than weakening the callee's signature.
- **`expire_on_commit=False` is global**, so after committing new M2M links in
  the same session, `selectinload` will not reload a relationship already cached
  in the identity map. In tests, call `session.expire_all()` between passes, or
  use the `fresh_session` fixture, which starts with an empty identity map and
  therefore reproduces production behaviour.
