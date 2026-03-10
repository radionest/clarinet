"""
Async record router for the Clarinet framework.

This module provides async API endpoints for managing records, record types, and record submissions.
Formerly known as task router.
"""

from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from clarinet.api.auth_config import current_active_user
from clarinet.api.dependencies import (
    CurrentUserDep,
    FileDefinitionRepositoryDep,
    PaginationDep,
    RecordRepositoryDep,
    RecordServiceDep,
    RecordTypeRepositoryDep,
    SeriesRepositoryDep,
    SessionDep,
    require_mutable_config,
)
from clarinet.config.toml_exporter import (
    delete_record_type_files,
    export_data_schema_sidecar,
    export_record_type_to_toml,
)
from clarinet.exceptions import CONFLICT, NOT_FOUND
from clarinet.exceptions.domain import ValidationError
from clarinet.models import (
    Record,
    RecordCreate,
    RecordFindResult,
    RecordOptional,
    RecordRead,
    RecordStatus,
    RecordType,
    RecordTypeCreate,
    RecordTypeFind,
    RecordTypeOptional,
    RecordTypeRead,
    User,
)
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.file_validation import FileValidationResult, validate_record_files
from clarinet.services.schema_hydration import hydrate_schema
from clarinet.types import RecordData
from clarinet.utils.file_checksums import checksums_changed, compute_checksums
from clarinet.utils.file_link_sync import sync_file_links
from clarinet.utils.validation import validate_json_by_schema


class FileCheckResult(SQLModel):
    """Response model for file check endpoint."""

    changed_files: list[str]
    checksums: dict[str, str]


router = APIRouter(
    tags=["Records"],
    responses={
        404: {"description": "Not found"},
        409: {"description": "Conflict"},
    },
    dependencies=[Depends(current_active_user)],
)


# Helpers


async def validate_record_data(
    record: Record, data: RecordData, session: AsyncSession
) -> RecordData:
    """Validate record data against its hydrated record type schema.

    Resolves ``x-options`` markers to ``oneOf`` before validation.
    Record must have record_type relation loaded.

    Args:
        record: Record with record_type loaded.
        data: Data to validate.
        session: Async DB session for schema hydration.

    Returns:
        Validated data.

    Raises:
        ValidationError: If data does not match schema.
    """
    if record.record_type.data_schema:
        hydrated = await hydrate_schema(record.record_type.data_schema, record, session)
        validate_json_by_schema(data, hydrated)

    return data


# Record Type Endpoints


@router.get("/types", response_model=list[RecordTypeRead])
async def get_all_record_types(
    repo: RecordTypeRepositoryDep,
) -> list[RecordTypeRead]:
    """Get all record types."""
    types = await repo.list_all()
    return [RecordTypeRead.model_validate(rt) for rt in types]


@router.post("/types/find", response_model=list[RecordTypeRead])
async def find_record_type(
    find_query: RecordTypeFind,
    repo: RecordTypeRepositoryDep,
) -> list[RecordTypeRead]:
    """Find record types by criteria."""
    types = await repo.find(find_query)
    return [RecordTypeRead.model_validate(rt) for rt in types]


