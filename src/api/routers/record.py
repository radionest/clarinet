"""
Async record router for the Clarinet framework.

This module provides async API endpoints for managing records, record types, and record submissions.
Formerly known as task router.
"""

import random
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Request,
    status,
)
from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy import String as SQLString
from sqlalchemy import cast, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_, col, select

from src.api.auth_config import current_active_user
from src.api.dependencies import PaginationDep
from src.exceptions import CONFLICT, NOT_FOUND
from src.models import (
    Patient,
    Record,
    RecordCreate,
    RecordFindResult,
    RecordFindResultComparisonOperator,
    RecordRead,
    RecordStatus,
    RecordType,
    RecordTypeCreate,
    RecordTypeFind,
    RecordTypeOptional,
    Series,
    Study,
    User,
    UserRole,
)
from src.services.file_validation import FileValidationResult, FileValidator
from src.types import RecordData
from src.utils.database import get_async_session
from src.utils.logger import logger
from src.utils.validation import validate_json_by_schema

router = APIRouter(
    tags=["Records"],
    responses={
        404: {"description": "Not found"},
        409: {"description": "Conflict"},
    },
)


# File Validation Helper


async def validate_record_files(
    record: Record,
    session: AsyncSession,
) -> FileValidationResult | None:
    """Validate input files for a record.

    Args:
        record: Record to validate files for
        session: Database session for loading relationships

    Returns:
        FileValidationResult if validation was performed, None if no input_files defined
    """
    await session.refresh(record, ["record_type"])

    if not record.record_type.input_files:
        return None

    working_folder = record.working_folder
    if working_folder is None:
        return None

    directory = Path(working_folder)
    validator = FileValidator(record.record_type)
    return validator.validate_input_files(record, directory)


# Record Type Endpoints


@router.get("/types", response_model=list[RecordType])
async def get_all_record_types(
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[RecordType]:
    """Get all record types."""
    result = await session.execute(select(RecordType))
    return result.scalars().all()


@router.post("/types/find", response_model=list[RecordType])
async def find_record_type(
    find_query: RecordTypeFind,
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[RecordType]:
    """Find record types by criteria."""
    find_terms = find_query.model_dump(exclude_none=True)
    find_statement = select(RecordType)

    for find_key, find_value in find_terms.items():
        if find_key == "name":
            find_statement = find_statement.where(
                cast(RecordType.name, SQLString).like(f"%{find_value}%")
            )
        elif isinstance(find_value, list):
            find_statement = find_statement.where(getattr(RecordType, find_key) == find_value)
        else:
            find_statement = find_statement.where(getattr(RecordType, find_key) == find_value)

    result = await session.execute(find_statement)
    return result.scalars().all()


@router.post("/types", response_model=RecordType, status_code=status.HTTP_201_CREATED)
async def add_record_type(
    record_type: RecordTypeCreate,
    constrain_unique_names: bool = True,
    session: AsyncSession = Depends(get_async_session),
) -> RecordType:
    """Create a new record type."""
    new_record_type = RecordType.model_validate(record_type)

    # Validate data schema if present
    if new_record_type.data_schema is not None:
        try:
            Draft202012Validator.check_schema(new_record_type.data_schema)
        except SchemaError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Data schema is invalid",
            ) from e

    # Ensure record type name is unique if required
    if constrain_unique_names:
        existing = await session.execute(
            select(RecordType).where(RecordType.name == record_type.name).limit(1)
        )
        if existing.scalars().first():
            raise CONFLICT.with_context(
                f"There is already a record type with name '{record_type.name}'"
            )

    session.add(new_record_type)
    await session.commit()
    await session.refresh(new_record_type)
    return new_record_type


@router.patch("/types/{record_type_id}", response_model=RecordType)
async def update_record_type(
    record_type_id: int,
    record_type_update: RecordTypeOptional,
    session: AsyncSession = Depends(get_async_session),
) -> RecordType:
    """Update an existing record type."""
    record_type = await session.get(RecordType, record_type_id)
    if record_type is None:
        raise NOT_FOUND.with_context(f"Record type with ID {record_type_id} not found")

    # Validate data schema if present
    if record_type_update.data_schema is not None:
        try:
            Draft202012Validator.check_schema(record_type_update.data_schema)
        except SchemaError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Data schema is invalid",
            ) from e

    # Update fields
    update_data = record_type_update.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in update_data.items():
        setattr(record_type, field, value)

    await session.commit()
    await session.refresh(record_type)
    return record_type


