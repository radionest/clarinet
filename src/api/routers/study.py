"""
Async study router for the Clarinet framework.

This module provides async API endpoints for managing medical imaging studies, series, and related data.
"""

from fastapi import APIRouter, status

from src.api.dependencies import StudyServiceDep
from src.models import (
    DicomUID,
    Patient,
    PatientSave,
    Series,
    SeriesCreate,
    SeriesRead,
    Study,
    StudyCreate,
)
from src.models.patient import PatientRead
from src.models.study import SeriesFind, StudyRead

router = APIRouter()


# Patient endpoints


@router.get("/patients", response_model=list[PatientRead])
async def get_all_patients(
    service: StudyServiceDep,
) -> list[Patient]:
    """Get all patients with their studies."""
    return await service.get_all_patients()


@router.get("/patients/{patient_id}", response_model=PatientRead)
async def get_patient_details(
    patient_id: str,
    service: StudyServiceDep,
) -> Patient:
    """Get patient details by ID with their studies."""
    return await service.get_patient(patient_id)


@router.post("/patients", response_model=Patient, status_code=status.HTTP_201_CREATED)
async def add_patient(
    patient: PatientSave,
    service: StudyServiceDep,
) -> Patient:
    """Create a new patient."""
    patient_data = patient.model_dump()
    return await service.create_patient(patient_data)


@router.post("/patients/{patient_id}/anonymize", response_model=Patient)
async def anonymize_patient(
    patient_id: str,
    service: StudyServiceDep,
) -> Patient:
    """Anonymize a patient by assigning an anonymous name."""
    return await service.anonymize_patient(patient_id)


# Study endpoints


@router.get("/studies", response_model=list[StudyRead])
async def get_studies(
    service: StudyServiceDep,
) -> list[Study]:
    """Get all studies with their relations."""
    return await service.get_all_studies()


@router.get("/studies/{study_uid}", response_model=StudyRead)
async def get_study_details(
    study_uid: DicomUID,
    service: StudyServiceDep,
) -> Study:
    """Get study details by UID with relations."""
    return await service.get_study(study_uid)


@router.get("/studies/{study_uid}/series", response_model=list[Series])
async def get_study_series(
    study_uid: DicomUID,
    service: StudyServiceDep,
) -> list[Series]:
    """Get all series for a study."""
    return await service.get_study_series(study_uid)


@router.post("/studies", response_model=Study, status_code=status.HTTP_201_CREATED)
async def add_study(
    study: StudyCreate,
    service: StudyServiceDep,
) -> Study:
    """Create a new study."""
    study_data = study.model_dump()
    return await service.create_study(study_data)


# Series endpoints


@router.get("/series", response_model=list[Series])
async def get_all_series(
    service: StudyServiceDep,
) -> list[Series]:
    """Get all series."""
    return await service.get_all_series()


@router.get("/series/random", response_model=Series)
async def get_random_series(
    service: StudyServiceDep,
) -> Series:
    """Get a random series."""
    return await service.get_random_series()


@router.get("/series/{series_uid}", response_model=SeriesRead)
async def get_series_details(
    series_uid: DicomUID,
    service: StudyServiceDep,
) -> Series:
    """Get series details by UID."""
    return await service.get_series(series_uid)


@router.post("/series", response_model=Series, status_code=status.HTTP_201_CREATED)
async def add_series(
    series: SeriesCreate,
    service: StudyServiceDep,
) -> Series:
    """Create a new series."""
    series_data = series.model_dump()
    return await service.create_series(series_data)


@router.post("/series/find", response_model=list[SeriesRead])
async def find_series(
    find_query: SeriesFind,
    service: StudyServiceDep,
) -> list[Series]:
    """Find series by criteria."""
    return await service.find_series(find_query)


@router.post("/studies/{study_uid}/add_anonymized", response_model=Study)
async def add_anonymized_study(
    study_uid: DicomUID,
    anon_uid: DicomUID,
    service: StudyServiceDep,
) -> Study:
    """Add anonymized UID to a study."""
    return await service.add_anonymized_study_uid(study_uid, anon_uid)


@router.post("/series/{series_uid}/add_anonymized", response_model=Series)
async def add_anonymized_series(
    series_uid: DicomUID,
    anon_uid: DicomUID,
    service: StudyServiceDep,
) -> Series:
    """Add anonymized UID to a series."""
    return await service.add_anonymized_series_uid(series_uid, anon_uid)
