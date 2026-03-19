"""
Async record router for the Clarinet framework.

This module provides async API endpoints for managing records, record types, and record submissions.
Formerly known as task router.
"""

from pathlib import Path
from typing import TYPE_CHECKING
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
from sqlmodel import SQLModel
from starlette.responses import Response

from clarinet.api.auth_config import current_active_user
from clarinet.api.dependencies import (
    AuthorizedRecordDep,
    CurrentUserDep,
    PaginationDep,
    RecordRepositoryDep,
    RecordServiceDep,
    RecordTypeRepositoryDep,
    RecordTypeServiceDep,
    SeriesRepositoryDep,
    SessionDep,
    SlicerServiceDep,
    get_client_ip,
    get_user_role_names,
    require_mutable_config,
)
from clarinet.api.masking import mask_record_patient_data, mask_records
from clarinet.config.toml_exporter import (
    delete_record_type_files,
    export_data_schema_sidecar,
    export_record_type_to_toml,
)
from clarinet.exceptions import CONFLICT, NOT_FOUND
from clarinet.exceptions.domain import AuthorizationError
from clarinet.models import (
    Record,
    RecordCreate,
    RecordFindResult,
    RecordOptional,
    RecordRead,
    RecordStatus,
    RecordTypeCreate,
    RecordTypeFind,
    RecordTypeOptional,
    RecordTypeRead,
    User,
)
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.file_validation import FileValidationResult, validate_record_files
from clarinet.services.schema_hydration import hydrate_schema
from clarinet.services.slicer.context import build_slicer_context_async
from clarinet.settings import settings
from clarinet.types import RecordData
from clarinet.utils.file_checksums import checksums_changed, compute_checksums

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from clarinet.repositories.record_repository import RecordRepository
    from clarinet.services.record_service import RecordService
    from clarinet.services.record_type_service import RecordTypeService
    from clarinet.services.slicer.service import SlicerService


class FileCheckResult(SQLModel):
    """Response model for file check endpoint."""

    changed_files: list[str]
    checksums: dict[str, str]


router = APIRouter(
    tags=["Records"],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Forbidden"},
        404: {"description": "Not found"},
        409: {"description": "Conflict"},
        422: {"description": "Validation error"},
    },
    dependencies=[Depends(current_active_user)],
)


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
    service: RecordTypeServiceDep,
    constrain_unique_names: bool = True,
) -> RecordTypeRead:
    """Create a new record type.

    In TOML mode, exports the created RecordType to a TOML file.
    """
    result = await service.create_record_type(record_type, constrain_unique_names)

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
    service: RecordTypeServiceDep,
) -> RecordTypeRead:
    """Update an existing record type.

    In TOML mode, exports the updated RecordType to a TOML file.
    """
    result = await service.update_record_type(record_type_id, record_type_update)

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
async def get_all_records(
    repo: RecordRepositoryDep,
    user: CurrentUserDep,
) -> list[RecordRead]:
    """Get all records with relations loaded.

    Superusers see all records. Non-superusers see only records matching their roles.
    """
    if user.is_superuser:
        records = await repo.get_all_with_relations()
    else:
        role_names = get_user_role_names(user)
        records = await repo.get_all_for_user_roles(role_names)
    return mask_records(records, user)


@router.get("/my", response_model=list[RecordRead])
async def get_my_records(
    repo: RecordRepositoryDep,
    user: CurrentUserDep,
) -> list[RecordRead]:
    """Get records for the current user with relations loaded.

    Superusers see only their own assigned records.
    Non-superusers see their assigned records plus unassigned records matching their roles.
    """
    role_names = None if user.is_superuser else get_user_role_names(user)
    is_regular_user = not user.is_superuser
    records = await repo.find_by_user(
        user.id,
        role_names=role_names,
        include_unassigned=is_regular_user,
        exclude_unique_violations=is_regular_user,
    )
    return mask_records(records, user)


@router.get("/my/pending", response_model=list[RecordRead])
async def get_my_pending_records(
    repo: RecordRepositoryDep,
    user: CurrentUserDep,
) -> list[RecordRead]:
    """Get pending records for the current user with relations loaded.

    Superusers see only their own pending records.
    Non-superusers see their pending records plus unassigned pending records matching their roles.
    """
    role_names = None if user.is_superuser else get_user_role_names(user)
    is_regular_user = not user.is_superuser
    records = await repo.find_pending_by_user(
        user.id,
        role_names=role_names,
        include_unassigned=is_regular_user,
        exclude_unique_violations=is_regular_user,
    )
    return mask_records(records, user)


