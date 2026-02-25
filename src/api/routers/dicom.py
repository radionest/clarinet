"""DICOM API router for PACS query and import operations."""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Request

from src.api.dependencies import (
    DicomClientDep,
    PacsNodeDep,
    StudyRepositoryDep,
    StudyServiceDep,
    SuperUserDep,
)
from src.models.study import StudyRead
from src.services.dicom.models import (
    PacsImportRequest,
    PacsStudyWithSeries,
    SeriesQuery,
    StudyQuery,
)
from src.utils.logger import logger

router = APIRouter()


@router.get("/patient/{patient_id}/studies", response_model=list[PacsStudyWithSeries])
async def search_patient_studies(
    patient_id: str,
    _user: SuperUserDep,
    client: DicomClientDep,
    pacs: PacsNodeDep,
    study_repo: StudyRepositoryDep,
) -> list[PacsStudyWithSeries]:
    """Query PACS for studies belonging to a patient.

    Args:
        patient_id: Patient ID to search in PACS
        _user: Authenticated superuser
        client: DICOM client
        pacs: PACS node configuration
        study_repo: Study repository for checking local existence

    Returns:
        List of PACS studies with series and local existence flag
    """
    studies = await client.find_studies(
        query=StudyQuery(patient_id=patient_id),
        peer=pacs,
    )

    if not studies:
        return []

    # Query series for all studies in parallel
    series_tasks = [
        client.find_series(
            query=SeriesQuery(study_instance_uid=s.study_instance_uid),
            peer=pacs,
        )
        for s in studies
    ]
    all_series = await asyncio.gather(*series_tasks)

    # Check local DB existence for each study
    results: list[PacsStudyWithSeries] = []
    for study, series_list in zip(studies, all_series):
        exists = await study_repo.get_optional(study.study_instance_uid) is not None
        results.append(
            PacsStudyWithSeries(
                study=study,
                series=series_list,
                already_exists=exists,
            )
        )

    logger.info(f"PACS search for patient {patient_id}: {len(results)} studies found")
    return results


@router.post("/import-study", response_model=StudyRead)
async def import_study_from_pacs(
    http_request: Request,
    background_tasks: BackgroundTasks,
    request: PacsImportRequest,
    _user: SuperUserDep,
    client: DicomClientDep,
    pacs: PacsNodeDep,
    service: StudyServiceDep,
) -> object:
    """Import a study and its series from PACS into the local database.

    Args:
        request: Import request with study UID and patient ID
        _user: Authenticated superuser
        client: DICOM client
        pacs: PACS node configuration
        service: Study service for creating studies and series

    Returns:
        Created study with all series
    """
    # Fetch study metadata from PACS
    studies = await client.find_studies(
        query=StudyQuery(
            study_instance_uid=request.study_instance_uid,
            patient_id=request.patient_id,
        ),
        peer=pacs,
    )

    if not studies:
        from src.exceptions import NOT_FOUND

        raise NOT_FOUND.with_context(f"Study {request.study_instance_uid} not found in PACS")

    pacs_study = studies[0]

    # Parse DICOM date (YYYYMMDD) to Python date
    study_date = datetime.now(tz=UTC).date()
    if pacs_study.study_date:
        try:
            study_date = (
                datetime.strptime(pacs_study.study_date, "%Y%m%d").replace(tzinfo=UTC).date()
            )
        except ValueError:
            logger.warning(f"Invalid DICOM date '{pacs_study.study_date}', using today's date")

    # Create study in local DB
    await service.create_study(
        {
            "study_uid": request.study_instance_uid,
            "date": study_date,
            "patient_id": request.patient_id,
        }
    )

    # Fetch series from PACS
    pacs_series = await client.find_series(
        query=SeriesQuery(study_instance_uid=request.study_instance_uid),
        peer=pacs,
    )

    # Create each series in local DB
    engine = getattr(http_request.app.state, "recordflow_engine", None)
    for idx, s in enumerate(pacs_series):
        await service.create_series(
            {
                "series_uid": s.series_instance_uid,
                "series_description": s.series_description,
                "series_number": s.series_number or (idx + 1),
                "study_uid": request.study_instance_uid,
            }
        )
        if engine:
            background_tasks.add_task(
                engine.handle_entity_created,
                "series",
                request.patient_id,
                request.study_instance_uid,
                s.series_instance_uid,
            )

    # Return full study with relations
    logger.info(f"Imported study {request.study_instance_uid} with {len(pacs_series)} series")
    return await service.get_study(request.study_instance_uid)
