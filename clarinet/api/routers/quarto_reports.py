"""Quarto reports — admin-only endpoints: list, render (background), poll, download."""

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from clarinet.api.dependencies import AdminUserDep, QuartoReportServiceDep
from clarinet.api.routers.reports import _safe_filename
from clarinet.models.quarto_report import (
    QuartoRenderRequest,
    QuartoRenderState,
    QuartoReportFormat,
    QuartoReportTemplate,
)

router = APIRouter(
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Forbidden"},
        404: {"description": "Quarto report or render not found"},
        409: {"description": "Render not ready"},
        503: {"description": "Quarto CLI not installed"},
    },
)


@router.get("", response_model=list[QuartoReportTemplate])
async def list_quarto_reports(
    _current_user: AdminUserDep,
    service: QuartoReportServiceDep,
) -> list[QuartoReportTemplate]:
    """List available Quarto report templates.

    Returns an empty list when the project has no Quarto reports folder or it
    contains no ``*.qmd`` files.
    """
    return service.list_reports()


@router.post("/{name}/render", response_model=QuartoRenderState, status_code=202)
async def render_quarto_report(
    name: str,
    body: QuartoRenderRequest,
    _current_user: AdminUserDep,
    service: QuartoReportServiceDep,
) -> QuartoRenderState:
    """Start a background render and return the initial (pending) state.

    The returned ``render_id`` is the key for polling status and downloading
    the result once finished.
    """
    return await service.request_render(name, body.formats)


@router.get("/{name}/renders/{render_id}/status", response_model=QuartoRenderState)
async def get_quarto_render_status(
    name: str,
    render_id: str,
    _current_user: AdminUserDep,
    service: QuartoReportServiceDep,
) -> QuartoRenderState:
    """Poll the status sidecar of a render (404 when the render is unknown)."""
    return service.get_render_state(name, render_id)


@router.get("/{name}/renders/{render_id}/download")
async def download_quarto_render(
    name: str,
    render_id: str,
    _current_user: AdminUserDep,
    service: QuartoReportServiceDep,
    report_format: QuartoReportFormat = Query(default=QuartoReportFormat.DOCX, alias="format"),
) -> FileResponse:
    """Download a rendered file; 409 while the render is still pending/running."""
    output_path = service.get_output_file(name, render_id, report_format)
    filename = _safe_filename(name, report_format.extension)
    return FileResponse(
        path=output_path,
        media_type=report_format.media_type,
        filename=filename,
    )