@router.get("/available_types", response_model=dict[str, int])
async def get_my_available_record_types(
    repo: RecordRepositoryDep,
    user: User = Depends(current_active_user),
) -> dict[str, int]:
    """Get all record types available to the current user with record counts."""
    type_counts = await repo.get_available_type_counts(user.id, exclude_unique_violations=True)
    return {rt.name: count for rt, count in type_counts.items()}


@router.get("/{record_id}", response_model=RecordRead)
async def get_record(
    record: AuthorizedRecordDep,
    user: CurrentUserDep,
) -> RecordRead:
    """Get a record by ID."""
    return mask_record_patient_data(RecordRead.model_validate(record), user)


@router.get(
    "/{record_id}/schema",
    response_class=JSONResponse,
    responses={
        200: {"description": "Hydrated JSON Schema with x-options resolved"},
        204: {"description": "Record type has no data schema"},
    },
)
async def get_hydrated_schema(
    record: AuthorizedRecordDep,
    session: SessionDep,
) -> Response:
    """Return the record type's JSON Schema with ``x-options`` resolved.

    Args:
        record_id: ID of the record.

    Returns:
        Hydrated JSON Schema dict, or 204 if the record type has no data_schema.
    """
    schema = record.record_type.data_schema
    if not schema:
        return Response(status_code=204)
    hydrated = await hydrate_schema(schema, record, session)
    return JSONResponse(content=hydrated)


async def check_record_constraints(
    new_record: RecordCreate,
    repo: RecordRepositoryDep,
) -> None:
    """Check if a record can be created based on max_records and unique_per_user constraints.

    Args:
        new_record: Record creation payload.
        repo: Record repository.

    Raises:
        RecordConstraintViolationError: If max_records or unique_per_user is violated.
        RecordTypeNotFoundError: If the record type does not exist.
    """
    await repo.check_constraints(
        new_record.record_type_name,
        new_record.series_uid,
        new_record.study_uid,
        patient_id=new_record.patient_id,
        user_id=new_record.user_id,
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
        parent = await repo.validate_parent_record(new_record.parent_record_id)
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
    _authorized_record: AuthorizedRecordDep,
    user: CurrentUserDep,
) -> RecordRead:
    """Update a record's status."""
    record, _ = await service.update_status(record_id, record_status)
    return mask_record_patient_data(RecordRead.model_validate(record), user)


@router.patch("/{record_id}/user", response_model=RecordRead)
async def assign_record_to_user(
    record_id: int,
    user_id: UUID,
    service: RecordServiceDep,
    _authorized_record: AuthorizedRecordDep,
    user: CurrentUserDep,
) -> RecordRead:
    """Assign a record to a user."""
    record, _ = await service.assign_user(record_id, user_id)
    return mask_record_patient_data(RecordRead.model_validate(record), user)


async def _process_submission(
    *,
    record_id: int,
    record: Record,
    data: RecordData,
    user: User,
    repo: RecordRepository,
    service: RecordService,
    rt_service: RecordTypeService,
    is_update: bool,
    slicer_service: SlicerService | None = None,
    session: AsyncSession | None = None,
    client_ip: str | None = None,
) -> RecordRead:
    """Validate, optionally run Slicer, and persist record data.

    Shared logic for POST/PATCH ``/data`` and ``/submit`` endpoints.

    Args:
        record_id: Record ID.
        record: Authorized ORM record.
        data: Submitted form data.
        user: Current user (for masking and user assignment).
        repo: Record repository (for parent/file operations).
        service: Record service (submit / update).
        rt_service: RecordType service (schema validation).
        is_update: ``True`` for PATCH (update), ``False`` for POST (submit).
        slicer_service: Optional Slicer service for validation.
        session: DB session, required when *slicer_service* is provided.
        client_ip: Client IP, required when *slicer_service* is provided.

    Returns:
        Masked ``RecordRead``.
    """
    validated_data = await rt_service.validate_record_data(record, data)
    record_read = RecordRead.model_validate(record)

    # Load parent once — reused by slicer context and file validation
    parent_read: RecordRead | None = None
    if record.parent_record_id is not None:
        parent_orm = await repo.get_with_relations(record.parent_record_id)
        parent_read = RecordRead.model_validate(parent_orm)

    # Run Slicer validation if configured
    if (
        slicer_service is not None
        and session is not None
        and client_ip is not None
        and record_read.record_type.slicer_result_validator
    ):
        context = await build_slicer_context_async(
            record_read,
            session,
            parent=parent_read,
        )
        slicer_url = f"http://{client_ip}:{settings.slicer_port}"
        await slicer_service.execute(
            slicer_url,
            record_read.record_type.slicer_result_validator,
            context,
            request_timeout=60.0,
        )

    if is_update:
        updated, _ = await service.update_data(record_id, validated_data)
    else:
        # Validate input files (raise on missing required files)
        file_result = await validate_record_files(
            record_read,
            raise_on_invalid=True,
            parent=parent_read,
        )
        if file_result and file_result.matched_files:
            await repo.set_files(record, file_result.matched_files)

        updated, _ = await service.submit_data(
            record_id,
            validated_data,
            RecordStatus.finished,
            user_id=user.id,
        )

    return mask_record_patient_data(RecordRead.model_validate(updated), user)


