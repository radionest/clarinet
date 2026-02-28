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
| `create(entity)` | `ModelT` | — (commits + refreshes) |
| `create_many(entities)` | `list[ModelT]` | — |
| `update(entity, update_data)` | `ModelT` | — |
| `delete(entity)` | `None` | — |
| `delete_by_id(id)` | `bool` | returns `False` |

Also: `build_query()`, `execute_query(query)`, `refresh(entity)`.

## Domain Exceptions Only

Repositories raise exceptions from `src.exceptions.domain`:

| Category | Exceptions |
|---|---|
| Not found | `EntityNotFoundError`, `UserNotFoundError`, `PatientNotFoundError`, `StudyNotFoundError`, `SeriesNotFoundError`, `RecordNotFoundError`, `RecordTypeNotFoundError`, `RoleNotFoundError` |
| Already exists | `EntityAlreadyExistsError`, `UserAlreadyExistsError`, `PatientAlreadyExistsError`, `RecordAlreadyExistsError`, etc. |
| Auth | `AuthenticationError`, `AuthorizationError`, `InvalidCredentialsError` |
| Business rules | `BusinessRuleViolationError`, `RecordConstraintViolationError`, `AlreadyAnonymizedError` |
| DB | `DatabaseError`, `DatabaseIntegrityError` |
| File/Storage | `StorageError`, `FileNotFoundError`, `FileSchemaError` |

**NEVER** import from `src.exceptions.http` in repositories — that's API layer only.
Exception handlers in `src/api/exception_handlers.py` convert domain → HTTP.

## NULL Comparison Gotcha

**WRONG:** `Record.user_id == None` (doesn't generate correct SQL)
**RIGHT:** `col(Record.user_id).is_(None)` / `.is_not(None)`

## RecordSearchCriteria (record_repository.py)

Special sentinel values for `anon_series_uid` / `anon_study_uid`:
- `"Null"` → `WHERE col.is_(None)` (find un-anonymized)
- `"*"` → `WHERE col.is_not(None)` (find anonymized)
- `None` → no filter applied

Other fields: `status`, `name` (record_type), `user_id`, `wo_user` (unassigned),
`random_one`, `data_queries: list[RecordFindResult]` (JSON field queries with operators).

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
| `update_data(id, data)` | Partial JSON data update |
| `assign_user(id, user_id)` | Assign record to user |
| `claim_record(id, user_id)` | Claim unassigned record |
| `bulk_update_status(ids, status)` | Batch status update |
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

## Eager Loading

Use `selectinload()` to avoid N+1. Nested loading for deep relations:
```python
selectinload(Patient.studies).selectinload(Study.series)
```

## N+1 Prevention

For aggregate queries, batch-fetch related entities:
```python
names = [name for name, _ in rows]
types = await session.execute(select(RecordType).where(RecordType.name.in_(names)))
type_map = {rt.name: rt for rt in types.scalars()}
```
