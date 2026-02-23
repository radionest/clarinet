"""Service layer for admin dashboard statistics."""

import asyncio

from src.models.base import RecordStatus
from src.repositories.patient_repository import PatientRepository
from src.repositories.record_repository import RecordRepository
from src.repositories.record_type_repository import RecordTypeRepository
from src.repositories.study_repository import StudyRepository
from src.repositories.user_repository import UserRepository


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

    async def get_total_counts(self) -> tuple[int, int, int, int]:
        """Get total counts for studies, records, users, and patients in parallel.

        Returns:
            Tuple of (total_studies, total_records, total_users, total_patients)
        """
        total_studies, total_records, total_users, total_patients = await asyncio.gather(
            self.study_repo.count(),
            self.record_repo.count(),
            self.user_repo.count(),
            self.patient_repo.count(),
        )
        return total_studies, total_records, total_users, total_patients

    async def get_records_by_status(self) -> dict[str, int]:
        """Get record counts grouped by status, with all statuses initialized.

        Returns:
            Dict mapping status value to count with all statuses present.
        """
        records_by_status: dict[str, int] = {status.value: 0 for status in RecordStatus}
        counts = await self.record_repo.get_status_counts()
        records_by_status.update(counts)
        return records_by_status

    async def get_record_type_stats(
        self,
    ) -> tuple[list, dict[str, dict[str, int]], dict[str, int]]:
        """Get record type list, per-type status counts, and per-type unique users in parallel.

        Returns:
            Tuple of (record_types, status_map, user_map)
        """
        record_types, status_map, user_map = await asyncio.gather(
            self.record_type_repo.list_all(),
            self.record_repo.get_per_type_status_counts(),
            self.record_repo.get_per_type_unique_users(),
        )
        return list(record_types), status_map, user_map
