"""Repository for Patient-specific database operations.

NOTE: selectinload() calls use ``# type: ignore`` — mypy cannot resolve
SQLAlchemy InstrumentedAttribute on SQLModel classes (known limitation).
"""

from collections.abc import Sequence

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.exceptions.domain import DatabaseIntegrityError, PatientNotFoundError
from clarinet.models import Patient, Study
from clarinet.models.counter import AutoIdCounter, patient_auto_id_seq
from clarinet.repositories.base import BaseRepository
from clarinet.settings import DatabaseDriver, settings
from clarinet.utils.logger import logger

_MAX_AUTO_ID_RETRIES = 3
_COUNTER_NAME = "patient_auto_id"


class PatientRepository(BaseRepository[Patient]):
    """Repository for Patient model operations."""

    def __init__(self, session: AsyncSession):
        """Initialize patient repository with session."""
        super().__init__(session, Patient)

    async def create(self, entity: Patient) -> Patient:
        """Create patient, auto-assigning auto_id if not provided."""
        if entity.auto_id is not None:
            await self._advance_counter(entity.auto_id)
            return await super().create(entity)

        for attempt in range(1, _MAX_AUTO_ID_RETRIES + 1):
            entity.auto_id = await self._next_auto_id()
            try:
                async with self.session.begin_nested():
                    self.session.add(entity)
                    await self.session.flush()
                await self.session.refresh(entity)
                return entity
            except IntegrityError as exc:
                logger.warning(
                    f"auto_id conflict on attempt {attempt}/{_MAX_AUTO_ID_RETRIES}: {exc}"
                )
                if attempt == _MAX_AUTO_ID_RETRIES:
                    raise DatabaseIntegrityError(
                        f"Failed to assign unique auto_id after {_MAX_AUTO_ID_RETRIES} attempts"
                    ) from exc
                if entity in self.session:
                    self.session.expunge(entity)

        raise DatabaseIntegrityError("Failed to assign unique auto_id")  # unreachable

    async def _next_auto_id(self) -> int:
        """Atomically increment and return the next patient auto_id.

        PostgreSQL: native sequence (nextval).
        SQLite: counter table with lazy seeding from MAX(Patient.auto_id).
        """
        if settings.database_driver != DatabaseDriver.SQLITE:
            result = await self.session.execute(select(patient_auto_id_seq.next_value()))
            return result.scalar_one()

        # SQLite fallback: counter table
        stmt = select(AutoIdCounter).where(AutoIdCounter.name == _COUNTER_NAME)
        counter_result = await self.session.execute(stmt)
        counter: AutoIdCounter | None = counter_result.scalar_one_or_none()  # type: ignore[assignment]

        if counter is None:
            max_result = await self.session.execute(
                select(func.coalesce(func.max(Patient.auto_id), 0))
            )
            seed: int = max_result.scalar_one()  # type: ignore[assignment]
            counter = AutoIdCounter(name=_COUNTER_NAME, last_value=seed)
            self.session.add(counter)

        counter.last_value += 1
        await self.session.flush()
        return counter.last_value

    async def _advance_counter(self, value: int) -> None:
        """Advance counter/sequence to at least ``value`` to prevent future collisions."""
        if settings.database_driver != DatabaseDriver.SQLITE:
            seq = patient_auto_id_seq.name
            await self.session.execute(
                text(f"SELECT setval('{seq}', GREATEST(:val, (SELECT last_value FROM {seq})))"),
                {"val": value},
            )
            return

        # SQLite fallback
        stmt = select(AutoIdCounter).where(AutoIdCounter.name == _COUNTER_NAME)
        counter_result = await self.session.execute(stmt)
        counter: AutoIdCounter | None = counter_result.scalar_one_or_none()  # type: ignore[assignment]

        if counter is None:
            max_result = await self.session.execute(
                select(func.coalesce(func.max(Patient.auto_id), 0))
            )
            current_max: int = max_result.scalar_one()  # type: ignore[assignment]
            counter = AutoIdCounter(name=_COUNTER_NAME, last_value=max(value, current_max))
            self.session.add(counter)
        elif counter.last_value < value:
            counter.last_value = value
        await self.session.flush()

    async def get_all_with_studies(self, skip: int = 0, limit: int = 100) -> Sequence[Patient]:
        """Get all patients with studies loaded.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of patients with studies loaded
        """
        statement = select(Patient).options(
            selectinload(Patient.studies).selectinload(Study.series),  # type: ignore[arg-type]
        )
        statement = self._paginate(statement, skip, limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_with_studies(self, patient_id: str) -> Patient:
        """Get patient with studies loaded.

        Args:
            patient_id: Patient ID

        Returns:
            Patient with studies loaded

        Raises:
            PatientNotFoundError: If patient doesn't exist.
        """
        statement = (
            select(Patient)
            .where(Patient.id == patient_id)
            .options(
                selectinload(Patient.studies).selectinload(Study.series),  # type: ignore[arg-type]
            )
        )
        result = await self.session.execute(statement)
        patient = result.scalars().first()

        if not patient:
            raise PatientNotFoundError(patient_id)

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
            select(Patient).where(Patient.name.ilike(f"%{name}%"))  # type: ignore
        )
        statement = self._paginate(statement, skip, limit)
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

        statement = self._paginate(statement, skip, limit)
        result = await self.session.execute(statement)
        return result.scalars().all()
