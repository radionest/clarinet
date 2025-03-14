"""
Study router for the Clarinet framework.

This module provides API endpoints for managing medical imaging studies, series, and related data.
"""

from datetime import date
from typing import Annotated, List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from sqlmodel import Session, select, and_, not_, func

from src.exceptions import NOT_FOUND, CONFLICT
from src.models import (
    Patient,
    PatientBase,
    PatientSave,
    Study,
    StudyBase,
    StudyCreate,
    StudyRead,
    Series,
    SeriesBase,
    SeriesCreate,
    SeriesFind,
    SeriesRead,
    DicomUID,
)
from src.utils.database import get_session
from src.utils.logger import logger
from src.utils.study import choose_unique_name
from src.settings import settings

router = APIRouter()

# Patient endpoints


@router.get("/patients", response_model=List[Patient])
async def get_all_patients(session: Session = Depends(get_session)) -> List[Patient]:
    """Get all patients."""
    return session.exec(select(Patient)).all()


@router.get("/patients/{patient_id}", response_model=Patient)
async def get_patient_details(
    patient_id: str, session: Session = Depends(get_session)
) -> Patient:
    """Get patient details by ID."""
    patient = session.get(Patient, patient_id)
    if not patient:
        raise NOT_FOUND.with_context(f"Patient with ID {patient_id} not found")
    return patient


@router.post("/patients", response_model=Patient, status_code=status.HTTP_201_CREATED)
async def add_patient(
    patient: PatientSave, session: Session = Depends(get_session)
) -> Patient:
    """Create a new patient."""
    new_patient = Patient(**patient.model_dump(by_alias=True))

    try:
        session.add(new_patient)
        session.commit()
        session.refresh(new_patient)
    except Exception:
        session.rollback()
        raise CONFLICT.with_context(f"Patient with ID {patient.id} already exists")

    return new_patient


@router.post("/patients/{patient_id}/anonymize", response_model=Patient)
async def anonymize_patient(
    patient_id: str, session: Session = Depends(get_session)
) -> Patient:
    """Anonymize a patient by assigning an anonymous name."""
    patient = session.get(Patient, patient_id)
    if not patient:
        raise NOT_FOUND.with_context(f"Patient with ID {patient_id} not found")

    if patient.anon_name is not None:
        raise CONFLICT.with_context("Patient already has an anonymous name")

    anon_names_list = []
    if settings.anon_names_list:
        with open(settings.anon_names_list, "r") as f:
            anon_names_list = f.readlines()

    if anon_names_list:
        new_anon_name = choose_unique_name(anon_names_list, session)
    else:
        new_anon_name = f"{settings.anon_id_prefix}_{patient.auto_id}"

    if new_anon_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot find available name for anonymization",
        )

    patient.anon_name = new_anon_name
    session.commit()
    session.refresh(patient)
    return patient


# Study endpoints


@router.get("/studies", response_model=List[Study])
async def get_studies(session: Session = Depends(get_session)) -> List[Study]:
    """Get all studies."""
    return session.exec(select(Study)).all()


@router.get("/studies/{study_uid}", response_model=Study)
async def get_study_details(
    study_uid: DicomUID, session: Session = Depends(get_session)
) -> Study:
    """Get study details by UID."""
    study = session.get(Study, study_uid)
    if not study:
        raise NOT_FOUND.with_context(f"Study with UID {study_uid} not found")
    return study


@router.get("/studies/{study_uid}/series", response_model=List[Series])
async def get_study_series(
    study_uid: DicomUID, session: Session = Depends(get_session)
) -> List[Series]:
    """Get all series for a study."""
    study = session.get(Study, study_uid)
    if not study:
        raise NOT_FOUND.with_context(f"Study with UID {study_uid} not found")
    return study.series


