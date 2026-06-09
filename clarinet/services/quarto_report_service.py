"""Service layer for Quarto reports — discovery registry + render orchestration.

Mirrors :mod:`clarinet.services.report_service` (SQL reports). Render state is
not in the DB: it lives in a ``status.json`` sidecar under
``settings.get_quarto_output_path()/<name>/<render_id>/`` written by the
renderer (see :mod:`clarinet.services.quarto_render`).
"""

import asyncio
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from clarinet.exceptions.domain import (
    QuartoNotInstalledError,
    QuartoRenderNotFoundError,
    QuartoRenderNotReadyError,
    QuartoReportNotFoundError,
)
from clarinet.models.quarto_report import (
    QuartoRenderState,
    QuartoRenderStatus,
    QuartoReportFormat,
    QuartoReportTemplate,
)
from clarinet.services.report_service import ReportRegistry
from clarinet.settings import settings
from clarinet.utils.logger import logger
from clarinet.utils.quarto_discovery import DiscoveredQuartoReport

# Fire-and-forget render tasks (pipeline-disabled fallback). Held in a module
# set so they are not garbage-collected mid-flight; the done callback discards.
_background_tasks: set[asyncio.Task[None]] = set()


class QuartoReportRegistry:
    """Immutable in-memory registry of Quarto templates.

    Built once at startup from :func:`discover_quarto_templates` and stored on
    ``app.state.quarto_report_registry``. Restart the API to pick up new files
    — matches how SQL reports / RecordType / hydrators are loaded.
    """

    def __init__(self, items: Sequence[DiscoveredQuartoReport]) -> None:
        self._templates: list[QuartoReportTemplate] = [template for template, _ in items]
        self._by_name: dict[str, DiscoveredQuartoReport] = {t.name: (t, p) for t, p in items}

    def list_templates(self) -> list[QuartoReportTemplate]:
        return list(self._templates)

    def get(self, name: str) -> DiscoveredQuartoReport | None:
        return self._by_name.get(name)


