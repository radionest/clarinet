"""Unit test for the quarto_render pipeline task wrapper (kind pass-through)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from clarinet.client import ClarinetClient
from clarinet.models.quarto_report import QuartoReportKind
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.tasks import quarto_render as task_mod
from clarinet.settings import settings


@pytest.mark.asyncio
async def test_task_passes_book_kind_and_subdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    render_dir = tmp_path / "renders" / "bk" / "rid"
    render_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))
    monkeypatch.setattr(task_mod, "resolve_quarto_executable", lambda: tmp_path / "quarto")

    captured: dict = {}

    async def fake_render_report(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(task_mod, "render_report", fake_render_report)

    msg = PipelineMessage(
        patient_id="",
        study_uid="",
        payload={
            "report_name": "bk",
            "qmd_path": str(render_dir / "project"),
            "render_dir": str(render_dir),
            "data_reports": [],
            "formats": ["docx"],
            "report_kind": "book",
            "project_subdir": "project",
        },
    )
    ctx = SimpleNamespace(client=AsyncMock(spec=ClarinetClient))

    await task_mod._render_quarto_report_impl(msg, ctx)

    assert captured["kind"] is QuartoReportKind.BOOK
    assert captured["project_subdir"] == "project"


@pytest.mark.asyncio
async def test_task_invalid_report_kind_writes_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An invalid report_kind in the payload lands a FAILED sidecar, not an
    uncaught ValueError that would leave the render stuck on PENDING."""
    from clarinet.services.quarto_render import read_status

    render_dir = tmp_path / "renders" / "bk" / "rid"
    render_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))

    msg = PipelineMessage(
        patient_id="",
        study_uid="",
        payload={
            "report_name": "bk",
            "qmd_path": str(render_dir / "project"),
            "render_dir": str(render_dir),
            "data_reports": [],
            "formats": ["docx"],
            "report_kind": "bogus",
            "project_subdir": "project",
        },
    )
    ctx = SimpleNamespace(client=AsyncMock(spec=ClarinetClient))

    await task_mod._render_quarto_report_impl(msg, ctx)

    state = read_status(render_dir)
    assert state is not None
    assert state["status"] == "failed"
    assert "invalid payload" in state["error"]
