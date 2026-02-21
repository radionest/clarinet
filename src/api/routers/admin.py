"""Admin router with system-wide statistics and record management endpoints."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy import func, select
from sqlmodel import col

from src.api.dependencies import RecordRepositoryDep, SessionDep, SuperUserDep
from src.models import Patient, Record, RecordRead, Study, User
from src.models.base import RecordStatus

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
    session: SessionDep,
) -> AdminStats:
    """Get system-wide aggregate statistics.

    Args:
        _current_user: Authenticated superuser (enforced by dependency).
        session: Database session.

    Returns:
        AdminStats with total counts and per-status record breakdown.
    """
    total_studies = (await session.execute(select(func.count()).select_from(Study))).scalar_one()
    total_records = (await session.execute(select(func.count()).select_from(Record))).scalar_one()
    total_users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    total_patients = (await session.execute(select(func.count()).select_from(Patient))).scalar_one()

    # Records grouped by status
    rows = (
        await session.execute(select(col(Record.status), func.count()).group_by(col(Record.status)))
    ).all()

    records_by_status: dict[str, int] = {status.value: 0 for status in RecordStatus}
    for status, count in rows:
        records_by_status[status.value] = count

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