class QuartoReportService:
    """Lists Quarto templates and orchestrates background renders."""

    def __init__(self, registry: QuartoReportRegistry, report_registry: ReportRegistry) -> None:
        self._registry = registry
        self._report_registry = report_registry

    def list_reports(self) -> list[QuartoReportTemplate]:
        return self._registry.list_templates()

    async def request_render(
        self, name: str, formats: list[QuartoReportFormat]
    ) -> QuartoRenderState:
        """Validate inputs, write the pending sidecar, and dispatch the render.

        Raises:
            QuartoReportNotFoundError: ``name`` (or a declared data report) is
                not registered.
            QuartoNotInstalledError: the quarto binary cannot be located.
        """
        from clarinet.services.quarto_render import resolve_quarto_executable, write_status

        entry = self._registry.get(name)
        if entry is None:
            raise QuartoReportNotFoundError(name)
        template, qmd_path = entry

        if resolve_quarto_executable() is None:
            raise QuartoNotInstalledError("quarto binary not found; run 'clarinet quarto install'")

        self._validate_data_reports(template)

        now = datetime.now(UTC)
        # Timestamp + random suffix: two concurrent renders of the same report
        # in the same microsecond must not collide on the directory name.
        render_id = f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        render_dir = self._render_dir(name, render_id)
        write_status(
            render_dir,
            name=name,
            render_id=render_id,
            status=QuartoRenderStatus.PENDING,
            formats=formats,
            created_at=now.isoformat(),
        )

        try:
            await self._dispatch(name, qmd_path, template.data_reports, formats, render_dir)
        except Exception as exc:
            # A broker/enqueue failure must not leave the sidecar stuck on
            # PENDING — record it as failed so the UI stops polling.
            logger.opt(exception=exc).error(f"Quarto render '{name}' dispatch failed")
            write_status(
                render_dir,
                name=name,
                render_id=render_id,
                status=QuartoRenderStatus.FAILED,
                formats=formats,
                error=f"dispatch failed: {exc}",
                created_at=now.isoformat(),
                finished_at=datetime.now(UTC).isoformat(),
            )
        return self.get_render_state(name, render_id)

    def get_render_state(self, name: str, render_id: str) -> QuartoRenderState:
        """Read the status sidecar for a render (404 when unknown)."""
        state = self._state_from_dir(self._render_dir(name, render_id))
        if state is None:
            raise QuartoRenderNotFoundError(
                f"Render '{render_id}' for Quarto report '{name}' not found"
            )
        return state

    def get_output_file(self, name: str, render_id: str, fmt: QuartoReportFormat) -> Path:
        """Resolve the rendered file path (409 if the render is not finished)."""
        render_dir = self._render_dir(name, render_id)
        state = self._state_from_dir(render_dir)
        if state is None:
            raise QuartoRenderNotFoundError(
                f"Render '{render_id}' for Quarto report '{name}' not found"
            )
        output_path = render_dir / f"report.{fmt.extension}"
        if state.status is not QuartoRenderStatus.DONE or not output_path.is_file():
            raise QuartoRenderNotReadyError(
                f"Render '{render_id}' ({fmt.value}) not ready (status: {state.status.value})"
            )
        return output_path

    def _validate_data_reports(self, template: QuartoReportTemplate) -> None:
        """Fail fast (404) when a declared ``clarinet.data`` report is unknown.

        Validating here — before dispatch — surfaces a typo as an immediate 404
        instead of a silent render failure later. The render itself re-resolves
        the SQL text from disk (the worker has no ``app.state`` registry), so
        only the report *names* travel through the queue.
        """
        for report_name in template.data_reports:
            if self._report_registry.get_sql(report_name) is None:
                raise QuartoReportNotFoundError(
                    f"{template.name}: required SQL report '{report_name}' not found"
                )

    async def _dispatch(
        self,
        name: str,
        qmd_path: Path,
        data_reports: list[str],
        formats: list[QuartoReportFormat],
        render_dir: Path,
    ) -> None:
        """Queue the render on the pipeline, or run it in-process as a fallback.

        Mirrors the dual strategy in ``dicom.py``: a TaskIQ kick when
        ``pipeline_enabled``, otherwise a tracked ``asyncio.create_task``.
        """
        payload = {
            "report_name": name,
            "qmd_path": str(qmd_path),
            "render_dir": str(render_dir),
            "data_reports": data_reports,
            "formats": [f.value for f in formats],
        }
        if settings.pipeline_enabled:
            from clarinet.services.pipeline.message import PipelineMessage
            from clarinet.services.pipeline.tasks.quarto_render import render_quarto_report

            # patient_id / study_uid are required str on PipelineMessage but
            # carry no meaning for a report render — pass empty, like
            # build_pipeline_message_from_record does for missing context.
            msg = PipelineMessage(patient_id="", study_uid="", payload=payload)
            await render_quarto_report.kicker().kiq(msg.model_dump())
        else:
            from clarinet.services.quarto_render import render_report, resolve_quarto_executable

            executable = resolve_quarto_executable()
            assert executable is not None  # guaranteed by request_render's check
            task = asyncio.create_task(
                render_report(
                    name=name,
                    qmd_path=qmd_path,
                    data_reports=data_reports,
                    formats=formats,
                    render_dir=render_dir,
                    quarto_executable=executable,
                    timeout_seconds=settings.quarto_render_timeout_seconds,
                )
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    def _render_dir(self, name: str, render_id: str) -> Path:
        """Build the per-render directory, rejecting path-traversal in inputs."""
        base = settings.get_quarto_output_path().resolve()
        target = (base / name / render_id).resolve()
        if not target.is_relative_to(base):
            raise QuartoRenderNotFoundError(f"Invalid render path for '{name}'/'{render_id}'")
        return target

    @staticmethod
    def _state_from_dir(render_dir: Path) -> QuartoRenderState | None:
        from clarinet.services.quarto_render import read_status

        raw = read_status(render_dir)
        if raw is None:
            return None
        return QuartoRenderState.model_validate(raw)
