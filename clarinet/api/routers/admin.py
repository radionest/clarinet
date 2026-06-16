"""Admin router with system-wide statistics and record management endpoints."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter
from fastapi import Path as PathParam

from clarinet.api.dependencies import (
    AdminServiceDep,
    AdminUserDep,
    AuditActorDep,
    PaginationDep,
    RecordEventRepositoryDep,
    RecordServiceDep,
    SessionDep,
)
from clarinet.models import Record, RecordEventFind, RecordEventRead, RecordRead
from clarinet.models.admin import (
    AdminStats,
    ClearOutputFilesResult,
    DeleteRecordResult,
    OnlineUsersResponse,
    RecordTypeStats,
    RoleMatrixResponse,
)
from clarinet.models.base import RecordStatus
from clarinet.settings import settings
from clarinet.utils.session import get_online_user_ids

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
    actor: AuditActorDep,
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
    record, _ = await service.assign_user(record_id, user_id, actor_id=actor)
    return record


@router.patch("/records/{record_id}/status", response_model=RecordRead)
async def admin_update_record_status(
    record_id: Annotated[int, PathParam(ge=1, le=2147483647)],
    record_status: RecordStatus,
    _current_user: AdminUserDep,
    service: RecordServiceDep,
    actor: AuditActorDep,
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
    record, _ = await service.update_status(record_id, record_status, actor_id=actor)
    return record


@router.delete("/records/{record_id}/user", response_model=RecordRead)
async def admin_unassign_record_user(
    record_id: Annotated[int, PathParam(ge=1, le=2147483647)],
    _current_user: AdminUserDep,
    service: RecordServiceDep,
    actor: AuditActorDep,
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
    record, _ = await service.unassign_user(record_id, actor_id=actor)
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
    actor: AuditActorDep,
) -> DeleteRecordResult:
    """Delete a record with all descendants and their OUTPUT files (admin only).

    Aborts with 409 Conflict if any record in the subtree is in ``inwork`` status.
    """
    deleted_ids, files_removed = await service.delete_record_cascade(record_id, actor_id=actor)
    return DeleteRecordResult(deleted_ids=deleted_ids, files_removed=files_removed)


@router.delete("/records/{record_id}/output-files", response_model=ClearOutputFilesResult)
async def clear_record_output_files(
    record_id: Annotated[int, PathParam(ge=1, le=2147483647)],
    _current_user: AdminUserDep,
    service: RecordServiceDep,
    actor: AuditActorDep,
) -> ClearOutputFilesResult:
    """Delete OUTPUT files from disk for a non-finished record (admin only).

    Intended for clearing stale output files before retrying a failed pipeline task.
    """
    deleted_files, deleted_links = await service.clear_output_files(record_id, actor_id=actor)
    return ClearOutputFilesResult(deleted_files=deleted_files, deleted_links=deleted_links)


@router.get("/records/events", response_model=list[RecordEventRead])
async def list_record_events(
    current_user: AdminUserDep,
    events_repo: RecordEventRepositoryDep,
    pagination: PaginationDep,
    kind: str | None = None,
    actor_id: UUID | None = None,
    patient_id: str | None = None,
    since: datetime | None = None,
) -> list[RecordEventRead]:
    """Global record audit feed, newest first (admin only).

    Optional filters: ``kind``, ``actor_id``, ``patient_id`` (events of the
    patient's current records), ``since`` (``occurred_at`` lower bound).
    Events of already-deleted records are available only via
    ``/records/events/deleted``.

    ``patient_id`` is returned to superusers only. This cross-patient feed also
    serves admin-role non-superusers, who are masked on every other surface
    (the record-scoped ``/records/{id}/events`` view applies per-patient
    masking), so an anonymized patient's real id is withheld here too. The
    ``patient_id`` query filter still works for any admin.
    """
    criteria = RecordEventFind(
        kind=kind,
        actor_id=actor_id,
        patient_id=patient_id,
        since=since,
        skip=pagination.skip,
        limit=pagination.limit,
    )
    events = await events_repo.find(criteria)
    reads = [RecordEventRead.model_validate(e) for e in events]
    if not current_user.is_superuser:
        reads = [r.model_copy(update={"patient_id": None}) for r in reads]
    return reads


@router.get("/records/events/deleted", response_model=list[RecordEventRead])
async def list_deleted_record_events(
    _current_user: AdminUserDep,
    events_repo: RecordEventRepositoryDep,
    pagination: PaginationDep,
) -> list[RecordEventRead]:
    """Audit events of deleted records, newest first (admin only).

    ``old_value`` carries a snapshot of the removed record; ``record_id``
    is NULL because the FK was detached on delete.
    """
    events = await events_repo.list_deleted(skip=pagination.skip, limit=pagination.limit)
    return [RecordEventRead.model_validate(e) for e in events]


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


@router.get("/online-users", response_model=OnlineUsersResponse)
async def get_online_users(
    _current_user: AdminUserDep,
    session: SessionDep,
) -> OnlineUsersResponse:
    """Ids of users currently online, for the admin presence indicator.

    "Online" = at least one session that would still authenticate now: not
    expired and within ``session_idle_timeout_minutes`` of last access. SSE
    ``presence`` events deliver live deltas; this is the initial snapshot (and
    the resync after an SSE reconnect).
    """
    ids = await get_online_user_ids(session, settings.session_idle_timeout_minutes)
    return OnlineUsersResponse(user_ids=sorted(str(u) for u in ids))


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
