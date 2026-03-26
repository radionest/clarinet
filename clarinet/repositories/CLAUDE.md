# Repositories Guide

## Base Repository API

`BaseRepository[ModelT]` (generic, in `base.py`) provides:

| Method | Returns | On not found |
|--------|---------|-------------|
| `get(id)` | `ModelT` | raises `EntityNotFoundError` |
| `get_optional(id)` | `ModelT \| None` | returns `None` |
| `get_by(**filters)` | `ModelT \| None` | returns `None` |
| `exists(**filters)` | `bool` | returns `False` |
| `get_all(skip, limit, **filters)` | `Sequence[ModelT]` | empty list |
| `list_all(**filters)` | `Sequence[ModelT]` | empty list |
| `count(**filters)` | `int` | `0` |
| `create(entity)` | `ModelT` | — (flushes + refreshes, no commit) |
| `create_many(entities)` | `list[ModelT]` | — (flushes + refreshes, no commit) |
| `update(entity, update_data, options=)` | `ModelT` | — |
| `delete(entity)` | `None` | — |
| `delete_by_id(id)` | `bool` | returns `False` |

Also: `build_query()`, `execute_query(query)`, `refresh(entity)`.

## Domain Exceptions Only

Repositories raise exceptions from `clarinet.exceptions.domain`:

| Category | Exceptions |
|---|---|
| Not found | `EntityNotFoundError`, `UserNotFoundError`, `PatientNotFoundError`, `StudyNotFoundError`, `SeriesNotFoundError`, `RecordNotFoundError`, `RecordTypeNotFoundError`, `RoleNotFoundError` |
| Already exists | `EntityAlreadyExistsError`, `UserAlreadyExistsError`, `PatientAlreadyExistsError`, `RecordAlreadyExistsError`, etc. |
| Auth | `AuthenticationError`, `AuthorizationError`, `InvalidCredentialsError` |
| Business rules | `BusinessRuleViolationError`, `RecordConstraintViolationError`, `AlreadyAnonymizedError` |
| DB | `DatabaseError`, `DatabaseIntegrityError` |
| File/Storage | `StorageError`, `FileNotFoundError`, `FileSchemaError` |

**NEVER** import from `clarinet.exceptions.http` in repositories — that's API layer only.
Exception handlers in `clarinet/api/exception_handlers.py` convert domain → HTTP.

## NULL Comparison Gotcha

**WRONG:** `Record.user_id == None` (doesn't generate correct SQL)
**RIGHT:** `col(Record.user_id).is_(None)` / `.is_not(None)`

## RecordSearchCriteria (record_repository.py)

Special sentinel values for `anon_series_uid` / `anon_study_uid`:
- `"Null"` → `WHERE col.is_(None)` (find un-anonymized)
- `"*"` → `WHERE col.is_not(None)` (find anonymized)
- `None` → no filter applied

Other fields: `status`, `name` (record_type), `user_id`, `wo_user` (unassigned),
`parent_record_id` (filter by parent), `random_one`, `data_queries: list[RecordFindResult]` (JSON field queries with operators).

Specialized methods, invalidation, PatientRepository auto_id: `.claude/rules/record-repo.md` (auto-loaded for record repos).

## Eager Loading

Use `selectinload()` to avoid N+1. Nested loading for deep relations:
```python
selectinload(Patient.studies).selectinload(Study.series)
```

### FileDefinition Eager Loading

All `RecordType` queries must eagerly load file links:
```python
selectinload(RecordType.file_links).selectinload(RecordTypeFileLink.file_definition)
```
- `RecordTypeRepository`: `_file_links_eager_load()` helper, applied to `get()`, `get_all()`, `list_all()`, `find()`
- `RecordRepository`: `_record_type_with_files()` helper chains through `Record.record_type`
- `RecordRepository`: `_record_file_links_eager_load()` helper for `Record.file_links → FileDefinition`
- `FileDefinitionRepository`: `get_or_create()`, `bulk_upsert()` for M2M link management

### BaseRepository.update() and Relationships

`BaseRepository.update()` accepts an optional `options` parameter for eager loading:
```python
await repo.update(entity, data, options=[selectinload(Model.relationship)])
```

Without `options`, `update()` uses `session.refresh()` which does NOT load relationships.
Accessing a relationship after plain `update()` causes `MissingGreenlet` in async contexts.

Alternatives when you don't use `options`:
- Re-fetch via `repo.get()` (if the repo overrides `get()` with eager loading)
- Use domain-specific methods like `update_status()`, `update_data()` that handle this internally

### M2M Link Lifecycle

Pattern for updating M2M relationships (e.g. file_links on RecordType):
1. Delete existing links → `session.flush()`
2. Create new link objects → `session.add()` each
3. `session.commit()`
4. Re-fetch parent with `selectinload` (via `repo.get()` or `update(options=...)`)

## N+1 Prevention

For aggregate queries, batch-fetch related entities:
```python
names = [name for name, _ in rows]
types = await session.execute(select(RecordType).where(RecordType.name.in_(names)))
type_map = {rt.name: rt for rt in types.scalars()}
```
