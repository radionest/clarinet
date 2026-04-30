"""Custom SQL reports — admin-only endpoints for listing and downloading."""

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from clarinet.api.dependencies import ReportServiceDep, SuperUserDep
from clarinet.models.report import ReportFormat, ReportTemplate

router = APIRouter(
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Forbidden"},
        404: {"description": "Report not found"},
        500: {"description": "Report execution failed"},
    },
)


# Anything outside ``[A-Za-z0-9_.-]`` is replaced before going into the
# Content-Disposition header so a hostile or accidental filename (newline,
# double quote) cannot break the response framing.
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_filename(name: str, extension: str) -> str:
    today = datetime.now(UTC).strftime("%Y%m%d")
    safe_name = _UNSAFE_FILENAME_RE.sub("_", name) or "report"
    return f"{safe_name}_{today}.{extension}"


@router.get("", response_model=list[ReportTemplate])
async def list_reports(
    _current_user: SuperUserDep,
    service: ReportServiceDep,
) -> list[ReportTemplate]:
    """List available SQL report templates.

    Returns an empty list when the project has no ``review/`` folder or it
    contains no ``*.sql`` files.
    """
    return service.list_reports()


@router.get("/{name}/download")
async def download_report(
    name: str,
    _current_user: SuperUserDep,
    service: ReportServiceDep,
    report_format: ReportFormat = Query(default=ReportFormat.CSV, alias="format"),
) -> StreamingResponse:
    """Execute the report and stream the result as CSV or XLSX.

    The filename embeds today's UTC date so repeated downloads do not collide
    in the user's Downloads folder.
    """
    buffer, media_type = await service.generate_report(name, report_format)
    filename = _safe_filename(name, report_format.extension)
    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