@router.post("/{record_id}/data", response_model=RecordRead)
async def submit_record_data(
    record_id: int,
    authorized_record: AuthorizedRecordDep,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
    rt_service: RecordTypeServiceDep,
    user: CurrentUserDep,
    data: RecordData = Body(),
) -> RecordRead:
    """Submit data for a record."""
    record = authorized_record

    if record.status == RecordStatus.blocked:
        raise CONFLICT.with_context("Record is blocked — required input files are missing.")

    if record.status == RecordStatus.finished:
        raise CONFLICT.with_context("Record already finished. Use PATCH to update the record data.")

    return await _process_submission(
        record_id=record_id,
        record=record,
        data=data,
        user=user,
        repo=repo,
        service=service,
        rt_service=rt_service,
        is_update=False,
    )


@router.patch("/{record_id}/data", response_model=RecordRead)
async def update_record_data(
    record_id: int,
    authorized_record: AuthorizedRecordDep,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
    rt_service: RecordTypeServiceDep,
    user: CurrentUserDep,
    data: RecordData = Body(),
) -> RecordRead:
    """Update a record's data."""
    record = authorized_record

    if record.status != RecordStatus.finished:
        raise CONFLICT.with_context("Record is not finished yet. Use POST to submit record data.")

    return await _process_submission(
        record_id=record_id,
        record=record,
        data=data,
        user=user,
        repo=repo,
        service=service,
        rt_service=rt_service,
        is_update=True,
    )


@router.post("/{record_id}/submit", response_model=RecordRead)
async def submit_record_with_validation(
    record_id: int,
    authorized_record: AuthorizedRecordDep,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
    rt_service: RecordTypeServiceDep,
    slicer_service: SlicerServiceDep,
    session: SessionDep,
    user: CurrentUserDep,
    client_ip: str = Depends(get_client_ip),
    data: RecordData = Body(default={}),
) -> RecordRead:
    """Submit data for a record, running Slicer validation first if configured.

    For records with a ``slicer_result_validator``, the validator script is
    executed on the user's local Slicer instance before the data is saved.
    This ensures output files are written to disk before downstream triggers fire.

    Args:
        record_id: Record ID.
        data: Form data (may be empty for no-schema records).

    Returns:
        Updated record.

    Raises:
        SlicerError: If Slicer validation fails (-> 422).
        SlicerConnectionError: If Slicer is unreachable (-> 502).
    """
    record = authorized_record

    if record.status == RecordStatus.blocked:
        raise CONFLICT.with_context("Record is blocked — required input files are missing.")

    if record.status == RecordStatus.finished:
        raise CONFLICT.with_context("Record already finished. Use PATCH to update the record data.")

    return await _process_submission(
        record_id=record_id,
        record=record,
        data=data,
        user=user,
        repo=repo,
        service=service,
        rt_service=rt_service,
        is_update=False,
        slicer_service=slicer_service,
        session=session,
        client_ip=client_ip,
    )


