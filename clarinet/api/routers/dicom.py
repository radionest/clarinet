"""DICOM API router for PACS query and import operations."""

import asyncio

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from clarinet.api.dependencies import (
    AnonymizationServiceDep,
    DicomClientDep,
    PacsNodeDep,
    RecordRepositoryDep,
    StudyRepositoryDep,
    StudyServiceDep,
    SuperUserDep,
)
from clarinet.exceptions import NOT_FOUND
from clarinet.models import Record
from clarinet.models.study import StudyRead
from clarinet.repositories.record_repository import RecordRepository, RecordSearchCriteria
from clarinet.services.dicom.models import (
    AnonymizationResult,
    AnonymizeStudyRequest,
    BackgroundAnonymizationStatus,
    PacsImportRequest,
    PacsStudyWithSeries,
    SeriesQuery,
    StudyQuery,
)
from clarinet.services.dicom.orchestrator import create_anonymization_orchestrator
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

    series_tasks = [
        client.find_series(
            query=SeriesQuery(study_instance_uid=s.study_instance_uid),
            peer=pacs,
        )
        for s in studies
    ]
    all_series = await asyncio.gather(*series_tasks)

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
    studies = await client.find_studies(
        query=StudyQuery(
            study_instance_uid=request.study_instance_uid,
            patient_id=request.patient_id,
        ),
        peer=pacs,
    )

    if not studies:
        raise NOT_FOUND.with_context(f"Study {request.study_instance_uid} not found in PACS")

    pacs_study = studies[0]

    study_date = parse_dicom_date(pacs_study.study_date)

    await service.create_study(
        {
            "study_uid": request.study_instance_uid,
            "date": study_date,
            "patient_id": request.patient_id,
            "study_description": pacs_study.study_description,
            "modalities_in_study": pacs_study.modalities_in_study,
        }
    )

    pacs_series = await client.find_series(
        query=SeriesQuery(study_instance_uid=request.study_instance_uid),
        peer=pacs,
    )

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

    logger.info(f"Imported study {request.study_instance_uid} with {len(pacs_series)} series")
    return await service.get_study(request.study_instance_uid)


@router.post(
    "/studies/{study_uid}/anonymize",
    response_model=AnonymizationResult,
    responses={
        202: {"model": BackgroundAnonymizationStatus},
        404: {"description": "Study or anonymize-study Record not found"},
    },
)
async def anonymize_study(
    study_uid: str,
    _user: SuperUserDep,
    service: AnonymizationServiceDep,
    record_repo: RecordRepositoryDep,
    request: AnonymizeStudyRequest | None = None,
    background: bool = Query(False, description="Run anonymization in the background"),
) -> AnonymizationResult | JSONResponse:
    """Anonymize a study: fetch from PACS, anonymize tags, distribute.

    When a tracking Record (``settings.anon_record_type_name``) exists for the
    study, dispatches via ``AnonymizationOrchestrator`` — applies skip-guard,
    anonymizes the Patient, and writes the result to the Record. Without a
    Record, sync mode runs raw anonymization (backwards-compatible) and
    background mode returns 404.
    """
    save_to_disk = request.save_to_disk if request else None
    send_to_pacs = request.send_to_pacs if request else None

    record = await _find_anonymize_record(record_repo, study_uid)

    if background:
        if record is None:
            raise NOT_FOUND.with_context(
                f"No '{settings.anon_record_type_name}' record exists for study {study_uid}"
            )
        result = await _dispatch_background_anonymization(
            study_uid, record, save_to_disk, send_to_pacs
        )
        return JSONResponse(status_code=202, content=result.model_dump())

    if record is None:
        # Raw mode: no Record tracking, no Patient anonymization, no skip-guard.
        return await service.anonymize_study(
            study_uid,
            save_to_disk=save_to_disk,
            send_to_pacs=send_to_pacs,
        )

    async with create_anonymization_orchestrator() as orch:
        return await orch.run(
            study_uid,
            record_id=record.id,
            save_to_disk=save_to_disk,
            send_to_pacs=send_to_pacs,
        )


async def _find_anonymize_record(
    repo: RecordRepository,
    study_uid: str,
) -> Record | None:
    criteria = RecordSearchCriteria(
        study_uid=study_uid,
        record_type_name=settings.anon_record_type_name,
    )
    records = await repo.find_by_criteria(criteria, limit=1)
    return records[0] if records else None


async def _dispatch_background_anonymization(
    study_uid: str,
    record: Record,
    save_to_disk: bool | None,
    send_to_pacs: bool | None,
) -> BackgroundAnonymizationStatus:
    """Dispatch anonymization to pipeline (TaskIQ) or in-process background task."""
    payload: dict[str, bool] = {}
    if save_to_disk is not None:
        payload["save_to_disk"] = save_to_disk
    if send_to_pacs is not None:
        payload["send_to_pacs"] = send_to_pacs

    record_id = record.id
    assert record_id is not None, "Persisted Record always has an id"

    if settings.pipeline_enabled:
        from clarinet.services.dicom.pipeline import anonymize_study_pipeline
        from clarinet.services.pipeline import PipelineMessage

        msg = PipelineMessage(
            patient_id=record.patient_id,
            study_uid=study_uid,
            record_id=record_id,
            payload=payload,
        )
        await anonymize_study_pipeline.kicker().kiq(msg.model_dump())
    else:
        bg_task = asyncio.create_task(
            _run_orchestrator_in_process(study_uid, record_id, save_to_disk, send_to_pacs)
        )
        _background_tasks.add(bg_task)
        bg_task.add_done_callback(_background_tasks.discard)

    return BackgroundAnonymizationStatus(study_uid=study_uid)


async def _run_orchestrator_in_process(
    study_uid: str,
    record_id: int,
    save_to_disk: bool | None,
    send_to_pacs: bool | None,
) -> None:
    """In-process fallback when ``pipeline_enabled=False``.

    Catches all exceptions so the background task is never disrupted.
    The Orchestrator already records errors to the Record before re-raising.
    """
    try:
        async with create_anonymization_orchestrator() as orch:
            await orch.run(
                study_uid,
                record_id=record_id,
                save_to_disk=save_to_disk,
                send_to_pacs=send_to_pacs,
            )
        logger.info(f"Background anonymization completed for study {study_uid}")
    except Exception:
        logger.exception(f"Background anonymization failed for study {study_uid}")
