"""
Async study router for the Clarinet framework.

This module provides async API endpoints for managing medical imaging studies, series, and related data.
"""

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from src.exceptions import CONFLICT, NOT_FOUND
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
from src.models.study import SeriesFind
from src.settings import settings
from src.utils.async_crud import add_item_async, exists_async
from src.utils.database import get_async_session

router = APIRouter()

# Patient endpoints


@router.get("/patients", response_model=list[Patient])
async def get_all_patients(session: AsyncSession = Depends(get_async_session)) -> list[Patient]:
    """Get all patients."""
    result = await session.execute(select(Patient))
    return list(result.scalars().all())


@router.get("/patients/{patient_id}", response_model=Patient)
async def get_patient_details(
    patient_id: str, session: AsyncSession = Depends(get_async_session)
) -> Patient:
    """Get patient details by ID."""
    patient = await session.get(Patient, patient_id)
    if not patient:
        raise NOT_FOUND.with_context(f"Patient with ID {patient_id} not found")
    return patient


@router.post("/patients", response_model=Patient, status_code=status.HTTP_201_CREATED)
async def add_patient(
    patient: PatientSave, session: AsyncSession = Depends(get_async_session)
) -> Patient:
    """Create a new patient."""
    # Check if patient already exists
    if await exists_async(Patient, session, id=patient.id):
        raise CONFLICT.with_context(f"Patient with ID {patient.id} already exists")

    new_patient = Patient(**patient.model_dump(by_alias=True))
    return await add_item_async(new_patient, session)


@router.post("/patients/{patient_id}/anonymize", response_model=Patient)
async def anonymize_patient(
    patient_id: str, session: AsyncSession = Depends(get_async_session)
) -> Patient:
    """Anonymize a patient by assigning an anonymous name."""
    patient = await session.get(Patient, patient_id)
    if not patient:
        raise NOT_FOUND.with_context(f"Patient with ID {patient_id} not found")

    if patient.anon_name is not None:
        raise CONFLICT.with_context("Patient already has an anonymous name")

    anon_names_list = []
    if settings.anon_names_list:
        async with aiofiles.open(settings.anon_names_list) as f:
            anon_names_list = await f.readlines()

    if anon_names_list:
        # Note: choose_unique_name needs to be made async-compatible
        # For now, we'll use a simpler approach
        import random

        new_anon_name = random.choice(anon_names_list).strip()

        # Check if name is already used
        result = await session.execute(select(Patient).where(Patient.anon_name == new_anon_name))
        if result.scalars().first():
            # If name is taken, fall back to auto-generated name
            new_anon_name = f"{settings.anon_id_prefix}_{patient.auto_id}"
    else:
        new_anon_name = f"{settings.anon_id_prefix}_{patient.auto_id}"

    if new_anon_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot find available name for anonymization",
        )

    patient.anon_name = new_anon_name
    await session.commit()
    await session.refresh(patient)
    return patient


# Study endpoints


@router.get("/studies", response_model=list[Study])
async def get_studies(session: AsyncSession = Depends(get_async_session)) -> list[Study]:
    """Get all studies."""
    result = await session.execute(select(Study))
    return list(result.scalars().all())


@router.get("/studies/{study_uid}", response_model=Study)
async def get_study_details(
    study_uid: DicomUID, session: AsyncSession = Depends(get_async_session)
) -> Study:
    """Get study details by UID."""
    study = await session.get(Study, study_uid)
    if not study:
        raise NOT_FOUND.with_context(f"Study with UID {study_uid} not found")
    return study


@router.get("/studies/{study_uid}/series", response_model=list[Series])
async def get_study_series(
    study_uid: DicomUID, session: AsyncSession = Depends(get_async_session)
) -> list[Series]:
    """Get all series for a study."""
    study = await session.get(Study, study_uid)
    if not study:
        raise NOT_FOUND.with_context(f"Study with UID {study_uid} not found")

    # Load series relationship
    await session.refresh(study, ["series"])
    return study.series


