"""Viewer plugin endpoints — generate URIs for external DICOM viewers."""

from fastapi import APIRouter

from clarinet.api.dependencies import AuthorizedRecordDep, ViewerRegistryDep
from clarinet.exceptions.http import NOT_FOUND

router = APIRouter()


@router.get("/{record_id}/viewers")
async def list_viewer_urls(
    record: AuthorizedRecordDep,
    registry: ViewerRegistryDep,
) -> dict[str, str]:
    """Get URIs for all enabled viewers for a record.

    Returns an empty dict for records without a study (e.g. PATIENT-level).
    """
    if record.study is None:
        return {}
    return registry.build_all_uris(
        patient_id=record.patient_id,
        study_uid=record.study.anon_uid or record.study.study_uid,
        series_uid=record.series_uid,
    )


@router.get("/{record_id}/viewers/{viewer_name}")
async def get_viewer_url(
    record: AuthorizedRecordDep,
    registry: ViewerRegistryDep,
    viewer_name: str,
) -> dict[str, str]:
    """Get URI for a specific viewer for a record.

    Returns an empty URI for records without a study (e.g. PATIENT-level).
    """
    adapter = registry.get(viewer_name)
    if adapter is None:
        raise NOT_FOUND.with_context(f"Viewer '{viewer_name}' is not configured")
    if record.study is None:
        return {"viewer": viewer_name, "uri": ""}
    uri = adapter.build_uri(
        patient_id=record.patient_id,
        study_uid=record.study.anon_uid or record.study.study_uid,
        series_uid=record.series_uid,
    )
    return {"viewer": viewer_name, "uri": uri}