@router.post(
    "/types",
    response_model=RecordTypeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_mutable_config)],
)
async def add_record_type(
    record_type: RecordTypeCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: RecordTypeRepositoryDep,
    fd_repo: FileDefinitionRepositoryDep,
    session: SessionDep,
    constrain_unique_names: bool = True,
) -> RecordTypeRead:
    """Create a new record type.

    In TOML mode, exports the created RecordType to a TOML file.
    """
    # Extract file definitions before creating ORM object
    file_defs = record_type.file_registry or []

    # Validate data schema if present
    if record_type.data_schema is not None:
        try:
            Draft202012Validator.check_schema(record_type.data_schema)
        except SchemaError as e:
            raise ValidationError(f"Data schema is invalid: {e}") from e

    if constrain_unique_names:
        await repo.ensure_unique_name(record_type.name)

    # Validate parent type (DAG check)
    if record_type.parent_type_name is not None:
        await repo.validate_parent_type(record_type.name, record_type.parent_type_name)

    # Create RecordType without file_registry (it's M2M, not a column)
    create_data = record_type.model_dump(exclude={"file_registry"})
    new_record_type = RecordType(**create_data)
    new_record_type.file_links = []
    session.add(new_record_type)
    await session.flush()

    # Create file links
    if file_defs:
        await sync_file_links(new_record_type, file_defs, fd_repo, session)

    await session.commit()

    # Re-fetch with eager loading
    result = await repo.get(new_record_type.name)

    # Export to TOML in background (TOML mode only)
    if getattr(request.app.state, "config_mode", "toml") == "toml":
        folder = Path(getattr(request.app.state, "config_tasks_path", "./tasks/"))
        background_tasks.add_task(export_record_type_to_toml, result, folder)
        background_tasks.add_task(export_data_schema_sidecar, result, folder)

    return RecordTypeRead.model_validate(result)


@router.patch(
    "/types/{record_type_id}",
    response_model=RecordTypeRead,
    dependencies=[Depends(require_mutable_config)],
)
async def update_record_type(
    record_type_id: str,
    record_type_update: RecordTypeOptional,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: RecordTypeRepositoryDep,
    fd_repo: FileDefinitionRepositoryDep,
    session: SessionDep,
) -> RecordTypeRead:
    """Update an existing record type.

    In TOML mode, exports the updated RecordType to a TOML file.
    """
    record_type = await repo.get(record_type_id)

    # Validate data schema if present
    if record_type_update.data_schema is not None:
        try:
            Draft202012Validator.check_schema(record_type_update.data_schema)
        except SchemaError as e:
            raise ValidationError(f"Data schema is invalid: {e}") from e

    # Validate parent type if being updated (DAG check)
    if "parent_type_name" in record_type_update.model_fields_set:
        await repo.validate_parent_type(record_type_id, record_type_update.parent_type_name)

    # Extract file_registry before model_dump to preserve FileDefinitionRead objects
    file_defs_set = "file_registry" in record_type_update.model_fields_set
    file_defs = record_type_update.file_registry if file_defs_set else None
    update_data = record_type_update.model_dump(
        exclude_unset=True,
        exclude_none=True,
        exclude={"file_registry"},
    )

    if update_data:
        await repo.update(record_type, update_data)

    # Sync file links if file_registry was explicitly provided
    if file_defs is not None:
        current = await repo.get(record_type_id)
        await sync_file_links(current, file_defs, fd_repo, session, clear_existing=True)
        await session.commit()

    # Always re-fetch with eager loading for response serialization
    result = await repo.get(record_type_id)

    # Export to TOML in background (TOML mode only)
    if getattr(request.app.state, "config_mode", "toml") == "toml":
        folder = Path(getattr(request.app.state, "config_tasks_path", "./tasks/"))
        background_tasks.add_task(export_record_type_to_toml, result, folder)
        background_tasks.add_task(export_data_schema_sidecar, result, folder)

    return RecordTypeRead.model_validate(result)


@router.get("/types/{record_type_id}", response_model=RecordTypeRead)
async def get_record_type(
    record_type_id: str,
    repo: RecordTypeRepositoryDep,
) -> RecordTypeRead:
    """Get a record type by ID."""
    rt = await repo.get(record_type_id)
    return RecordTypeRead.model_validate(rt)


@router.delete(
    "/types/{record_type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_mutable_config)],
)
async def delete_record_type(
    record_type_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: RecordTypeRepositoryDep,
) -> None:
    """Delete a record type.

    In TOML mode, removes the corresponding TOML and schema files.
    """
    record_type = await repo.get(record_type_id)
    name = record_type.name
    await repo.delete(record_type)

    # Delete TOML files in background (TOML mode only)
    if getattr(request.app.state, "config_mode", "toml") == "toml":
        folder = Path(getattr(request.app.state, "config_tasks_path", "./tasks/"))
        background_tasks.add_task(delete_record_type_files, name, folder)


