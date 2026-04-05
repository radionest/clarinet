"""DICOMweb proxy router — QIDO-RS and WADO-RS endpoints.

Translates DICOMweb HTTP requests into DICOM Q/R operations via the
DicomWebProxyService, enabling OHIF Viewer to display images from
a traditional PACS that only supports C-FIND/C-GET.
"""

import asyncio
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from clarinet.api.dependencies import (
    CurrentUserDep,
    DicomClientDep,
    DicomWebCacheDep,
    DicomWebProxyServiceDep,
    PacsNodeDep,
)
from clarinet.utils.dicom import parse_frame_numbers
from clarinet.utils.logger import logger

router = APIRouter()

DICOM_JSON_CONTENT_TYPE = "application/dicom+json"


def _dicomweb_base_url(request: Request) -> str:
    """Build the DICOMweb base URL from the incoming request."""
    return str(request.base_url).rstrip("/") + "/dicom-web"


@router.get("/studies")
async def search_studies(
    request: Request,
    _user: CurrentUserDep,
    service: DicomWebProxyServiceDep,
) -> JSONResponse:
    """QIDO-RS: Search for studies.

    Args:
        request: FastAPI request (query params forwarded to C-FIND)
        _user: Authenticated user
        service: DICOMweb proxy service

    Returns:
        DICOM JSON array of matching studies
    """
    params = dict(request.query_params)
    results = await service.search_studies(params)
    return JSONResponse(content=results, media_type=DICOM_JSON_CONTENT_TYPE)


@router.get("/studies/{study_uid}/metadata")
async def retrieve_study_metadata(
    study_uid: str,
    request: Request,
    _user: CurrentUserDep,
    service: DicomWebProxyServiceDep,
) -> JSONResponse:
    """WADO-RS: Retrieve metadata for all instances in a study.

    Args:
        study_uid: Study Instance UID
        request: FastAPI request (for building base URL)
        _user: Authenticated user
        service: DICOMweb proxy service

    Returns:
        DICOM JSON array of instance metadata with BulkDataURIs
    """
    base_url = _dicomweb_base_url(request)
    metadata = await service.retrieve_study_metadata(study_uid, base_url)
    return JSONResponse(content=metadata, media_type=DICOM_JSON_CONTENT_TYPE)


@router.get("/studies/{study_uid}/series")
async def search_series(
    study_uid: str,
    request: Request,
    _user: CurrentUserDep,
    service: DicomWebProxyServiceDep,
) -> JSONResponse:
    """QIDO-RS: Search for series within a study.

    Args:
        study_uid: Study Instance UID
        request: FastAPI request (query params forwarded to C-FIND)
        _user: Authenticated user
        service: DICOMweb proxy service

    Returns:
        DICOM JSON array of matching series
    """
    params = dict(request.query_params)
    results = await service.search_series(study_uid, params)
    return JSONResponse(content=results, media_type=DICOM_JSON_CONTENT_TYPE)


@router.get("/studies/{study_uid}/series/{series_uid}/instances")
async def search_instances(
    study_uid: str,
    series_uid: str,
    request: Request,
    _user: CurrentUserDep,
    service: DicomWebProxyServiceDep,
) -> JSONResponse:
    """QIDO-RS: Search for instances within a series.

    Args:
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        request: FastAPI request (query params forwarded to C-FIND)
        _user: Authenticated user
        service: DICOMweb proxy service

    Returns:
        DICOM JSON array of matching instances
    """
    params = dict(request.query_params)
    results = await service.search_instances(study_uid, series_uid, params)
    return JSONResponse(content=results, media_type=DICOM_JSON_CONTENT_TYPE)


@router.get("/studies/{study_uid}/series/{series_uid}/metadata")
async def retrieve_series_metadata(
    study_uid: str,
    series_uid: str,
    request: Request,
    _user: CurrentUserDep,
    service: DicomWebProxyServiceDep,
) -> JSONResponse:
    """WADO-RS: Retrieve metadata for all instances in a series.

    Triggers C-GET to cache if not already cached, then returns DICOM JSON
    metadata with BulkDataURIs pointing to the frames endpoint.

    Args:
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        request: FastAPI request (for building base URL)
        _user: Authenticated user
        service: DICOMweb proxy service

    Returns:
        DICOM JSON array of instance metadata with BulkDataURIs
    """
    base_url = _dicomweb_base_url(request)
    metadata = await service.retrieve_series_metadata(study_uid, series_uid, base_url)
    return JSONResponse(content=metadata, media_type=DICOM_JSON_CONTENT_TYPE)


@router.get("/studies/{study_uid}/series/{series_uid}/instances/{instance_uid}/frames/{frames}")
async def retrieve_frames(
    study_uid: str,
    series_uid: str,
    instance_uid: str,
    frames: str,
    _user: CurrentUserDep,
    service: DicomWebProxyServiceDep,
) -> Response:
    """WADO-RS: Retrieve pixel data frames for a specific instance.

    Args:
        study_uid: Study Instance UID
        series_uid: Series Instance UID
        instance_uid: SOP Instance UID
        frames: Comma-separated 1-based frame numbers (e.g. "1" or "1,2,3")
        _user: Authenticated user
        service: DICOMweb proxy service

    Returns:
        Multipart response with raw pixel data
    """
    frame_numbers = parse_frame_numbers(frames)

    body, content_type = await service.retrieve_frames(
        study_uid=study_uid,
        series_uid=series_uid,
        instance_uid=instance_uid,
        frame_numbers=frame_numbers,
    )

    return Response(content=body, media_type=content_type)


@router.get("/studies/{study_uid}/series/{series_uid}/archive")
async def download_series_archive(
    study_uid: str,
    series_uid: str,
    _user: CurrentUserDep,
    cache: DicomWebCacheDep,
    client: DicomClientDep,
    pacs: PacsNodeDep,
) -> StreamingResponse:
    """Download a DICOM series as a ZIP archive.

    Ensures the series is cached in memory (fetching from PACS if needed),
    then builds a ZIP from in-memory datasets.
    """
    cached = await cache.ensure_series_cached(study_uid, series_uid, client, pacs)

    spooled = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)  # noqa: SIM115
    count = await asyncio.to_thread(cache.build_series_zip, cached, spooled)
    spooled.seek(0)

    logger.info(f"Serving ZIP archive for series {series_uid} ({count} instances)")

    return StreamingResponse(
        spooled,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{series_uid}.zip"'},
    )


@router.post("/preload/{study_uid}")
async def preload_study(
    study_uid: str,
    _user: CurrentUserDep,
    service: DicomWebProxyServiceDep,
) -> JSONResponse:
    """Start background preloading of a study into the DICOMweb cache.

    Returns a task_id for polling progress via GET /preload/{study_uid}/progress/{task_id}.
    """
    task_id = await service.start_preload(study_uid)
    return JSONResponse({"task_id": task_id})


@router.get("/preload/{study_uid}/progress/{task_id}")
async def preload_progress(
    study_uid: str,  # noqa: ARG001 — path param required by URL pattern
    task_id: str,
    _user: CurrentUserDep,
    service: DicomWebProxyServiceDep,
) -> JSONResponse:
    """Poll preload progress for a study."""
    progress = service.get_preload_progress(task_id)
    if progress is None:
        return JSONResponse({"status": "not_found"})
    return JSONResponse(progress)