@router.get("/types/{record_type_id}", response_model=RecordType)
async def get_record_type(
    record_type_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> RecordType:
    """Get a record type by ID."""
    record_type = await session.get(RecordType, record_type_id)
    if record_type is None:
        raise NOT_FOUND.with_context(f"Record type with ID {record_type_id} not found")
    return record_type


@router.delete("/types/{record_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record_type(
    record_type_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Delete a record type."""
    record_type = await session.get(RecordType, record_type_id)
    if record_type is None:
        raise NOT_FOUND.with_context(f"Record type with ID {record_type_id} not found")

    await session.delete(record_type)
    await session.commit()


# Record Endpoints


@router.get("/", response_model=list[Record])
async def get_all_records(session: AsyncSession = Depends(get_async_session)) -> Sequence[Record]:
    """Get all records."""
    result = await session.execute(select(Record))
    return result.scalars().all()


@router.get("/my", response_model=list[Record])
async def get_my_records(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[Record]:
    """Get all records assigned to the current user."""
    result = await session.execute(select(Record).where(Record.user_id == user.id))
    return result.scalars().all()


@router.get("/my/pending", response_model=list[RecordRead])
async def get_my_pending_records(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[Record]:
    """Get all pending records assigned to the current user."""
    result = await session.execute(
        select(Record).where(
            Record.user_id == user.id,
            and_(
                Record.status != RecordStatus.failed,
                Record.status != RecordStatus.finished,
                Record.status != RecordStatus.pause,
            ),
        )
    )
    return result.scalars().all()


@router.get("/available_types", response_model=dict[RecordType, int])
async def get_my_available_record_types(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict[RecordType, int]:
    """Get all record types available to the current user with record counts."""
    statement = (
        select(RecordType.name, func.count(col(Record.id)).label("record_count"))
        .join(Record)
        .join(UserRole)
        .where(UserRole.users.any(User.id == user.id))  # type: ignore[attr-defined]
        .where(Record.status == RecordStatus.pending)
        .group_by(col(RecordType.name))
    )
    result = await session.execute(statement)
    results = result.all()  # This returns tuples (id, count), not scalars

    return {
        record_type: record_count
        for record_type_id, record_count in results
        if (record_type := await session.get(RecordType, record_type_id)) is not None
    }


@router.get("/{record_id}", response_model=Record | RecordRead)
async def get_record(
    record_id: int,
    detailed: bool = False,
    session: AsyncSession = Depends(get_async_session),
) -> Record | RecordRead:
    """Get a record by ID."""
    record = await session.get(Record, record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record with ID {record_id} not found")

    if detailed:
        return RecordRead.model_validate(record)
    return record


async def check_record_constraints(
    new_record: RecordCreate,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Check if a record can be added based on constraints."""
    # Count existing records with same record type, series, and study
    query = (
        select(func.count(col(Record.id)))
        .join(RecordType)
        .where(
            RecordType.name == new_record.record_type_name,
            Record.series_uid == new_record.series_uid,
            Record.study_uid == new_record.study_uid,
        )
    )

    result = await session.execute(query)
    same_records_count = result.scalar_one()
    record_type = await session.get(RecordType, new_record.record_type_name)

    if record_type is None:
        raise NOT_FOUND.with_context(f"Record type with ID {new_record.record_type_name} not found")

    if record_type.max_users and same_records_count >= record_type.max_users:
        raise CONFLICT.with_context(
            f"The maximum users per record limit \
            ({same_records_count} of {record_type.max_users})\
            is reached"
        )


@router.post(
    "/",
    response_model=RecordRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(check_record_constraints)],
)
async def add_record(
    new_record: RecordCreate,
    session: AsyncSession = Depends(get_async_session),
) -> Record:
    """Create a new record."""
    record = Record(**new_record.model_dump())
    session.add(record)
    await session.commit()
    await session.refresh(record)

    # Validate input files if defined
    file_result = await validate_record_files(record, session)
    if file_result and not file_result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{"file_name": e.file_name, "error": e.message} for e in file_result.errors],
        )
    if file_result and file_result.matched_files:
        record.files = file_result.matched_files
        await session.commit()
        await session.refresh(record)

    return record


