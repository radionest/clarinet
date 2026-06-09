"""Built-in pipeline task: render a Quarto (``*.qmd``) report to DOCX/PDF.

Triggered by ``POST /api/admin/quarto-reports/{name}/render`` (when
``pipeline_enabled``). All inputs travel in ``msg.payload`` because the worker
has no access to the API's ``app.state`` registries — the router resolves the
``.qmd`` path and the SQL text of each declared data report before dispatch.

The actual work lives in :func:`clarinet.services.quarto_render.render_report`
so the in-process fallback (``pipeline_enabled=False``) shares the same code.
"""

from __future__ import annotations

from pathlib import Path

from clarinet.models.quarto_report import QuartoRenderStatus, QuartoReportFormat
from clarinet.services.pipeline.context import TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.task import pipeline_task
from clarinet.services.quarto_render import render_report, resolve_quarto_executable, write_status
from clarinet.settings import settings
from clarinet.utils.logger import logger


@pipeline_task(queue=settings.default_queue_name)
async def render_quarto_report(msg: PipelineMessage, _ctx: TaskContext) -> None:
    """Render the Quarto report described by ``msg.payload``.

    ``_ctx`` (the standard TaskContext) is unused: this task addresses files by
    explicit paths from the payload, not via record/series working dirs.
    """
    payload = msg.payload
    name: str = payload["report_name"]
    qmd_path = Path(payload["qmd_path"])
    render_dir = Path(payload["render_dir"])
    data_reports: list[str] = payload.get("data_reports", [])
    try:
        formats = [QuartoReportFormat(value) for value in payload.get("formats", ["docx"])]
    except ValueError as exc:
        logger.error(f"Quarto render '{name}': invalid formats in payload: {exc}")
        write_status(
            render_dir,
            name=name,
            render_id=render_dir.name,
            status=QuartoRenderStatus.FAILED,
            formats=[],
            error=f"invalid formats: {exc}",
        )
        return

    # Defense in depth: the write target comes from the queue payload. A
    # legitimate message is produced by QuartoReportService (which validates the
    # path); refuse any render_dir outside the configured output root regardless.
    output_base = settings.get_quarto_output_path().resolve()
    if not render_dir.resolve().is_relative_to(output_base):
        logger.error(f"Quarto render '{name}': render_dir outside output root; refusing")
        return

    executable = resolve_quarto_executable()
    if executable is None:
        logger.error(f"Quarto render '{name}' dispatched but quarto binary is not installed")
        write_status(
            render_dir,
            name=name,
            render_id=render_dir.name,
            status=QuartoRenderStatus.FAILED,
            formats=formats,
            error="quarto binary not found on the worker host",
        )
        return

    await render_report(
        name=name,
        qmd_path=qmd_path,
        data_reports=data_reports,
        formats=formats,
        render_dir=render_dir,
        quarto_executable=executable,
        timeout_seconds=settings.quarto_render_timeout_seconds,
    )