# Record Endpoints


@router.get("/", response_model=list[RecordRead])
async def get_all_records(repo: RecordRepositoryDep) -> Sequence[Record]:
    """Get all records with relations loaded."""
    return await repo.get_all_with_relations()


@router.get("/my", response_model=list[RecordRead])
async def get_my_records(
    repo: RecordRepositoryDep,
    user: User = Depends(current_active_user),
) -> Sequence[Record]:
    """Get all records assigned to the current user with relations loaded."""
    return await repo.find_by_user(user.id)


@router.get("/my/pending", response_model=list[RecordRead])
async def get_my_pending_records(
    repo: RecordRepositoryDep,
    user: User = Depends(current_active_user),
) -> Sequence[Record]:
    """Get all pending records assigned to the current user with relations loaded."""
    return await repo.find_pending_by_user(user.id)


@router.get("/available_types", response_model=dict[str, int])
async def get_my_available_record_types(
    repo: RecordRepositoryDep,
    user: User = Depends(current_active_user),
) -> dict[str, int]:
    """Get all record types available to the current user with record counts."""
    type_counts = await repo.get_available_type_counts(user.id)
    return {rt.name: count for rt, count in type_counts.items()}


@router.get("/{record_id}", response_model=RecordRead)
async def get_record(
    record_id: int,
    repo: RecordRepositoryDep,
) -> RecordRead:
    """Get a record by ID."""
    record = await repo.get_with_relations(record_id)
    return RecordRead.model_validate(record)


@router.get("/{record_id}/schema", response_class=JSONResponse)
async def get_hydrated_schema(
    record_id: int,
    repo: RecordRepositoryDep,
    session: SessionDep,
) -> JSONResponse:
    """Return the record type's JSON Schema with ``x-options`` resolved.

    Args:
        record_id: ID of the record.

    Returns:
        Hydrated JSON Schema dict.

    Raises:
        NOT_FOUND: If the record or its data_schema does not exist.
    """
    record = await repo.get_with_relations(record_id)
    schema = record.record_type.data_schema
    if not schema:
        raise NOT_FOUND.with_context("Record type has no data schema")
    hydrated = await hydrate_schema(schema, record, session)
    return JSONResponse(content=hydrated)


async def check_record_constraints(
    new_record: RecordCreate,
    repo: RecordRepositoryDep,
) -> None:
    """Check if a record can be added based on constraints."""
    await repo.check_constraints(
        new_record.record_type_name, new_record.series_uid, new_record.study_uid
    )


@router.post(
    "/",
    response_model=RecordRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(check_record_constraints)],
)
async def add_record(
    new_record: RecordCreate,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
) -> Record:
    """Create a new record.

    If the RecordType defines required input files and they are not yet
    present, the record is created with ``blocked`` status instead of
    raising a validation error.
    """
    # Validate and inherit from parent record if specified
    if new_record.parent_record_id is not None:
        parent = await repo.validate_parent_record(
            new_record.parent_record_id, new_record.record_type_name
        )
        # Inherit user_id from parent if not explicitly set
        if new_record.user_id is None:
            new_record.user_id = parent.user_id

    record = Record(**new_record.model_dump())
    return await service.create_record(record)


@router.patch("/{record_id}/status", response_model=RecordRead)
async def update_record_status(
    record_id: int,
    record_status: RecordStatus,
    service: RecordServiceDep,
) -> Record:
    """Update a record's status."""
    record, _ = await service.update_status(record_id, record_status)
    return record


@router.patch("/{record_id}/user", response_model=RecordRead)
async def assign_record_to_user(
    record_id: int,
    user_id: UUID,
    service: RecordServiceDep,
) -> Record:
    """Assign a record to a user."""
    record, _ = await service.assign_user(record_id, user_id)
    return record


