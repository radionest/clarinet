"""DICOMweb proxy router — QIDO-RS and WADO-RS endpoints.

Translates DICOMweb HTTP requests into DICOM Q/R operations via the
DicomWebProxyService, enabling OHIF Viewer to display images from
a traditional PACS that only supports C-FIND/C-GET.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from src.api.dependencies import CurrentUserDep, DicomWebProxyServiceDep

router = APIRouter()

DICOM_JSON_CONTENT_TYPE = "application/dicom+json"


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
    base_url = str(request.base_url).rstrip("/") + "/dicom-web"
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
    base_url = str(request.base_url).rstrip("/") + "/dicom-web"
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
    try:
        frame_numbers = [int(f.strip()) for f in frames.split(",") if f.strip()]
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid frame numbers: {frames}"},
        )
    if not frame_numbers:
        return JSONResponse(
            status_code=400,
            content={"detail": "No frame numbers specified"},
        )

    body, content_type = await service.retrieve_frames(
        study_uid=study_uid,
        series_uid=series_uid,
        instance_uid=instance_uid,
        frame_numbers=frame_numbers,
    )

    return Response(content=body, media_type=content_type)
