"""Admin router with system-wide statistics and record management endpoints."""

from uuid import UUID

from fastapi import APIRouter

from clarinet.api.dependencies import AdminServiceDep, RecordServiceDep, SuperUserDep
from clarinet.models import Record, RecordRead
from clarinet.models.admin import AdminStats, RecordTypeStats, RoleMatrixResponse
from clarinet.models.base import RecordStatus

router = APIRouter()


@router.get("/stats", response_model=AdminStats)
async def get_admin_stats(
    _current_user: SuperUserDep,
    service: AdminServiceDep,
) -> AdminStats:
    """Get system-wide aggregate statistics.

    Args:
        _current_user: Authenticated superuser (enforced by dependency).
        service: Admin service.

    Returns:
        AdminStats with total counts and per-status record breakdown.
    """
    return await service.get_stats()


@router.patch("/records/{record_id}/assign", response_model=RecordRead)
async def admin_assign_record_user(
    record_id: int,
    user_id: UUID,
    _current_user: SuperUserDep,
    service: RecordServiceDep,
) -> Record:
    """Assign a user to a record (superuser only).

    Args:
        record_id: The record to assign.
        user_id: The user UUID to assign.
        _current_user: Authenticated superuser (enforced by dependency).
        service: Record service.

    Returns:
        Updated record with all relations loaded.
    """
    record, _ = await service.assign_user(record_id, user_id)
    return record


@router.patch("/records/{record_id}/status", response_model=RecordRead)
async def admin_update_record_status(
    record_id: int,
    record_status: RecordStatus,
    _current_user: SuperUserDep,
    service: RecordServiceDep,
) -> Record:
    """Set any status on a record (superuser only).

    Args:
        record_id: The record to update.
        record_status: New status to set.
        _current_user: Authenticated superuser (enforced by dependency).
        service: Record service.

    Returns:
        Updated record with all relations loaded.
    """
    record, _ = await service.update_status(record_id, record_status)
    return record


@router.delete("/records/{record_id}/user", response_model=RecordRead)
async def admin_unassign_record_user(
    record_id: int,
    _current_user: SuperUserDep,
    service: RecordServiceDep,
) -> Record:
    """Remove user assignment from a record (superuser only).

    If the record is inwork, status is reset to pending.

    Args:
        record_id: The record to unassign.
        _current_user: Authenticated superuser (enforced by dependency).
        service: Record service.

    Returns:
        Updated record with all relations loaded.
    """
    record, _ = await service.unassign_user(record_id)
    return record


@router.get("/role-matrix", response_model=RoleMatrixResponse)
async def get_role_matrix(
    _current_user: SuperUserDep,
    service: AdminServiceDep,
) -> RoleMatrixResponse:
    """Get role matrix for admin dashboard.

    Args:
        _current_user: Authenticated superuser (enforced by dependency).
        service: Admin service.

    Returns:
        RoleMatrixResponse with all roles and users with their assignments.
    """
    return await service.get_role_matrix()


@router.get("/record-types/stats", response_model=list[RecordTypeStats])
async def get_record_type_stats(
    _current_user: SuperUserDep,
    service: AdminServiceDep,
) -> list[RecordTypeStats]:
    """Get per-record-type statistics.

    Args:
        _current_user: Authenticated superuser (enforced by dependency).
        service: Admin service.

    Returns:
        List of RecordTypeStats with per-type counts and unique users.
    """
    return await service.get_record_type_stats()
