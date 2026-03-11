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

## RecordRepository Specialized Methods

Beyond `BaseRepository`, `RecordRepository` has:

| Method | Description |
|---|---|
| `get_with_record_type(id)` | Eager-loads `record_type` |
| `get_with_relations(id)` | Eager-loads patient, study, series, record_type |
| `get_all_with_relations(skip, limit)` | All records with full eager load |
| `find_by_criteria(criteria)` | Complex search via `RecordSearchCriteria` |
| `find_by_user(user_id, …)` | Records for specific user |
| `find_pending_by_user(user_id)` | Pending/inwork records |
| `create_with_relations(record)` | Create with eager load after commit |
| `update_status(id, status)` | Status transition with validation |
| `update_data(id, data, new_status)` | Update data and optionally status |
| `set_files(record, matched_files)` | Create `RecordFileLink` rows; builds fd_map internally from eager-loaded `file_links` |
| `update_checksums(record, checksums)` | Update checksum on existing `RecordFileLink` rows |
| `assign_user(id, user_id)` | Assign record to user |
| `ensure_user_assigned(id, user_id)` | Assign user only if record has no user yet |
| `claim_record(id, user_id)` | Claim unassigned record |
| `bulk_update_status(ids, status)` | Batch status update |
| `validate_parent_record(parent_id, child_type)` | Validate parent record type matches child's `parent_type_name` |
| `check_constraints(record, record_type)` | Validate RecordType constraints |
| `get_available_type_counts(user_id)` | Dict of available RecordType → count (batch-loaded to avoid N+1) |
| `get_status_counts()` | Global status counts |
| `get_per_type_status_counts()` | Status counts per type |
| `get_per_type_unique_users()` | Unique user count per type |

## Record Invalidation (record_repository.py)

`invalidate_record(record_id, mode, source_record_id=None, reason=None)`:
- **hard**: `status` → `pending`, append reason to `context_info` (keeps `user_id`)
- **soft**: only append reason to `context_info` (status unchanged)
- Default reason: `"Invalidated by record #{source_record_id}"`
- `context_info` is appended (newline-separated), never overwritten

## PatientRepository: auto_id Generation

`PatientRepository.create()` overrides the base `create()` to auto-assign `auto_id = MAX(auto_id) + 1`
when `entity.auto_id is None`. Retries up to 3 times on `IntegrityError` (UNIQUE conflict).
If `auto_id` is explicitly provided, falls through to `super().create()` without the MAX query.

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
