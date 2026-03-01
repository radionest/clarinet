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
from jsonschema import Draft202012Validator, SchemaError

from src.api.auth_config import current_active_user
from src.api.dependencies import (
    PaginationDep,
    RecordRepositoryDep,
    RecordTypeRepositoryDep,
    SeriesRepositoryDep,
)
from src.exceptions import CONFLICT, NOT_FOUND
from src.exceptions.domain import ValidationError
from src.models import (
    Record,
    RecordCreate,
    RecordFindResult,
    RecordRead,
    RecordStatus,
    RecordType,
    RecordTypeCreate,
    RecordTypeFind,
    RecordTypeOptional,
    User,
)
from src.repositories.record_repository import RecordSearchCriteria
from src.services.file_validation import FileValidationResult, FileValidator
from src.types import RecordData
from src.utils.logger import logger
from src.utils.validation import validate_json_by_schema

router = APIRouter(
    tags=["Records"],
    responses={
        404: {"description": "Not found"},
        409: {"description": "Conflict"},
    },
    dependencies=[Depends(current_active_user)],
)


# Helpers


def trigger_recordflow(
    request: Request,
    background_tasks: BackgroundTasks,
    record: Record,
    old_status: RecordStatus | None = None,
) -> None:
    """Trigger RecordFlow engine in background if enabled.

    Args:
        request: FastAPI request to access app state.
        background_tasks: FastAPI background tasks.
        record: Record that changed.
        old_status: Previous status. If provided, triggers status change handler;
            otherwise triggers data update handler.
    """
    engine = getattr(request.app.state, "recordflow_engine", None)
    if not engine:
        return
    record_read = RecordRead.model_validate(record)
    if old_status is not None:
        background_tasks.add_task(engine.handle_record_status_change, record_read, old_status)
    else:
        background_tasks.add_task(engine.handle_record_data_update, record_read)


def validate_record_files(record: RecordRead) -> FileValidationResult | None:
    """Validate input files for a record.

    Accepts ``RecordRead`` (Pydantic) because ``working_folder`` and other
    computed fields are defined on ``RecordRead``, not on the ORM ``Record``.
    Callers should convert via ``RecordRead.model_validate(record)`` first.

    Args:
        record: RecordRead instance with all relations populated

    Returns:
        FileValidationResult if validation was performed, None if no input_files defined
    """
    if not record.record_type.input_files:
        return None

    working_folder = record.working_folder
    if working_folder is None:
        logger.warning(
            f"Cannot resolve working_folder for record {record.id}, skipping file validation"
        )
        return None

    directory = Path(working_folder)
    validator = FileValidator(record.record_type)
    result = validator.validate_input_files(record, directory)
    if not result.valid:
        errors = "; ".join(f"{e.file_name}: {e.message}" for e in result.errors)
        raise ValidationError(f"File validation failed: {errors}")
    return result


def validate_record_data(record: Record, data: RecordData) -> RecordData:
    """Validate record data against its record type schema.

    Record must have record_type relation loaded.

    Args:
        record: Record with record_type loaded
        data: Data to validate

    Returns:
        Validated data

    Raises:
        ValidationError: If data does not match schema
    """
    if record.record_type.data_schema:
        validate_json_by_schema(data, record.record_type.data_schema)

    return data


# Record Type Endpoints


@router.get("/types", response_model=list[RecordType])
async def get_all_record_types(
    repo: RecordTypeRepositoryDep,
) -> Sequence[RecordType]:
    """Get all record types."""
    return await repo.list_all()


@router.post("/types/find", response_model=list[RecordType])
async def find_record_type(
    find_query: RecordTypeFind,
    repo: RecordTypeRepositoryDep,
) -> Sequence[RecordType]:
    """Find record types by criteria."""
    return await repo.find(find_query)


@router.post("/types", response_model=RecordType, status_code=status.HTTP_201_CREATED)
async def add_record_type(
    record_type: RecordTypeCreate,
    repo: RecordTypeRepositoryDep,
    constrain_unique_names: bool = True,
) -> RecordType:
    """Create a new record type."""
    new_record_type = RecordType.model_validate(record_type)

    # Validate data schema if present
    if new_record_type.data_schema is not None:
        try:
            Draft202012Validator.check_schema(new_record_type.data_schema)
        except SchemaError as e:
            raise ValidationError(f"Data schema is invalid: {e}") from e

    if constrain_unique_names:
        await repo.ensure_unique_name(record_type.name)

    return await repo.create(new_record_type)


