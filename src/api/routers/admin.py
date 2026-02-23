"""Admin router with system-wide statistics and record management endpoints."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel as PydanticBaseModel

from src.api.dependencies import AdminServiceDep, RecordRepositoryDep, SuperUserDep
from src.models import Record, RecordRead

router = APIRouter()


class AdminStats(PydanticBaseModel):
    """Aggregate system statistics for admin dashboard."""

    total_studies: int
    total_records: int
    total_users: int
    total_patients: int
    records_by_status: dict[str, int]


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
    total_studies, total_records, total_users, total_patients = await service.get_total_counts()
    records_by_status = await service.get_records_by_status()

    return AdminStats(
        total_studies=total_studies,
        total_records=total_records,
        total_users=total_users,
        total_patients=total_patients,
        records_by_status=records_by_status,
    )


@router.patch("/records/{record_id}/assign", response_model=RecordRead)
async def admin_assign_record_user(
    record_id: int,
    user_id: UUID,
    _current_user: SuperUserDep,
    repo: RecordRepositoryDep,
) -> Record:
    """Assign a user to a record (superuser only).

    Args:
        record_id: The record to assign.
        user_id: The user UUID to assign.
        _current_user: Authenticated superuser (enforced by dependency).
        repo: Record repository.

    Returns:
        Updated record with all relations loaded.
    """
    await repo.assign_user(record_id, user_id)
    return await repo.get_with_relations(record_id)


class RecordTypeStatusCounts(PydanticBaseModel):
    """Per-status record counts for a record type."""

    pending: int = 0
    inwork: int = 0
    finished: int = 0
    failed: int = 0
    pause: int = 0


class RecordTypeStats(PydanticBaseModel):
    """Record type with aggregate statistics."""

    name: str
    description: str | None = None
    label: str | None = None
    level: str
    role_name: str | None = None
    min_users: int | None = None
    max_users: int | None = None
    total_records: int
    records_by_status: RecordTypeStatusCounts
    unique_users: int


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
    record_types, status_map, user_map = await service.get_record_type_stats()

    result: list[RecordTypeStats] = []
    for rt in record_types:
        counts = status_map.get(rt.name, {})
        status_counts = RecordTypeStatusCounts(
            pending=counts.get("pending", 0),
            inwork=counts.get("inwork", 0),
            finished=counts.get("finished", 0),
            failed=counts.get("failed", 0),
            pause=counts.get("pause", 0),
        )
        total = sum(counts.values())
        result.append(
            RecordTypeStats(
                name=rt.name,
                description=rt.description,
                label=rt.label,
                level=rt.level.value,
                role_name=rt.role_name,
                min_users=rt.min_users,
                max_users=rt.max_users,
                total_records=total,
                records_by_status=status_counts,
                unique_users=user_map.get(rt.name, 0),
            )
        )

    return result
