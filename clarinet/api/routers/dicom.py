"""DICOM API router for PACS query and import operations."""

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from clarinet.api.dependencies import (
    AnonymizationServiceDep,
    DicomClientDep,
    PacsNodeDep,
    StudyRepositoryDep,
    StudyServiceDep,
    SuperUserDep,
)
from clarinet.models.study import StudyRead
from clarinet.services.dicom.models import (
    AnonymizationResult,
    AnonymizeStudyRequest,
    BackgroundAnonymizationStatus,
    PacsImportRequest,
    PacsStudyWithSeries,
    SeriesQuery,
    StudyQuery,
)
from clarinet.services.dicom.series_filter import SeriesFilter, SeriesFilterCriteria
from clarinet.settings import settings
from clarinet.utils.dicom import parse_dicom_date
from clarinet.utils.logger import logger

router = APIRouter()

# Strong references for fire-and-forget background tasks (prevents GC before completion)
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


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
        from clarinet.exceptions import NOT_FOUND

        raise NOT_FOUND.with_context(f"Study {request.study_instance_uid} not found in PACS")

    pacs_study = studies[0]

    study_date = parse_dicom_date(pacs_study.study_date)

    # Create study in local DB (triggers entity flow automatically via StudyService)
    await service.create_study(
        {
            "study_uid": request.study_instance_uid,
            "date": study_date,
            "patient_id": request.patient_id,
            "study_description": pacs_study.study_description,
            "modalities_in_study": pacs_study.modalities_in_study,
        }
    )

    # Fetch series from PACS
    pacs_series = await client.find_series(
        query=SeriesQuery(study_instance_uid=request.study_instance_uid),
        peer=pacs,
    )

    # Optionally filter series at import time
    if settings.series_filter_on_import:
        series_filter = SeriesFilter()
        filter_result = series_filter.filter(
            pacs_series,
            to_criteria=SeriesFilterCriteria.from_series_result,
        )
        for fi in filter_result.excluded:
            logger.debug(
                f"Import filter: skipping series {fi.item.series_instance_uid} ({fi.reason})"
            )
        pacs_series = filter_result.included

    # Create each series in local DB (triggers entity flow automatically via StudyService)
    for idx, s in enumerate(pacs_series):
        await service.create_series(
            {
                "series_uid": s.series_instance_uid,
                "series_description": s.series_description,
                "series_number": s.series_number or (idx + 1),
                "modality": s.modality,
                "instance_count": s.number_of_series_related_instances,
                "study_uid": request.study_instance_uid,
            }
        )

    # Return full study with relations
    logger.info(f"Imported study {request.study_instance_uid} with {len(pacs_series)} series")
    return await service.get_study(request.study_instance_uid)


@router.post(
    "/studies/{study_uid}/anonymize",
    response_model=AnonymizationResult,
    responses={202: {"model": BackgroundAnonymizationStatus}},
)
async def anonymize_study(
    study_uid: str,
    _user: SuperUserDep,
    service: AnonymizationServiceDep,
    request: AnonymizeStudyRequest | None = None,
    background: bool = Query(False, description="Run anonymization in the background"),
) -> AnonymizationResult | JSONResponse:
    """Anonymize a study: fetch from PACS, anonymize tags, distribute.

    Args:
        study_uid: Study Instance UID to anonymize
        _user: Authenticated superuser
        service: Anonymization service
        request: Optional overrides for save_to_disk/send_to_pacs
        background: If true, run in background and return immediately

    Returns:
        Anonymization result or status dict if running in background
    """
    save_to_disk = request.save_to_disk if request else None
    send_to_pacs = request.send_to_pacs if request else None

    if background:
        result = await _dispatch_background_anonymization(study_uid, save_to_disk, send_to_pacs)
        return JSONResponse(status_code=202, content=result.model_dump())

    return await service.anonymize_study(
        study_uid,
        save_to_disk=save_to_disk,
        send_to_pacs=send_to_pacs,
    )


async def _dispatch_background_anonymization(
    study_uid: str,
    save_to_disk: bool | None,
    send_to_pacs: bool | None,
) -> BackgroundAnonymizationStatus:
    """Dispatch anonymization to pipeline or in-process background task.

    Args:
        study_uid: Study Instance UID to anonymize.
        save_to_disk: Override for save-to-disk setting.
        send_to_pacs: Override for send-to-PACS setting.

    Returns:
        Background anonymization status.
    """
    if settings.pipeline_enabled:
        from clarinet.services.dicom.tasks import get_anonymize_study_task

        task = get_anonymize_study_task()
        await task.kiq(study_uid, save_to_disk=save_to_disk, send_to_pacs=send_to_pacs)
    else:
        from clarinet.services.dicom.tasks import anonymize_study_background

        bg_task = asyncio.create_task(
            anonymize_study_background(study_uid, save_to_disk, send_to_pacs)
        )
        _background_tasks.add(bg_task)
        bg_task.add_done_callback(_background_tasks.discard)

    return BackgroundAnonymizationStatus(study_uid=study_uid)
