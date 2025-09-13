"""Service layer for study-related business logic."""

import random
from typing import Any

from src.exceptions import CONFLICT
from src.models import Patient, Series, Study
from src.models.study import SeriesFind
from src.repositories.patient_repository import PatientRepository
from src.repositories.series_repository import SeriesRepository
from src.repositories.study_repository import StudyRepository
from src.services.providers import AnonymousNameProvider
from src.settings import settings


class StudyService:
    """Service for study-related business logic."""

    def __init__(
        self,
        study_repo: StudyRepository,
        patient_repo: PatientRepository,
        series_repo: SeriesRepository,
        name_provider: AnonymousNameProvider | None = None,
    ):
        """Initialize study service with repositories.

        Args:
            study_repo: Study repository instance
            patient_repo: Patient repository instance
            series_repo: Series repository instance
            name_provider: Optional anonymous name provider
        """
        self.study_repo = study_repo
        self.patient_repo = patient_repo
        self.series_repo = series_repo
        self.name_provider = name_provider or AnonymousNameProvider(settings.anon_names_list)

    # Patient operations

    async def get_all_patients(self, skip: int = 0, limit: int = 100) -> list[Patient]:
        """Get all patients with pagination.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of patients
        """
        patients = await self.patient_repo.get_all(skip=skip, limit=limit)
        return list(patients)

    async def get_patient(self, patient_id: str) -> Patient:
        """Get patient by ID.

        Args:
            patient_id: Patient ID

        Returns:
            Patient object

        Raises:
            NOT_FOUND: If patient doesn't exist
        """
        return await self.patient_repo.get(patient_id)

    async def create_patient(self, patient_data: dict[str, Any]) -> Patient:
        """Create new patient.

        Args:
            patient_data: Patient data dictionary

        Returns:
            Created patient

        Raises:
            CONFLICT: If patient already exists
        """
        # Check if patient exists
        if await self.patient_repo.exists(id=patient_data["id"]):
            raise CONFLICT.with_context(f"Patient with ID {patient_data['id']} already exists")

        patient = Patient(**patient_data)
        return await self.patient_repo.create(patient)

    async def anonymize_patient(self, patient_id: str) -> Patient:
        """Anonymize patient by assigning anonymous name.

        Args:
            patient_id: Patient ID

        Returns:
            Anonymized patient

        Raises:
            NOT_FOUND: If patient doesn't exist
            CONFLICT: If patient already has anonymous name
        """
        patient = await self.patient_repo.get(patient_id)

        # Check if already anonymized
        if patient.anon_name is not None:
            raise CONFLICT.with_context("Patient already has an anonymous name")

        # Generate anonymous name
        anon_name = await self._generate_anonymous_name(patient)

        if anon_name is None:
            raise CONFLICT.with_context("Cannot find available name for anonymization")

        # Update patient with anonymous name
        return await self.patient_repo.update_anon_name(patient, anon_name)

    async def _generate_anonymous_name(self, patient: Patient) -> str | None:
        """Generate unique anonymous name for patient.

        Args:
            patient: Patient to anonymize

        Returns:
            Generated anonymous name or None if failed
        """
        anon_names_list = await self.name_provider.get_available_names()

        if anon_names_list:
            available_names = []
            for name in anon_names_list:
                name = name.strip()
                if name and not await self.patient_repo.exists_anon_name(name):
                    available_names.append(name)

            if available_names:
                return random.choice(available_names)

        return f"{settings.anon_id_prefix}_{patient.auto_id}"

    # Study operations

    async def get_all_studies(self, skip: int = 0, limit: int = 100) -> list[Study]:
        """Get all studies with pagination.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of studies
        """
        studies = await self.study_repo.get_all(skip=skip, limit=limit)
        return list(studies)

    async def get_study(self, study_uid: str) -> Study:
        """Get study by UID.

        Args:
            study_uid: Study UID

        Returns:
            Study object

        Raises:
            NOT_FOUND: If study doesn't exist
        """
        return await self.study_repo.get(study_uid)

    async def get_study_series(self, study_uid: str) -> list[Series]:
        """Get all series for a study.

        Args:
            study_uid: Study UID

        Returns:
            List of series

        Raises:
            NOT_FOUND: If study doesn't exist
        """
        study = await self.study_repo.get_with_series(study_uid)
        return list(study.series)

    async def create_study(self, study_data: dict[str, Any]) -> Study:
        """Create new study.

        Args:
            study_data: Study data dictionary

        Returns:
            Created study

        Raises:
            NOT_FOUND: If patient doesn't exist
            CONFLICT: If study already exists
        """
        # Check if patient exists
        patient = await self.patient_repo.get(study_data["patient_id"])

        # Check if study already exists
        if await self.study_repo.exists(study_uid=study_data["study_uid"]):
            raise CONFLICT.with_context(f"Study with UID {study_data['study_uid']} already exists")

        study = Study(**study_data)
        study.patient = patient
        return await self.study_repo.create(study)

    async def add_anonymized_study_uid(self, study_uid: str, anon_uid: str) -> Study:
        """Add anonymized UID to study.

        Args:
            study_uid: Original study UID
            anon_uid: Anonymized UID

        Returns:
            Updated study

        Raises:
            NOT_FOUND: If study doesn't exist
        """
        study = await self.study_repo.get(study_uid)
        return await self.study_repo.update(study, {"anon_uid": anon_uid})

    # Series operations

    async def get_all_series(self, skip: int = 0, limit: int = 100) -> list[Series]:
        """Get all series with pagination.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of series
        """
        series = await self.series_repo.get_all(skip=skip, limit=limit)
        return list(series)

    async def get_series(self, series_uid: str) -> Series:
        """Get series by UID.

        Args:
            series_uid: Series UID

        Returns:
            Series object

        Raises:
            NOT_FOUND: If series doesn't exist
        """
        return await self.series_repo.get(series_uid)

    async def get_random_series(self) -> Series:
        """Get a random series.

        Returns:
            Random series

        Raises:
            NOT_FOUND: If no series exist
        """
        return await self.series_repo.get_random()

    async def create_series(self, series_data: dict[str, Any]) -> Series:
        """Create new series.

        Args:
            series_data: Series data dictionary

        Returns:
            Created series

        Raises:
            NOT_FOUND: If study doesn't exist
            CONFLICT: If series already exists
        """
        # Check if study exists
        study = await self.study_repo.get(series_data["study_uid"])

        # Check if series already exists
        if await self.series_repo.exists(series_uid=series_data["series_uid"]):
            raise CONFLICT.with_context(
                f"Series with UID {series_data['series_uid']} already exists"
            )

        series = Series(**series_data)
        series.study = study
        return await self.series_repo.create(series)

    async def find_series(self, find_query: SeriesFind) -> list[Series]:
        """Find series by criteria.

        Args:
            find_query: Search criteria

        Returns:
            List of matching series
        """
        return await self.series_repo.find_by_criteria(find_query)

    async def add_anonymized_series_uid(self, series_uid: str, anon_uid: str) -> Series:
        """Add anonymized UID to series.

        Args:
            series_uid: Original series UID
            anon_uid: Anonymized UID

        Returns:
            Updated series

        Raises:
            NOT_FOUND: If series doesn't exist
        """
        series = await self.series_repo.get(series_uid)
        return await self.series_repo.update(series, {"anon_uid": anon_uid})