@router.post("/studies", response_model=Study, status_code=status.HTTP_201_CREATED)
async def add_study(
    study: StudyCreate, session: AsyncSession = Depends(get_async_session)
) -> Study:
    """Create a new study."""
    # Check if patient exists
    patient = await session.get(Patient, study.patient_id)
    if not patient:
        raise NOT_FOUND.with_context(f"Patient with ID {study.patient_id} not found")

    # Check if study already exists
    if await exists_async(Study, session, study_uid=study.study_uid):
        raise CONFLICT.with_context(f"Study with UID {study.study_uid} already exists")

    new_study = Study.model_validate(study)
    new_study.patient = patient

    return await add_item_async(new_study, session)


# Series endpoints


@router.get("/series", response_model=list[Series])
async def get_all_series(session: AsyncSession = Depends(get_async_session)) -> list[Series]:
    """Get all series."""
    result = await session.execute(select(Series))
    return list(result.scalars().all())


@router.get("/series/random", response_model=Series)
async def get_random_series(session: AsyncSession = Depends(get_async_session)) -> Series:
    """Get a random series."""
    result = await session.execute(select(Series).order_by(func.random()).limit(1))
    series = result.scalars().first()
    if not series:
        raise NOT_FOUND.with_context("No series found")
    return series


@router.get("/series/{series_uid}", response_model=SeriesRead)
async def get_series_details(
    series_uid: DicomUID, session: AsyncSession = Depends(get_async_session)
) -> Series:
    """Get series details by UID."""
    series = await session.get(Series, series_uid)
    if not series:
        raise NOT_FOUND.with_context(f"Series with UID {series_uid} not found")
    return series


@router.post("/series", response_model=Series, status_code=status.HTTP_201_CREATED)
async def add_series(
    series: SeriesCreate, session: AsyncSession = Depends(get_async_session)
) -> Series:
    """Create a new series."""
    # Check if study exists
    study = await session.get(Study, series.study_uid)
    if not study:
        raise NOT_FOUND.with_context(f"Study with UID {series.study_uid} not found")

    # Check if series already exists
    if await exists_async(Series, session, series_uid=series.series_uid):
        raise CONFLICT.with_context(f"Series with UID {series.series_uid} already exists")

    new_series = Series.model_validate(series)
    new_series.study = study

    return await add_item_async(new_series, session)


@router.post("/series/find", response_model=list[SeriesRead])
async def find_series(
    find_query: SeriesFind, session: AsyncSession = Depends(get_async_session)
) -> list[Series]:
    """Find series by criteria."""
    find_statement = select(Series)

    # Apply find criteria
    for query_key, query_value in find_query.model_dump(
        exclude_none=True, exclude_defaults=True, exclude={"tasks"}
    ).items():
        match query_value:
            case "*":
                find_statement = find_statement.where(getattr(Series, query_key) is not None)
            case _:
                find_statement = find_statement.where(getattr(Series, query_key) == query_value)

    # Apply task-related filters if present
    if find_query.tasks:
        from src.models import Task, TaskDesign

        find_statement = find_statement.join(Task, isouter=True)
        find_statement = find_statement.join(TaskDesign, isouter=True)

        # Apply task filters (simplified for now)
        # Full implementation would need to handle TaskFind conditions

    result = await session.execute(find_statement.distinct())
    results = result.scalars().all()
    return list(results)


@router.post("/studies/{study_uid}/add_anonymized", response_model=Study)
async def add_anonymized_study(
    study_uid: DicomUID, anon_uid: DicomUID, session: AsyncSession = Depends(get_async_session)
) -> Study:
    """Add anonymized UID to a study."""
    study = await session.get(Study, study_uid)
    if not study:
        raise NOT_FOUND.with_context(f"Study with UID {study_uid} not found")

    study.anon_uid = anon_uid
    await session.commit()
    await session.refresh(study)
    return study


@router.post("/series/{series_uid}/add_anonymized", response_model=Series)
async def add_anonymized_series(
    series_uid: DicomUID, anon_uid: DicomUID, session: AsyncSession = Depends(get_async_session)
) -> Series:
    """Add anonymized UID to a series."""
    series = await session.get(Series, series_uid)
    if not series:
        raise NOT_FOUND.with_context(f"Series with UID {series_uid} not found")

    series.anon_uid = anon_uid
    await session.commit()
    await session.refresh(series)
    return series