@router.patch("/{record_id}/status", response_model=RecordRead)
async def update_record_status(
    record_id: int,
    record_status: RecordStatus,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
) -> Record:
    """Update a record's status."""
    record = await session.get(Record, record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record with ID {record_id} not found")

    old_status = record.status
    record.status = record_status
    await session.commit()
    await session.refresh(record)

    # Trigger RecordFlow if enabled and status changed
    if old_status != record_status:
        recordflow_engine = getattr(request.app.state, "recordflow_engine", None)
        if recordflow_engine:
            record_read = RecordRead.model_validate(record)
            background_tasks.add_task(
                recordflow_engine.handle_record_status_change, record_read, old_status
            )

    return record


@router.patch("/{record_id}/user", response_model=Record)
async def assign_record_to_user(
    record_id: int,
    user_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> Record:
    """Assign a record to a user."""
    record = await session.get(Record, record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record with ID {record_id} not found")

    user = await session.get(User, user_id)
    if user is None:
        raise NOT_FOUND.with_context(f"User with ID {user_id} not found")

    record.user_id = user_id
    await session.commit()
    await session.refresh(record)
    return record


async def validate_record_data(
    record_id: int,
    data: RecordData,
    session: AsyncSession = Depends(get_async_session),
) -> RecordData:
    """Validate record data against its schema."""
    record = await session.get(Record, record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record with ID {record_id} not found")

    # Load record_type relationship
    await session.refresh(record, ["record_type"])

    # Validate against record type's data schema
    if record.record_type.data_schema:
        try:
            validate_json_by_schema(data, record.record_type.data_schema)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Data does not match schema: {e!s}",
            ) from e

    # Add additional validation here (e.g., Slicer validation)

    return data


@router.post("/{record_id}/data", response_model=RecordRead)
async def submit_record_data(
    record_id: int,
    data: RecordData,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
) -> Record:
    """Submit data for a record."""
    # Get and validate record
    record = await session.get(Record, record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record with ID {record_id} not found")

    if record.status == RecordStatus.finished:
        raise CONFLICT.with_context("Record already finished. Use PATCH to update the record data.")

    # Validate data
    validated_data = await validate_record_data(record_id, data, session)

    # Validate input files if defined
    file_result = await validate_record_files(record, session)
    if file_result and not file_result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{"file_name": e.file_name, "error": e.message} for e in file_result.errors],
        )
    if file_result and file_result.matched_files:
        record.files = file_result.matched_files

    # Update record
    old_status = record.status
    record.data = validated_data
    record.status = RecordStatus.finished
    await session.commit()
    await session.refresh(record)

    # Trigger RecordFlow if enabled
    recordflow_engine = getattr(request.app.state, "recordflow_engine", None)
    if recordflow_engine:
        record_read = RecordRead.model_validate(record)
        background_tasks.add_task(
            recordflow_engine.handle_record_status_change, record_read, old_status
        )

    return record


@router.patch("/{record_id}/data", response_model=RecordRead)
async def update_record_data(
    record_id: int,
    data: RecordData,
    session: AsyncSession = Depends(get_async_session),
) -> Record:
    """Update a record's data."""
    # Get and validate record
    record = await session.get(Record, record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record with ID {record_id} not found")

    if record.status != RecordStatus.finished:
        raise CONFLICT.with_context("Record is not finished yet. Use POST to submit record data.")

    # Validate data
    validated_data = await validate_record_data(record_id, data, session)

    # Update record
    record.data = validated_data
    await session.commit()
    await session.refresh(record)

    # Publish event or trigger background tasks here if needed

    return record


@router.post("/{record_id}/validate-files")
async def validate_files_endpoint(
    record_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> FileValidationResult:
    """Validate input files for a record without saving the result.

    This endpoint checks if the required input files exist in the record's
    working folder without modifying the record.

    Args:
        record_id: ID of the record to validate files for

    Returns:
        FileValidationResult with validation status and matched files
    """
    record = await session.get(Record, record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record with ID {record_id} not found")

    result = await validate_record_files(record, session)
    if result is None:
        return FileValidationResult(valid=True)

    return result


@router.post("/find", response_model=list[RecordRead])
async def find_records(
    pagination: PaginationDep,
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
    session: AsyncSession = Depends(get_async_session),
) -> Sequence[Record]:
    """Find records by various criteria."""
    find_statement = select(Record).join(RecordType)

    # Add filters for patient
    if patient_id:
        find_statement = find_statement.join(Study).join(Patient).where(Patient.id == patient_id)

    if (
        patient_anon_id and "_" in patient_anon_id
    ):  # Extract auto_id from anon_id format (e.g., "CLARINET_123" -> 123)
        auto_id = int(patient_anon_id.split("_")[1])
        find_statement = find_statement.join(Study).join(Patient).where(Patient.auto_id == auto_id)

    # Add filters for series
    if series_uid:
        find_statement = find_statement.where(Record.series_uid == series_uid)

    match anon_series_uid:
        case None:
            pass
        case "Null":
            find_statement = find_statement.join(Series).where(Series.anon_uid is None)
        case "*":
            find_statement = find_statement.join(Series).where(Series.anon_uid is not None)
        case _:
            find_statement = find_statement.join(Series).where(Series.anon_uid == anon_series_uid)

    # Add filters for study
    if study_uid:
        find_statement = find_statement.where(Record.study_uid == study_uid)

    match anon_study_uid:
        case None:
            pass
        case "Null":
            find_statement = find_statement.join(Study).where(Study.anon_uid is None)
        case "*":
            find_statement = find_statement.join(Study).where(Study.anon_uid is not None)
        case _:
            find_statement = find_statement.join(Study).where(Study.anon_uid == anon_study_uid)

    # Add user filters
    match wo_user:
        case None:
            pass
        case True:
            find_statement = find_statement.where(Record.user_id is None)
        case False:
            find_statement = find_statement.where(Record.user_id is not None)

    if user_id:
        find_statement = find_statement.where(Record.user_id == user_id)

    # Add record filters
    if record_status:
        find_statement = find_statement.where(Record.status == record_status)

    if record_type_name:
        find_statement = find_statement.where(RecordType.name == record_type_name)

    # Add data filters
    for query in find_queries:
        # Record.data is a JSON column, we need to handle it properly
        data_field = Record.data.op("->")(query.result_name).as_string()  # type: ignore[union-attr]
        match query.comparison_operator:
            case RecordFindResultComparisonOperator.eq:
                find_statement = find_statement.where(
                    data_field.cast(query.sql_type) == query.result_value
                )
            case RecordFindResultComparisonOperator.gt:
                find_statement = find_statement.where(
                    data_field.cast(query.sql_type) > query.result_value
                )
            case RecordFindResultComparisonOperator.lt:
                find_statement = find_statement.where(
                    data_field.cast(query.sql_type) < query.result_value
                )
            case RecordFindResultComparisonOperator.contains:
                find_statement = find_statement.where(
                    data_field.cast(query.sql_type).like(f"%{query.result_value}%")
                )

    # Apply pagination
    find_statement = find_statement.distinct()
    find_statement = find_statement.offset(pagination.skip)
    find_statement = find_statement.limit(pagination.limit)

    # Execute query
    result = await session.execute(find_statement)
    results = result.scalars().all()

    # Apply random selection if requested
    if random_one and results:
        results = [random.choice(results)]

    logger.info(f"Found {len(results)} records matching criteria")
    return results


@router.patch("/bulk/status", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_update_record_status(
    record_ids: list[int],
    new_status: RecordStatus,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Update status for multiple records at once."""
    for record_id in record_ids:
        record = await session.get(Record, record_id)
        if record:
            record.status = new_status

    await session.commit()


async def assign_user_to_record(
    record_id: int,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Record:
    """Assign the current user to a record."""
    record = await session.get(Record, record_id)
    if record is None:
        raise NOT_FOUND.with_context(f"Record with ID {record_id} not found")

    record.status = RecordStatus.inwork
    record.user_id = user.id
    await session.commit()
    await session.refresh(record)
    return record


async def get_random_series_async(session: AsyncSession = Depends(get_async_session)) -> Series:
    """Get a random series from the database."""
    result = await session.execute(select(Series))
    all_series = result.scalars().all()
    if not all_series:
        raise NOT_FOUND.with_context("No series found in database")
    return random.choice(all_series)


async def add_demo_records_for_user(
    user: User,
    series: Series = Depends(get_random_series_async),
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Add demo records for a new user."""
    # Find demo record types
    result = await session.execute(
        select(RecordType).where(cast(RecordType.name, SQLString).like("%demo%"))
    )
    record_types = result.scalars().all()

    if not record_types:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No demo record types found",
        )

    # Create a record for each demo record type
    for record_type in record_types:
        if record_type.level == "SERIES":
            new_record = RecordCreate(
                status=RecordStatus.pending,
                user_id=user.id,
                series_uid=series.series_uid,
                study_uid=series.study_uid,
                patient_id=series.study.patient_id,
                record_type_name=record_type.name,
            )
        elif record_type.level == "STUDY":
            new_record = RecordCreate(
                status=RecordStatus.pending,
                user_id=user.id,
                study_uid=series.study_uid,
                patient_id=series.study.patient_id,
                record_type_name=record_type.name,
            )
        else:
            continue

        record = Record(**new_record.model_dump())
        session.add(record)

    await session.commit()
