"""Admin router with system-wide statistics and record management endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter
from fastapi import Path as PathParam

from clarinet.api.dependencies import AdminServiceDep, AdminUserDep, RecordServiceDep
from clarinet.models import Record, RecordRead
from clarinet.models.admin import (
    AdminStats,
    ClearOutputFilesResult,
    DeleteRecordResult,
    RecordTypeStats,
    RoleMatrixResponse,
)
from clarinet.models.base import RecordStatus

router = APIRouter(
    responses={
        400: {"description": "Bad request"},
        401: {"description": "Not authenticated"},
        403: {"description": "Forbidden"},
        404: {"description": "Not found"},
        422: {"description": "Validation error"},
    },
)


@router.get("/stats", response_model=AdminStats)
async def get_admin_stats(
    _current_user: AdminUserDep,
    service: AdminServiceDep,
) -> AdminStats:
    """Get system-wide aggregate statistics.

    Args:
        _current_user: Authenticated admin user (superuser or admin role).
        service: Admin service.

    Returns:
        AdminStats with total counts and per-status record breakdown.
    """
    return await service.get_stats()


@router.patch("/records/{record_id}/assign", response_model=RecordRead)
async def admin_assign_record_user(
    record_id: Annotated[int, PathParam(ge=1, le=2147483647)],
    user_id: UUID,
    _current_user: AdminUserDep,
    service: RecordServiceDep,
) -> Record:
    """Assign a user to a record (admin only).

    Args:
        record_id: The record to assign.
        user_id: The user UUID to assign.
        _current_user: Authenticated admin user (superuser or admin role).
        service: Record service.

    Returns:
        Updated record with all relations loaded.
    """
    record, _ = await service.assign_user(record_id, user_id)
    return record


@router.patch("/records/{record_id}/status", response_model=RecordRead)
async def admin_update_record_status(
    record_id: Annotated[int, PathParam(ge=1, le=2147483647)],
    record_status: RecordStatus,
    _current_user: AdminUserDep,
    service: RecordServiceDep,
) -> Record:
    """Set any status on a record (admin only).

    Args:
        record_id: The record to update.
        record_status: New status to set.
        _current_user: Authenticated admin user (superuser or admin role).
        service: Record service.

    Returns:
        Updated record with all relations loaded.
    """
    record, _ = await service.update_status(record_id, record_status)
    return record


@router.delete("/records/{record_id}/user", response_model=RecordRead)
async def admin_unassign_record_user(
    record_id: Annotated[int, PathParam(ge=1, le=2147483647)],
    _current_user: AdminUserDep,
    service: RecordServiceDep,
) -> Record:
    """Remove user assignment from a record (admin only).

    If the record is inwork, status is reset to pending.

    Args:
        record_id: The record to unassign.
        _current_user: Authenticated admin user (superuser or admin role).
        service: Record service.

    Returns:
        Updated record with all relations loaded.
    """
    record, _ = await service.unassign_user(record_id)
    return record


@router.delete(
    "/records/{record_id}",
    response_model=DeleteRecordResult,
    responses={409: {"description": "Subtree contains a record in 'inwork' status"}},
)
async def delete_record_cascade(
    record_id: Annotated[int, PathParam(ge=1, le=2147483647)],
    _current_user: AdminUserDep,
    service: RecordServiceDep,
) -> DeleteRecordResult:
    """Delete a record with all descendants and their OUTPUT files (admin only).

    Aborts with 409 Conflict if any record in the subtree is in ``inwork`` status.
    """
    deleted_ids, files_removed = await service.delete_record_cascade(record_id)
    return DeleteRecordResult(deleted_ids=deleted_ids, files_removed=files_removed)


@router.delete("/records/{record_id}/output-files", response_model=ClearOutputFilesResult)
async def clear_record_output_files(
    record_id: Annotated[int, PathParam(ge=1, le=2147483647)],
    _current_user: AdminUserDep,
    service: RecordServiceDep,
) -> ClearOutputFilesResult:
    """Delete OUTPUT files from disk for a non-finished record (admin only).

    Intended for clearing stale output files before retrying a failed pipeline task.
    """
    deleted_files, deleted_links = await service.clear_output_files(record_id)
    return ClearOutputFilesResult(deleted_files=deleted_files, deleted_links=deleted_links)


@router.get("/role-matrix", response_model=RoleMatrixResponse)
async def get_role_matrix(
    _current_user: AdminUserDep,
    service: AdminServiceDep,
) -> RoleMatrixResponse:
    """Get role matrix for admin dashboard.

    Args:
        _current_user: Authenticated admin user (superuser or admin role).
        service: Admin service.

    Returns:
        RoleMatrixResponse with all roles and users with their assignments.
    """
    return await service.get_role_matrix()


@router.get("/record-types/stats", response_model=list[RecordTypeStats])
async def get_record_type_stats(
    _current_user: AdminUserDep,
    service: AdminServiceDep,
) -> list[RecordTypeStats]:
    """Get per-record-type statistics.

    Args:
        _current_user: Authenticated admin user (superuser or admin role).
        service: Admin service.

    Returns:
        List of RecordTypeStats with per-type counts and unique users.
    """
    return await service.get_record_type_stats()