@router.post("/{record_id}/data", response_model=RecordRead)
async def submit_record_data(
    record_id: int,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
    session: SessionDep,
    current_user: CurrentUserDep,
    data: RecordData = Body(),
) -> Record:
    """Submit data for a record."""
    record = await repo.get_with_relations(record_id)

    if record.status == RecordStatus.blocked:
        raise CONFLICT.with_context("Record is blocked — required input files are missing.")

    if record.status == RecordStatus.finished:
        raise CONFLICT.with_context("Record already finished. Use PATCH to update the record data.")

    validated_data = await validate_record_data(record, data, session)

    # Validate input files if defined (raise on missing required files)
    record_read = RecordRead.model_validate(record)
    file_result = await validate_record_files(record_read, raise_on_invalid=True)

    if file_result and file_result.matched_files:
        await repo.set_files(record, file_result.matched_files)

    # Update record data, set finished status (auto-assign user if missing)
    record, _ = await service.submit_data(
        record_id, validated_data, RecordStatus.finished, user_id=current_user.id
    )

    return record


@router.patch("/{record_id}/data", response_model=RecordRead)
async def update_record_data(
    record_id: int,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
    session: SessionDep,
    data: RecordData = Body(),
) -> Record:
    """Update a record's data."""
    record = await repo.get_with_record_type(record_id)

    if record.status != RecordStatus.finished:
        raise CONFLICT.with_context("Record is not finished yet. Use POST to submit record data.")

    validated_data = await validate_record_data(record, data, session)
    updated, _ = await service.update_data(record_id, validated_data)

    return updated


@router.patch("/{record_id}", response_model=RecordRead)
async def update_record(
    record_id: int,
    record_update: RecordOptional,
    repo: RecordRepositoryDep,
) -> Record:
    """Update a record with partial data.

    Currently supports: viewer_study_uids.
    Does NOT trigger RecordFlow (use PATCH /status for workflow transitions).
    """
    update_data = record_update.model_dump(exclude_unset=True)
    if not update_data:
        return await repo.get_with_relations(record_id)
    return await repo.update_fields(record_id, update_data)


@router.post("/{record_id}/validate-files")
async def validate_files_endpoint(
    record_id: int,
    repo: RecordRepositoryDep,
) -> FileValidationResult:
    """Validate input files for a record without saving the result.

    This endpoint checks if the required input files exist in the record's
    working folder without modifying the record.

    Args:
        record_id: ID of the record to validate files for

    Returns:
        FileValidationResult with validation status and matched files
    """
    record = await repo.get_with_relations(record_id)

    record_read = RecordRead.model_validate(record)
    result = await validate_record_files(record_read)
    if result is None:
        return FileValidationResult(valid=True)

    return result


@router.post("/{record_id}/check-files", response_model=FileCheckResult)
async def check_record_files(
    record_id: int,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
) -> FileCheckResult:
    """Compute current file checksums, compare with stored, trigger invalidation if changed.

    For ``blocked`` records, this endpoint also checks whether the required
    input files have appeared and auto-transitions to ``pending`` if so.

    Args:
        record_id: ID of the record to check files for

    Returns:
        FileCheckResult with changed files and current checksums
    """
    record = await repo.get_with_relations(record_id)
    record_read = RecordRead.model_validate(record)

    # Auto-unblock: if record is blocked, check whether input files are now present
    if record.status == RecordStatus.blocked:
        file_result = await validate_record_files(record_read)
        if file_result is not None and file_result.valid:
            if file_result.matched_files:
                await repo.set_files(record, file_result.matched_files)
            record, _ = await service.update_status(record_id, RecordStatus.pending)
            record_read = RecordRead.model_validate(record)
        else:
            # Still blocked — return early with empty result
            return FileCheckResult(changed_files=[], checksums={})

    new_checksums = await compute_checksums(
        record_read.record_type.file_registry or [],
        record_read,
        Path(record_read.working_folder),
    )
    old_checksums = {
        link.name: link.checksum for link in (record_read.file_links or []) if link.checksum
    }
    changed = checksums_changed(old_checksums, new_checksums)

    await repo.update_checksums(record, new_checksums)

    if changed:
        await service.notify_file_change(record)

    return FileCheckResult(changed_files=list(changed), checksums=new_checksums)


