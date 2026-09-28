---
type: Convention
title: Persistence conventions
description: How to write SQLModel models, repositories and migrations here — schema naming, eager loading, the server_default rule for additive migrations, who owns migrations, and dialect pitfalls on PostgreSQL and SQLite.
tags: [sqlmodel, repositories, migrations, alembic, postgres, sqlite]
timestamp: 2026-09-29T12:00:00Z
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

**NULL comparisons: prefer the explicit SQLAlchemy spelling.**
`Record.user_id == None` compiles to `IS NULL` and is correct, but
`col(Record.user_id).is_(None)` / `.is_not(None)` states the intent and does
not trip the `E711` lint rule enabled in this repo.

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
- Updating an M2M set goes through the loaded collection: `parent.links = []` →
  `flush()` (the `delete-orphan` cascade deletes the rows) → `parent.links.append(link)`
  per new link → `commit()` → re-fetch the parent with `selectinload`. Never
  `session.delete()` a link that is still inside the loaded collection: the
  deleted objects stay there, and their remove events — through pydantic's
  value-based `__eq__` on link models — strip the freshly added links from it
  (#567; mechanism in `clarinet/repositories/CLAUDE.md`, "M2M Link Lifecycle").
- For aggregates, batch-fetch instead of looping:
  `select(RecordType).where(RecordType.name.in_(names))` → build a dict.
- The authenticated `User` (`read_token`, the `X-Internal-Token` admin row, the
  `/dicom-web` cache) is returned **detached**, with only `roles` eager-loaded, so
  a rollback later in the request cannot expire it into `MissingGreenlet`. The
  role rows themselves stay in the session (the default cascade has no
  `expunge`): read role names before any rollback. Do not touch the user's other
  relationships; re-fetch through the user repository.

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
`ALTER TABLE … ADD COLUMN … NOT NULL`, which PostgreSQL and SQLite both reject
once the table has rows (SQLite: `Cannot add a NOT NULL column with default
value NULL`). Both accept it on an empty table, and test databases are empty,
so the test suite alone never catches this.

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

Autogenerate compiles a `server_default` with the database it is connected
to: on SQLite — the scaffold default — `false()` would land as `sa.text('0')`,
which PostgreSQL rejects (#450); on PostgreSQL `func.now()` would land as
`sa.text('now()')`, which SQLite rejects. `render_item` in
`clarinet/utils/migrations.py` renders them as `sa.true()` / `sa.false()` /
`sa.func.now()`, compiled by the database applying the migration. The
generated `alembic/env.py` is a three-line shim over `run_env()` in the same
module, which passes `render_item`, `compare_type=True` and
`render_as_batch=True`: type, nullability and index changes render as batch
operations — plain `ALTER`s on PostgreSQL, table rebuilds on SQLite (which has
no `ALTER COLUMN`) — so a revision applies on both, whichever database generated
it. Foreign-key and unique-constraint changes still need a hand-written step,
because the framework's constraints are unnamed. `env.py` is written only when
missing, so an `env.py` from before #655 is replaced by hand (CHANGELOG), and
`clarinet init-migrations` / `clarinet db migrate create` warn until it is. The
hook sees model defaults only — a downgrade that re-adds a dropped boolean
column still renders the reflected `sa.text('0')`.

Alternatives: a nullable `Optional[X]` when `None` is domain-meaningful, or a
hand-written add-nullable → backfill → `alter_column(nullable=False)` migration.
Regression coverage lives in `tests/migration/test_schema_integrity.py`,
`tests/migration/test_data_preservation.py` and
`tests/migration/test_cli_functions.py` (`TestCrossDialectRegression`
autogenerates on SQLite and applies on PostgreSQL); the PostgreSQL leg runs in CI
(`test-postgres` job) and as stages 2b and 6 of `make test-all-stages`.

## Who owns migrations

The framework ships **no** migrations, by design: downstream projects run and
are tested in different environments, upgrade from different framework
versions, and some migrations need project-specific data backfills. Each
project owns its `alembic/` history, generated by `clarinet init-migrations`
and `clarinet db migrate create`. The framework owns the inputs to that
autogenerate and tests them:

1. Models use dialect-portable column types (`PortableJSON`, `sqlalchemy.Uuid`).
2. `alembic/env.py` is a shim over `clarinet.utils.migrations.run_env()`, so
   render/compare/batch policy ships with the package.
3. `tests/migration/test_cli_functions.py::TestCreateMigration` asserts that
   autogenerate right after `upgrade head` is empty and applies — on SQLite,
   and on PostgreSQL in CI's `test-postgres` job.
4. Every schema change ships a CHANGELOG **Downstream migration** note; new
   framework tables (`record_event`, `pipeline_task_run`, …) need one too.

Autogenerate does not handle everything portably: PostgreSQL enum labels
(`ALTER TYPE … ADD VALUE`), `server_default` changes (`compare_server_default`
is off) and foreign-key / unique-constraint changes (the framework's constraints
are unnamed, so the rendered `drop_constraint` fails on the other dialect) need
a hand-written step in that note.

## Pitfalls

- **`from __future__ import annotations` is forbidden in `table=True` files.** It
  stringifies type hints and breaks SQLAlchemy's `Relationship()` parsing. Use
  manual forward references: `list["ModelName"]`.
- **`list`/`dict` fields in `table=True` models need `sa_column=Column(JSON)`** —
  every inherited field becomes a column, and SQLModel has no default SQL type
  for them. Use the `PortableJSON` alias from `clarinet/types.py`
  (`JSON().with_variant(JSONB(), "postgresql")`) so PostgreSQL gets JSONB and its
  GROUP BY / DISTINCT / equality support.
- **UUID columns use `sqlalchemy.Uuid`**, never `postgresql.UUID` / `sa.UUID`
  (the same class). SQLite keeps the declared name `UUID` and reflects it as
  `NUMERIC`, so autogenerate never reaches an empty diff and emits
  `alter_column` ops SQLite rejects (#655). `Uuid` is native `UUID` on
  PostgreSQL and `CHAR(32)` on SQLite.
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
- **Write rows another request may delete with a Core `update()`, not ORM
  assignment + commit.** An ORM flush emits `UPDATE … WHERE pk` and raises
  `StaleDataError` when 0 rows match, so a concurrent delete (logout, revoke,
  session limit) turns the request into a 500. `update(Model).where(...).values(...)`
  matches 0 rows silently. Example: `DatabaseStrategy.read_token`'s
  `last_accessed` write (#665).