@router.patch("/types/{record_type_id}", response_model=RecordType)
async def update_record_type(
    record_type_id: str,
    record_type_update: RecordTypeOptional,
    repo: RecordTypeRepositoryDep,
) -> RecordType:
    """Update an existing record type."""
    record_type = await repo.get(record_type_id)

    # Validate data schema if present
    if record_type_update.data_schema is not None:
        try:
            Draft202012Validator.check_schema(record_type_update.data_schema)
        except SchemaError as e:
            raise ValidationError(f"Data schema is invalid: {e}") from e

    update_data = record_type_update.model_dump(exclude_unset=True, exclude_none=True)
    return await repo.update(record_type, update_data)


@router.get("/types/{record_type_id}", response_model=RecordType)
async def get_record_type(
    record_type_id: str,
    repo: RecordTypeRepositoryDep,
) -> RecordType:
    """Get a record type by ID."""
    return await repo.get(record_type_id)


@router.delete("/types/{record_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record_type(
    record_type_id: str,
    repo: RecordTypeRepositoryDep,
) -> None:
    """Delete a record type."""
    record_type = await repo.get(record_type_id)
    await repo.delete(record_type)


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


@router.get("/available_types", response_model=dict[RecordType, int])
async def get_my_available_record_types(
    repo: RecordRepositoryDep,
    user: User = Depends(current_active_user),
) -> dict[RecordType, int]:
    """Get all record types available to the current user with record counts."""
    return await repo.get_available_type_counts(user.id)


@router.get("/{record_id}", response_model=RecordRead)
async def get_record(
    record_id: int,
    repo: RecordRepositoryDep,
) -> RecordRead:
    """Get a record by ID."""
    record = await repo.get_with_relations(record_id)
    return RecordRead.model_validate(record)


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
) -> Record:
    """Create a new record."""
    record = Record(**new_record.model_dump())
    record = await repo.create_with_relations(record)

    # Validate input files if defined
    record_read = RecordRead.model_validate(record)
    file_result = validate_record_files(record_read)
    if file_result and file_result.matched_files:
        await repo.set_files(record, file_result.matched_files)
        return await repo.get_with_relations(record.id)  # type: ignore[arg-type]

    return record


@router.patch("/{record_id}/status", response_model=RecordRead)
async def update_record_status(
    record_id: int,
    record_status: RecordStatus,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: RecordRepositoryDep,
) -> Record:
    """Update a record's status."""
    record, old_status = await repo.update_status(record_id, record_status)

    # Trigger RecordFlow if enabled and status changed
    if old_status != record_status:
        trigger_recordflow(request, background_tasks, record, old_status)

    return record


@router.patch("/{record_id}/user", response_model=RecordRead)
async def assign_record_to_user(
    record_id: int,
    user_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: RecordRepositoryDep,
) -> Record:
    """Assign a record to a user."""
    record, old_status = await repo.assign_user(record_id, user_id)

    # Trigger RecordFlow if enabled and status changed
    if old_status != record.status:
        trigger_recordflow(request, background_tasks, record, old_status)

    return record


@router.post("/{record_id}/data", response_model=RecordRead)
async def submit_record_data(
    record_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: RecordRepositoryDep,
    data: RecordData = Body(),
) -> Record:
    """Submit data for a record."""
    record = await repo.get_with_relations(record_id)

    if record.status == RecordStatus.finished:
        raise CONFLICT.with_context("Record already finished. Use PATCH to update the record data.")

    # Validate data against schema
    validated_data = validate_record_data(record, data)

    # Validate input files if defined
    record_read = RecordRead.model_validate(record)
    file_result = validate_record_files(record_read)
    files: dict[str, str] | None = None
    if file_result and file_result.matched_files:
        files = file_result.matched_files

    # Update record data, set finished status
    record, old_status = await repo.update_data(
        record_id, validated_data, new_status=RecordStatus.finished, files=files
    )

    # Trigger RecordFlow if enabled
    trigger_recordflow(request, background_tasks, record, old_status)

    return record


@router.patch("/{record_id}/data", response_model=RecordRead)
async def update_record_data(
    record_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    repo: RecordRepositoryDep,
    data: RecordData = Body(),
) -> Record:
    """Update a record's data."""
    record = await repo.get_with_record_type(record_id)

    if record.status != RecordStatus.finished:
        raise CONFLICT.with_context("Record is not finished yet. Use POST to submit record data.")

    validated_data = validate_record_data(record, data)
    updated, _ = await repo.update_data(record_id, validated_data)

    # Trigger RecordFlow data update flows if enabled
    trigger_recordflow(request, background_tasks, updated)

    return updated


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
    result = validate_record_files(record_read)
    if result is None:
        return FileValidationResult(valid=True)

    return result


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
    repo: RecordRepositoryDep,
) -> None:
    """Update status for multiple records at once."""
    await repo.bulk_update_status(record_ids, new_status)


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