@router.post("/studies", response_model=Study, status_code=status.HTTP_201_CREATED)
async def add_study(
    study: StudyCreate, session: Session = Depends(get_session)
) -> Study:
    """Create a new study."""
    # Check if patient exists
    patient = session.get(Patient, study.patient_id)
    if not patient:
        raise NOT_FOUND.with_context(f"Patient with ID {study.patient_id} not found")

    new_study = Study.model_validate(study)
    new_study.patient = patient

    try:
        session.add(new_study)
        session.commit()
        session.refresh(new_study)
    except Exception:
        session.rollback()
        raise CONFLICT.with_context(f"Study with UID {study.study_uid} already exists")

    return new_study


# Series endpoints


@router.get("/series", response_model=List[Series])
async def get_all_series(session: Session = Depends(get_session)) -> List[Series]:
    """Get all series."""
    return session.exec(select(Series)).all()


@router.get("/series/random", response_model=Series)
async def get_random_series(session: Session = Depends(get_session)) -> Series:
    """Get a random series."""
    result = session.exec(select(Series).order_by(func.random()).limit(1)).first()
    if not result:
        raise NOT_FOUND.with_context("No series found")
    return result


@router.get("/series/{series_uid}", response_model=SeriesRead)
async def get_series_details(
    series_uid: DicomUID, session: Session = Depends(get_session)
) -> Series:
    """Get series details by UID."""
    series = session.get(Series, series_uid)
    if not series:
        raise NOT_FOUND.with_context(f"Series with UID {series_uid} not found")
    return series


@router.post("/series", response_model=Series, status_code=status.HTTP_201_CREATED)
async def add_series(
    series: SeriesCreate, session: Session = Depends(get_session)
) -> Series:
    """Create a new series."""
    # Check if study exists
    study = session.get(Study, series.study_uid)
    if not study:
        raise NOT_FOUND.with_context(f"Study with UID {series.study_uid} not found")

    new_series = Series.model_validate(series)
    new_series.study = study

    try:
        session.add(new_series)
        session.commit()
        session.refresh(new_series)
    except Exception:
        session.rollback()
        raise CONFLICT.with_context(
            f"Series with UID {series.series_uid} already exists"
        )

    return new_series


@router.post("/series/find", response_model=List[SeriesRead])
async def find_series(
    find_query: SeriesFind, session: Session = Depends(get_session)
) -> List[Series]:
    """Find series by criteria."""
    find_statement = select(Series)

    # Apply find criteria
    for query_key, query_value in find_query.model_dump(
        exclude_none=True, exclude_defaults=True, exclude="tasks"
    ).items():
        match query_value:
            case "*":
                find_statement = find_statement.where(
                    getattr(Series, query_key) != None
                )
            case _:
                find_statement = find_statement.where(
                    getattr(Series, query_key) == query_value
                )

    # Apply task-related filters if present
    if find_query.tasks:
        from src.models import Task, TaskType

        find_statement = find_statement.join(Task, isouter=True)
        find_statement = find_statement.join(TaskType, isouter=True)

        # Apply task filters (simplified for now)
        # Full implementation would need to handle TaskFind conditions

    results = session.exec(find_statement.distinct()).all()
    return results


@router.post("/studies/{study_uid}/add_anonymized", response_model=Study)
async def add_anonymized_study(
    study_uid: DicomUID, anon_uid: DicomUID, session: Session = Depends(get_session)
) -> Study:
    """Add anonymized UID to a study."""
    study = session.get(Study, study_uid)
    if not study:
        raise NOT_FOUND.with_context(f"Study with UID {study_uid} not found")

    study.anon_uid = anon_uid
    session.commit()
    session.refresh(study)
    return study


@router.post("/series/{series_uid}/add_anonymized", response_model=Series)
async def add_anonymized_series(
    series_uid: DicomUID, anon_uid: DicomUID, session: Session = Depends(get_session)
) -> Series:
    """Add anonymized UID to a series."""
    series = session.get(Series, series_uid)
    if not series:
        raise NOT_FOUND.with_context(f"Series with UID {series_uid} not found")

    series.anon_uid = anon_uid
    session.commit()
    session.refresh(series)
    return series
