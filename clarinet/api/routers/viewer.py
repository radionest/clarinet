"""Viewer plugin endpoints — generate URIs for external DICOM viewers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from clarinet.api.dependencies import AuthorizedRecordDep, ViewerRegistryDep
from clarinet.exceptions.http import NOT_FOUND

if TYPE_CHECKING:
    from clarinet.models.record import Record

router = APIRouter()


def _resolve_series_uid(record: Record) -> str | None:
    """Return series_uid respecting viewer_mode of the record type."""
    if record.record_type and record.record_type.viewer_mode == "all_series":
        return None
    return record.series_uid


@router.get("/{record_id}/viewers", responses={404: {"description": "Record not found"}})
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
        series_uid=_resolve_series_uid(record),
    )


@router.get(
    "/{record_id}/viewers/{viewer_name}",
    responses={404: {"description": "Record or viewer not found"}},
)
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
        series_uid=_resolve_series_uid(record),
    )
    return {"viewer": viewer_name, "uri": uri}