@router.post("/{record_id}/invalidate", response_model=RecordRead)
async def invalidate_record(
    record_id: int,
    repo: RecordRepositoryDep,
    mode: str = Body(default="hard"),
    source_record_id: int | None = Body(default=None),
    reason: str | None = Body(default=None),
) -> Record:
    """Invalidate a record.

    Hard mode resets status to pending (keeps user assignment).
    Soft mode only appends the reason to context_info.

    Args:
        record_id: ID of the record to invalidate.
        mode: "hard" or "soft".
        source_record_id: ID of the record that triggered invalidation.
        reason: Human-readable reason for invalidation.

    Returns:
        Updated record.
    """
    return await repo.invalidate_record(
        record_id=record_id,
        mode=mode,
        source_record_id=source_record_id,
        reason=reason,
    )


@router.post("/find", response_model=list[RecordRead])
async def find_records(
    pagination: PaginationDep,
    repo: RecordRepositoryDep,
    find_queries: list[RecordFindResult] = Body(default=[]),
    patient_id: str | None = None,
    patient_anon_id: str | None = None,
    series_uid: str | None = None,
    anon_series_uid: str | None = None,
    study_uid: str | None = None,
    anon_study_uid: str | None = None,
    user_id: UUID | None = None,
    record_type_name: str | None = None,
    record_status: RecordStatus | None = None,
    wo_user: bool | None = None,
    random_one: bool = False,
) -> Sequence[Record]:
    """Find records by various criteria."""
    criteria = RecordSearchCriteria(
        patient_id=patient_id,
        patient_anon_id=patient_anon_id,
        series_uid=series_uid,
        anon_series_uid=anon_series_uid,
        study_uid=study_uid,
        anon_study_uid=anon_study_uid,
        user_id=user_id,
        record_type_name=record_type_name,
        record_status=record_status,
        wo_user=wo_user,
        random_one=random_one,
        data_queries=find_queries,
    )
    return await repo.find_by_criteria(criteria, skip=pagination.skip, limit=pagination.limit)


@router.patch("/bulk/status", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_update_record_status(
    record_ids: list[int],
    new_status: RecordStatus,
    service: RecordServiceDep,
) -> None:
    """Update status for multiple records at once."""
    await service.bulk_update_status(record_ids, new_status)


# Dependency functions (used by other parts of the application)


async def assign_user_to_record(
    record_id: int,
    repo: RecordRepositoryDep,
    user: User = Depends(current_active_user),
) -> Record:
    """Assign the current user to a record."""
    return await repo.claim_record(record_id, user.id)


async def add_demo_records_for_user(
    user: User,
    repo: RecordRepositoryDep,
    series_repo: SeriesRepositoryDep,
    record_type_repo: RecordTypeRepositoryDep,
) -> None:
    """Add demo records for a new user."""
    series = await series_repo.get_random()

    record_types = await record_type_repo.find(RecordTypeFind(name="demo"))

    if not record_types:
        raise NOT_FOUND.with_context("No demo record types found")

    # Create a record for each demo record type
    records: list[Record] = []
    for record_type in record_types:
        if record_type.level not in ("SERIES", "STUDY"):
            continue

        new_record = RecordCreate(
            status=RecordStatus.pending,
            user_id=user.id,
            study_uid=series.study_uid,
            patient_id=series.study.patient_id,
            record_type_name=record_type.name,
            series_uid=series.series_uid if record_type.level == "SERIES" else None,
        )
        records.append(Record(**new_record.model_dump()))

    if records:
        await repo.create_many(records)
