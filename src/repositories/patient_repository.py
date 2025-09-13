"""Repository for Patient-specific database operations."""

from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from src.models import Patient, Study
from src.repositories.base import BaseRepository


class PatientRepository(BaseRepository[Patient]):
    """Repository for Patient model operations."""

    def __init__(self, session: AsyncSession):
        """Initialize patient repository with session."""
        super().__init__(session, Patient)

    async def get_with_studies(self, patient_id: str) -> Patient:
        """Get patient with studies loaded.

        Args:
            patient_id: Patient ID

        Returns:
            Patient with studies loaded

        Raises:
            NOT_FOUND: If patient doesn't exist
        """
        statement = (
            select(Patient).where(Patient.id == patient_id).options(selectinload("studies"))  # type: ignore
        )
        result = await self.session.execute(statement)
        patient = result.scalars().first()

        if not patient:
            from src.exceptions import NOT_FOUND

            raise NOT_FOUND.with_context(f"Patient {patient_id} not found")

        return patient

    async def find_by_name(self, name: str, skip: int = 0, limit: int = 100) -> Sequence[Patient]:
        """Find patients by name.

        Args:
            name: Name pattern to search
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of patients
        """
        statement = (
            select(Patient)
            .where(Patient.name.ilike(f"%{name}%"))  # type: ignore
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def find_by_anon_name(self, anon_name: str) -> Patient | None:
        """Find patient by anonymous name.

        Args:
            anon_name: Anonymous name to search

        Returns:
            Patient if found, None otherwise
        """
        statement = select(Patient).where(Patient.anon_name == anon_name)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def find_by_id(self, patient_id: str) -> Patient | None:
        """Find patient by ID.

        Args:
            patient_id: Patient ID

        Returns:
            Patient if found, None otherwise
        """
        statement = select(Patient).where(Patient.id == patient_id)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_studies(self, patient_id: str) -> list[Study]:
        """Get all studies for a patient.

        Args:
            patient_id: Patient ID

        Returns:
            List of studies
        """
        patient = await self.get(patient_id)
        await self.session.refresh(patient, ["studies"])
        return list(patient.studies)

    async def count_studies(self, patient_id: str) -> int:
        """Count studies for a patient.

        Args:
            patient_id: Patient ID

        Returns:
            Number of studies
        """
        statement = select(func.count()).select_from(Study).where(Study.patient_id == patient_id)
        result = await self.session.execute(statement)
        return result.scalar() or 0

    async def exists_anon_name(self, anon_name: str) -> bool:
        """Check if anonymous name exists.

        Args:
            anon_name: Anonymous name to check

        Returns:
            True if name exists
        """
        return await self.exists(anon_name=anon_name)

    async def update_anon_name(self, patient: Patient, anon_name: str) -> Patient:
        """Update patient's anonymous name.

        Args:
            patient: Patient to update
            anon_name: New anonymous name

        Returns:
            Updated patient
        """
        patient.anon_name = anon_name
        await self.session.commit()
        await self.session.refresh(patient)
        return patient

    async def search(
        self,
        query: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Patient]:
        """Search patients with optional query.

        Args:
            query: Search query for patient id, name or anonymous name
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of matching patients
        """
        statement = select(Patient)

        if query:
            statement = statement.where(
                (Patient.id.ilike(f"%{query}%"))  # type: ignore
                | (Patient.name.ilike(f"%{query}%"))  # type: ignore
                | (Patient.anon_name.ilike(f"%{query}%"))  # type: ignore
            )

        statement = statement.offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()