@router.patch("/{record_id}/submit", response_model=RecordRead)
async def resubmit_record_with_validation(
    record_id: int,
    authorized_record: AuthorizedRecordDep,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
    rt_service: RecordTypeServiceDep,
    slicer_service: SlicerServiceDep,
    session: SessionDep,
    user: CurrentUserDep,
    client_ip: str = Depends(get_client_ip),
    data: RecordData = Body(default={}),
) -> RecordRead:
    """Re-submit data for a finished record, running Slicer validation first if configured.

    Args:
        record_id: Record ID.
        data: Form data (may be empty for no-schema records).

    Returns:
        Updated record.

    Raises:
        SlicerError: If Slicer validation fails (-> 422).
        SlicerConnectionError: If Slicer is unreachable (-> 502).
    """
    record = authorized_record

    if record.status != RecordStatus.finished:
        raise CONFLICT.with_context("Record is not finished yet. Use POST to submit record data.")

    return await _process_submission(
        record_id=record_id,
        record=record,
        data=data,
        user=user,
        repo=repo,
        service=service,
        rt_service=rt_service,
        is_update=True,
        slicer_service=slicer_service,
        session=session,
        client_ip=client_ip,
    )


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
    record: AuthorizedRecordDep,
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
    record_read = RecordRead.model_validate(record)
    parent_read = None
    if record.parent_record_id is not None:
        parent = await repo.get_with_relations(record.parent_record_id)
        parent_read = RecordRead.model_validate(parent)
    result = await validate_record_files(record_read, parent=parent_read)
    if result is None:
        return FileValidationResult(valid=True)

    return result


@router.post("/{record_id}/check-files", response_model=FileCheckResult)
async def check_record_files(
    record_id: int,
    authorized_record: AuthorizedRecordDep,
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
    record = authorized_record
    record_read = RecordRead.model_validate(record)

    # Fetch parent for fallback pattern resolution
    parent_read = None
    if record.parent_record_id is not None:
        parent = await repo.get_with_relations(record.parent_record_id)
        parent_read = RecordRead.model_validate(parent)

    # Auto-unblock: if record is blocked, check whether input files are now present
    if record.status == RecordStatus.blocked:
        file_result = await validate_record_files(record_read, parent=parent_read)
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
    _authorized_record: AuthorizedRecordDep,
    service: RecordServiceDep,
    mode: str = Body(default="hard"),
    source_record_id: int | None = Body(default=None),
    reason: str | None = Body(default=None),
) -> RecordRead:
    """Invalidate a record.

    Hard mode resets status to pending (keeps user assignment) and fires
    RecordFlow triggers. Soft mode only appends the reason to context_info.

    Args:
        record_id: ID of the record to invalidate.
        mode: "hard" or "soft".
        source_record_id: ID of the record that triggered invalidation.
        reason: Human-readable reason for invalidation.

    Returns:
        Updated record.
    """
    record = await service.invalidate_record(
        record_id=record_id,
        mode=mode,
        source_record_id=source_record_id,
        reason=reason,
    )
    return RecordRead.model_validate(record)


@router.post("/find", response_model=list[RecordRead])
async def find_records(
    pagination: PaginationDep,
    repo: RecordRepositoryDep,
    user: CurrentUserDep,
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
) -> list[RecordRead]:
    """Find records by various criteria."""
    role_names = None if user.is_superuser else get_user_role_names(user)
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
        role_names=role_names,
        data_queries=find_queries,
    )
    records = await repo.find_by_criteria(criteria, skip=pagination.skip, limit=pagination.limit)
    return mask_records(records, user)


@router.patch("/bulk/status", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_update_record_status(
    record_ids: list[int],
    new_status: RecordStatus,
    service: RecordServiceDep,
    user: CurrentUserDep,
    repo: RecordRepositoryDep,
) -> None:
    """Update status for multiple records at once."""
    if not user.is_superuser:
        user_roles = get_user_role_names(user)
        for rid in record_ids:
            record = await repo.get_with_relations(rid)
            role_name = record.record_type.role_name
            if role_name is None or role_name not in user_roles:
                raise AuthorizationError(f"Insufficient permissions to access record {rid}")
    await service.bulk_update_status(record_ids, new_status)


# Dependency functions (used by other parts of the application)


async def assign_user_to_record(
    record_id: int,
    service: RecordServiceDep,
    user: User = Depends(current_active_user),
) -> Record:
    """Assign the current user to a record with uniqueness constraint check."""
    return await service.claim_record(record_id, user.id)


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
