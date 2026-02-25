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
- `EntityNotFoundError`, `PatientNotFoundError`, `RecordNotFoundError`, etc.

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
