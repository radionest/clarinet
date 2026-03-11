"""Service layer for admin dashboard statistics."""

import asyncio

from clarinet.models.admin import (
    AdminStats,
    RecordTypeStats,
    RecordTypeStatusCounts,
    RoleMatrixResponse,
    UserRoleInfo,
)
from clarinet.models.base import RecordStatus
from clarinet.repositories.patient_repository import PatientRepository
from clarinet.repositories.record_repository import RecordRepository
from clarinet.repositories.record_type_repository import RecordTypeRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.repositories.user_repository import UserRepository


class AdminService:
    """Service for aggregating admin dashboard statistics."""

    def __init__(
        self,
        record_repo: RecordRepository,
        record_type_repo: RecordTypeRepository,
        study_repo: StudyRepository,
        patient_repo: PatientRepository,
        user_repo: UserRepository,
    ):
        """Initialize admin service with repositories.

        Args:
            record_repo: Record repository instance
            record_type_repo: RecordType repository instance
            study_repo: Study repository instance
            patient_repo: Patient repository instance
            user_repo: User repository instance
        """
        self.record_repo = record_repo
        self.record_type_repo = record_type_repo
        self.study_repo = study_repo
        self.patient_repo = patient_repo
        self.user_repo = user_repo

    async def get_stats(self) -> AdminStats:
        """Get aggregate system statistics for admin dashboard.

        Returns:
            AdminStats with total counts and per-status record breakdown.
        """
        (total_studies, total_records, total_users, total_patients), records_by_status = (
            await asyncio.gather(
                self._get_total_counts(),
                self._get_records_by_status(),
            )
        )
        return AdminStats(
            total_studies=total_studies,
            total_records=total_records,
            total_users=total_users,
            total_patients=total_patients,
            records_by_status=records_by_status,
        )

    async def get_record_type_stats(self) -> list[RecordTypeStats]:
        """Get per-record-type statistics with status counts and unique users.

        Returns:
            List of RecordTypeStats with per-type counts and unique users.
        """
        record_types, status_map, user_map = await asyncio.gather(
            self.record_type_repo.list_all(),
            self.record_repo.get_per_type_status_counts(),
            self.record_repo.get_per_type_unique_users(),
        )

        result = []
        for rt in record_types:
            counts = status_map.get(rt.name, {})
            status_counts = {status.value: counts.get(status.value, 0) for status in RecordStatus}
            result.append(
                RecordTypeStats(
                    name=rt.name,
                    description=rt.description,
                    label=rt.label,
                    level=rt.level.value,
                    role_name=rt.role_name,
                    min_records=rt.min_records,
                    max_records=rt.max_records,
                    total_records=sum(counts.values()),
                    records_by_status=RecordTypeStatusCounts(**status_counts),
                    unique_users=user_map.get(rt.name, 0),
                )
            )
        return result

    async def get_role_matrix(self) -> RoleMatrixResponse:
        """Get role matrix data: all roles and all users with their role assignments.

        Returns:
            RoleMatrixResponse with sorted role names and user info.
        """
        roles, users = await asyncio.gather(
            self.user_repo.get_all_roles(),
            self.user_repo.get_all_with_roles(),
        )

        role_names = sorted(role.name for role in roles)
        user_infos = [
            UserRoleInfo(
                id=str(user.id),
                email=user.email,
                is_active=user.is_active,
                is_superuser=user.is_superuser,
                role_names=sorted(role.name for role in user.roles),
            )
            for user in users
        ]
        return RoleMatrixResponse(roles=role_names, users=user_infos)

    async def _get_total_counts(self) -> tuple[int, int, int, int]:
        """Get total counts for studies, records, users, and patients in parallel.

        Returns:
            Tuple of (total_studies, total_records, total_users, total_patients)
        """
        return await asyncio.gather(
            self.study_repo.count(),
            self.record_repo.count(),
            self.user_repo.count(),
            self.patient_repo.count(),
        )

    async def _get_records_by_status(self) -> dict[str, int]:
        """Get record counts grouped by status, with all statuses initialized.

        Returns:
            Dict mapping status value to count with all statuses present.
        """
        records_by_status: dict[str, int] = {status.value: 0 for status in RecordStatus}
        counts = await self.record_repo.get_status_counts()
        records_by_status.update(counts)
        return records_by_status
