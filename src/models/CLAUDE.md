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

`Record` has computed fields that **silently return `None`** if relationships aren't loaded:
- `working_folder` — needs `record_type` (checks `.level`)
- `slicer_args_formatted` — needs `record_type` (checks `.slicer_script_args`)
- `radiant` — needs `study`, `patient`

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

## JSON Columns

Use `sa_column=Column(JSON)` for dict/list fields:
- `Record.data` — dynamic data per RecordType.data_schema
- `Record.files` — `dict[str, str]` mapping FileDefinition.name → filename
- `RecordType.slicer_script_args`, `data_schema`, `input_files`, `output_files`

## Custom Types

- `DicomUID` = `Annotated[str, StringConstraints(pattern=r"^[0-9\.]*$", min_length=5, max_length=64)]`

## Frontend Consistency

Backend models (`src/models/`) and frontend models (`src/frontend/src/api/`) must stay in sync.
When changing `{Model}Read`, `{Model}Create`, or `{Model}Optional` schemas — update the
corresponding Gleam types in:
- `src/frontend/src/api/models.gleam` — shared data models
- `src/frontend/src/api/types.gleam` — type definitions
- `src/frontend/src/api/records.gleam`, `studies.gleam`, `users.gleam`, `admin.gleam` — API-specific types

Check field names, types, and optionality match between Python schemas and Gleam types.

## Forward References

Models use `TYPE_CHECKING` to avoid circular imports. Schemas with forward refs
need `model_rebuild()` — called in `app.py` lifespan (e.g. `SeriesFind.model_rebuild()`).
