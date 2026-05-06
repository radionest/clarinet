"""
Async record router for the Clarinet framework.

This module provides async API endpoints for managing records, record types, and record submissions.
Formerly known as task router.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    Query,
    Request,
    status,
)
from fastapi import (
    Path as FastAPIPath,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import SQLModel
from starlette.responses import Response

from clarinet.api.auth_config import current_active_user
from clarinet.api.dependencies import (
    AuthorizedRecordDep,
    CurrentUserDep,
    MutableRecordDep,
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
    RecordContextInfoUpdate,
    RecordCreate,
    RecordOptional,
    RecordPage,
    RecordRead,
    RecordSearchFilter,
    RecordSearchQuery,
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
        400: {"description": "Bad request (malformed body)"},
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
    response_model=None,
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


@router.patch(
    "/bulk/status",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # Required: PEP 563 makes -> None a truthy ForwardRef, triggering FastAPI 204 body assertion
)
async def bulk_update_record_status(
    record_ids: list[Annotated[int, Body(ge=1, le=2147483647)]],
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


@router.patch("/{record_id}/status", response_model=RecordRead)
async def update_record_status(
    record_id: int,
    record_status: RecordStatus,
    service: RecordServiceDep,
    _authorized_record: MutableRecordDep,
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


@router.patch("/{record_id}/context-info", response_model=RecordRead)
async def update_record_context_info(
    record_id: int,
    body: RecordContextInfoUpdate,
    repo: RecordRepositoryDep,
    _authorized_record: MutableRecordDep,
    user: CurrentUserDep,
) -> RecordRead:
    """Replace ``context_info`` (markdown source) on a record.

    Permitted to superusers (which includes pipeline service tokens), the
    record's assigned user, and any role-authorised user when the record is
    unassigned. Pass ``null`` to clear the field. The rendered HTML is
    available on the response as ``context_info_html``.
    """
    record = await repo.update_fields(record_id, {"context_info": body.context_info})
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
    new_status: RecordStatus = RecordStatus.finished,
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
    # Skip validation when submitting with a non-finished status (e.g. failed) —
    # error data won't match the record type schema and files may not exist.
    skip_validation = not is_update and new_status != RecordStatus.finished

    validated_data = (
        data if skip_validation else await rt_service.validate_record_data(record, data)
    )
    record_read = RecordRead.model_validate(record)

    # Load parent once — reused by slicer context and file validation
    parent_read: RecordRead | None = None
    if record.parent_record_id is not None:
        parent_orm = await repo.get_with_relations(record.parent_record_id)
        parent_read = RecordRead.model_validate(parent_orm)

    # Run Slicer validation if configured
    if (
        not skip_validation
        and slicer_service is not None
        and session is not None
        and client_ip is not None
        and record_read.record_type.slicer_result_validator
    ):
        context = await build_slicer_context_async(
            record_read,
            session,
            parent=parent_read,
        )
        # Prepend record_id check — ensures validation runs on the same record that was opened
        validator_script = (
            "validate_record_id(record_id)\n" + record_read.record_type.slicer_result_validator
        )
        slicer_url = f"http://{client_ip}:{settings.slicer_port}"
        await slicer_service.execute(
            slicer_url,
            validator_script,
            context,
            request_timeout=60.0,
        )

    if is_update:
        updated, _ = await service.update_data(record_id, validated_data)
    else:
        if not skip_validation:
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
            new_status,
            user_id=user.id,
        )

    return mask_record_patient_data(RecordRead.model_validate(updated), user)


_SUBMIT_STATUSES = (RecordStatus.finished, RecordStatus.failed)


@router.post("/{record_id}/data", response_model=RecordRead)
async def submit_record_data(
    record_id: int,
    authorized_record: MutableRecordDep,
    repo: RecordRepositoryDep,
    service: RecordServiceDep,
    rt_service: RecordTypeServiceDep,
    user: CurrentUserDep,
    data: RecordData = Body(),
    submit_status: RecordStatus | None = Query(default=None, alias="status"),
) -> RecordRead:
    """Submit data for a record.

    Pass ``?status=failed`` to mark the record as failed (skips validation).
    """
    record = authorized_record
    target_status = submit_status or RecordStatus.finished

    if target_status not in _SUBMIT_STATUSES:
        raise CONFLICT.with_context(
            f"Invalid submit status '{target_status.value}'. "
            f"Allowed: {', '.join(s.value for s in _SUBMIT_STATUSES)}."
        )

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
        new_status=target_status,
    )


@router.patch("/{record_id}/data", response_model=RecordRead)
async def update_record_data(
    record_id: int,
    authorized_record: MutableRecordDep,
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


_PREFILL_STATUSES = (RecordStatus.pending, RecordStatus.blocked)


async def _do_prefill(
    record_id: int,
    record: Record,
    data: RecordData,
    user: User,
    service: RecordService,
    rt_service: RecordTypeService,
) -> RecordRead:
    """Validate and persist prefill data without status change or triggers."""
    if record.status not in _PREFILL_STATUSES:
        raise CONFLICT.with_context(
            f"Record status '{record.status.value}' does not allow prefill. "
            f"Allowed: {', '.join(s.value for s in _PREFILL_STATUSES)}."
        )
    validated = await rt_service.validate_record_data_partial(record, data)
    updated, _ = await service.prefill_data(record_id, validated)
    return mask_record_patient_data(RecordRead.model_validate(updated), user)


@router.post("/{record_id}/data/prefill", response_model=RecordRead)
async def prefill_record_data_post(
    record_id: int,
    authorized_record: MutableRecordDep,
    service: RecordServiceDep,
    rt_service: RecordTypeServiceDep,
    user: CurrentUserDep,
    data: RecordData = Body(),
) -> RecordRead:
    """Set prefill data on a pending/blocked record. Errors if data already exists."""
    if authorized_record.data:
        raise CONFLICT.with_context(
            "Record already has data. Use PUT to replace or PATCH to merge."
        )
    return await _do_prefill(record_id, authorized_record, data, user, service, rt_service)


@router.put("/{record_id}/data/prefill", response_model=RecordRead)
async def prefill_record_data_put(
    record_id: int,
    authorized_record: MutableRecordDep,
    service: RecordServiceDep,
    rt_service: RecordTypeServiceDep,
    user: CurrentUserDep,
    data: RecordData = Body(),
) -> RecordRead:
    """Replace prefill data on a pending/blocked record."""
    return await _do_prefill(record_id, authorized_record, data, user, service, rt_service)


@router.patch("/{record_id}/data/prefill", response_model=RecordRead)
async def prefill_record_data_patch(
    record_id: int,
    authorized_record: MutableRecordDep,
    service: RecordServiceDep,
    rt_service: RecordTypeServiceDep,
    user: CurrentUserDep,
    data: RecordData = Body(),
) -> RecordRead:
    """Merge new data into existing prefill data on a pending/blocked record."""
    merged = {**(authorized_record.data or {}), **data}
    return await _do_prefill(record_id, authorized_record, merged, user, service, rt_service)


@router.post("/{record_id}/submit", response_model=RecordRead)
async def submit_record_with_validation(
    record_id: int,
    authorized_record: MutableRecordDep,
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
    authorized_record: MutableRecordDep,
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
    record_id: Annotated[int, FastAPIPath(ge=1, le=2147483647)],
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
    _authorized_record: AuthorizedRecordDep,
    service: RecordServiceDep,
) -> FileCheckResult:
    """Compute current file checksums, compare with stored, trigger invalidation if changed.

    For ``blocked`` records, this endpoint also checks whether the required
    input files have appeared and auto-transitions to ``pending`` if so.
    """
    changed_files, checksums = await service.check_files(record_id)
    return FileCheckResult(changed_files=changed_files, checksums=checksums)


# Anything outside ``[A-Za-z0-9_.-]`` is replaced before going into the
# Content-Disposition header so a hostile or accidental filename (newline,
# double quote) cannot break the response framing.
_UNSAFE_OUTPUT_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


@router.get("/{record_id}/output-files/{file_name}")
async def download_output_file(
    record_id: Annotated[int, FastAPIPath(ge=1, le=2147483647)],
    file_name: Annotated[str, FastAPIPath(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")],
    _authorized_record: AuthorizedRecordDep,
    service: RecordServiceDep,
) -> FileResponse:
    """Download a single OUTPUT file by ``FileDefinition.name``.

    For ``multiple=True`` definitions, returns the first glob match — a
    dedicated ZIP endpoint will be added when needed.
    """
    paths = await service.resolve_output_file(record_id, file_name)
    file_path = paths[0]
    safe_name = _UNSAFE_OUTPUT_FILENAME_RE.sub("_", file_path.name) or "file"
    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return FileResponse(path=file_path, filename=safe_name, media_type=media_type)


_MANUALLY_FAILABLE_STATUSES = (RecordStatus.pending, RecordStatus.inwork)


@router.post("/{record_id}/fail", response_model=RecordRead)
async def fail_record(
    record_id: int,
    authorized_record: AuthorizedRecordDep,
    service: RecordServiceDep,
    user: CurrentUserDep,
    reason: str = Body(embed=True, min_length=1),
) -> RecordRead:
    """Manually mark a record as failed with a reason.

    Only records in ``pending`` or ``inwork`` status can be failed manually.
    """
    reason = reason.strip()
    if not reason:
        raise CONFLICT.with_context("Reason cannot be empty or whitespace-only.")

    if authorized_record.status not in _MANUALLY_FAILABLE_STATUSES:
        raise CONFLICT.with_context(
            f"Cannot fail record in '{authorized_record.status.value}' status. "
            f"Allowed: {', '.join(s.value for s in _MANUALLY_FAILABLE_STATUSES)}."
        )

    updated = await service.fail_record(record_id, reason)
    return mask_record_patient_data(RecordRead.model_validate(updated), user)


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


def _build_record_search_criteria(
    query: RecordSearchFilter,
    user: User,
    *,
    extra_excludes: set[str] | None = None,
) -> RecordSearchCriteria:
    """Build ``RecordSearchCriteria`` for /records/find endpoints.

    Regular users get their roles attached, ``include_unassigned=True``, and
    ``exclude_unique_violations=True`` so unassigned context-duplicates of
    unique_per_user types they already completed are hidden. Superusers get
    no role filter and no violation filter.
    """
    is_regular_user = not user.is_superuser
    role_names = get_user_role_names(user) if is_regular_user else None
    excludes = {"data_queries"}
    if extra_excludes:
        excludes |= extra_excludes
    return RecordSearchCriteria(
        **query.model_dump(exclude=excludes),
        data_queries=query.data_queries,
        role_names=role_names,
        include_unassigned=is_regular_user,
        exclude_unique_violations=is_regular_user,
    )


@router.post("/find/random", response_model=RecordRead | None)
async def find_random_record(
    repo: RecordRepositoryDep,
    user: CurrentUserDep,
    query: RecordSearchFilter,
) -> RecordRead | None:
    """Find a single random record matching filter criteria."""
    criteria = _build_record_search_criteria(query, user)
    record = await repo.find_random(criteria)
    if record is None:
        return None
    return mask_records([record], user)[0]


@router.post("/find", response_model=RecordPage)
async def find_records(
    repo: RecordRepositoryDep,
    user: CurrentUserDep,
    query: RecordSearchQuery,
) -> RecordPage:
    """Search records with cursor-based pagination."""
    criteria = _build_record_search_criteria(
        query, user, extra_excludes={"cursor", "limit", "sort"}
    )
    result = await repo.find_page(
        criteria,
        cursor=query.cursor,
        limit=query.limit,
        sort=query.sort,
    )
    return RecordPage(
        items=mask_records(result.records, user),
        next_cursor=result.next_cursor,
        limit=query.limit,
        sort=query.sort,
    )


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
