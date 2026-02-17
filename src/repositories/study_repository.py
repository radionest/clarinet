"""Repository for Study-specific database operations."""

from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from src.models import Patient, Record, Study
from src.repositories.base import BaseRepository


class StudyRepository(BaseRepository[Study]):
    """Repository for Study model operations."""

    def __init__(self, session: AsyncSession):
        """Initialize study repository with session."""
        super().__init__(session, Study)

    async def get_with_relations(self, study_id: str) -> Study:
        """Get study with all relations loaded.

        Args:
            study_id: Study ID

        Returns:
            Study with relations loaded

        Raises:
            NOT_FOUND: If study doesn't exist
        """
        statement = (
            select(Study)
            .where(Study.study_uid == study_id)
            .options(
                selectinload(Study.patient),  # type: ignore
                selectinload(Study.series),  # type: ignore
                selectinload(Study.records),  # type: ignore
            )
        )
        result = await self.session.execute(statement)
        study = result.scalars().first()

        if not study:
            from src.exceptions import NOT_FOUND

            raise NOT_FOUND.with_context(f"Study {study_id} not found")

        return study

    async def find_by_patient(
        self, patient_id: str, skip: int = 0, limit: int = 100
    ) -> Sequence[Study]:
        """Find all studies for a patient.

        Args:
            patient_id: Patient ID
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of studies for the patient
        """
        statement = select(Study).where(Study.patient_id == patient_id).offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_patient(self, study_id: str) -> Patient:
        """Get patient for a study.

        Args:
            study_id: Study ID

        Returns:
            Patient object
        """
        study = await self.get(study_id)
        await self.session.refresh(study, ["patient"])
        return study.patient

    async def get_series(self, study_id: str) -> list:
        """Get all series for a study.

        Args:
            study_id: Study ID

        Returns:
            List of series
        """
        study = await self.get(study_id)
        await self.session.refresh(study, ["series"])
        return list(study.series)

    async def has_record(self, study: Study, record_id: int) -> bool:
        """Check if record is in study.

        Args:
            study: Study to check
            record_id: Record ID to check

        Returns:
            True if record is in study
        """
        await self.session.refresh(study, ["records"])
        return any(record.id == record_id for record in study.records)

    async def get_by_uid(self, study_uid: str) -> Study:
        """Get study by UID.

        Args:
            study_uid: Study UID

        Returns:
            Study object
        """
        statement = select(Study).where(Study.study_uid == study_uid)
        result = await self.session.execute(statement)
        study = result.scalars().first()
        if not study:
            from src.exceptions import NOT_FOUND

            raise NOT_FOUND.with_context(f"Study {study_uid} not found")
        return study

    async def get_records(self, study_id: str) -> list[Record]:
        """Get all records for a study.

        Args:
            study_id: Study ID

        Returns:
            List of records
        """
        study = await self.get(study_id)
        await self.session.refresh(study, ["records"])
        return list(study.records)

    async def search(
        self,
        patient_id: str | None = None,
        study_uid: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Study]:
        """Search studies with optional filters.

        Args:
            patient_id: Filter by patient ID
            study_uid: Filter by study UID
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of matching studies
        """
        statement = select(Study)

        if patient_id:
            statement = statement.where(Study.patient_id == patient_id)

        if study_uid:
            statement = statement.where(Study.study_uid == study_uid)

        statement = statement.offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def count_series(self, study_id: str) -> int:
        """Count series in a study.

        Args:
            study_id: Study ID

        Returns:
            Number of series
        """
        from src.models import Series

        statement = select(func.count()).select_from(Series).where(Series.study_uid == study_id)
        result = await self.session.execute(statement)
        return result.scalar() or 0

    async def count_records(self, study_id: str) -> int:
        """Count records in a study.

        Args:
            study_id: Study ID

        Returns:
            Number of records
        """
        statement = select(func.count()).select_from(Record).where(Record.study_uid == study_id)
        result = await self.session.execute(statement)
        return result.scalar() or 0

    async def get_with_series(self, study_uid: str) -> Study:
        """Get study with loaded series.

        Args:
            study_uid: Study UID

        Returns:
            Study with series loaded

        Raises:
            NOT_FOUND: If study doesn't exist
        """
        study = await self.get_by_uid(study_uid)
        await self.session.refresh(study, ["series"])
        return study
