"""Unit tests for clarinet.services.quarto_render (status sidecar, env, resolve)."""

import sys
from pathlib import Path

import pytest

from clarinet.models.quarto_report import QuartoRenderStatus, QuartoReportFormat
from clarinet.services import quarto_render
from clarinet.services.quarto_render import (
    _build_render_env,
    read_status,
    resolve_quarto_executable,
    write_status,
)
from clarinet.settings import settings


def test_status_sidecar_roundtrip(tmp_path: Path) -> None:
    write_status(
        tmp_path,
        name="rep",
        render_id="20260101_000000_000000",
        status=QuartoRenderStatus.RUNNING,
        formats=[QuartoReportFormat.DOCX, QuartoReportFormat.PDF],
        ready={"docx": True, "pdf": False},
        created_at="2026-01-01T00:00:00+00:00",
    )
    data = read_status(tmp_path)
    assert data is not None
    assert data["name"] == "rep"
    assert data["status"] == "running"
    assert data["ready"] == {"docx": True, "pdf": False}
    assert data["created_at"] == "2026-01-01T00:00:00+00:00"
    # No partial temp file is left behind (atomic write).
    assert not (tmp_path / "status.json.tmp").exists()


def test_read_status_missing_returns_none(tmp_path: Path) -> None:
    assert read_status(tmp_path / "missing") is None


def test_build_render_env_strips_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Secrets that must never reach the Python chunks executed by quarto.
    monkeypatch.setenv("CLARINET_DATABASE_PASSWORD", "topsecret")
    monkeypatch.setenv("CLARINET_SECRET_KEY", "shh")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = _build_render_env(tmp_path, tmp_path / "tmp")

    assert "CLARINET_DATABASE_PASSWORD" not in env
    assert "CLARINET_SECRET_KEY" not in env
    assert "DATABASE_URL" not in env
    # Only the explicitly-allowed keys are present.
    assert env["HOME"] == str(tmp_path)
    assert env["TMPDIR"] == str(tmp_path / "tmp")
    assert env["QUARTO_PYTHON"] == sys.executable
    assert env["PATH"] == "/usr/bin:/bin"


def test_resolve_executable_explicit_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "quarto"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(settings, "quarto_executable", str(fake))
    assert resolve_quarto_executable() == fake


def test_resolve_executable_missing_explicit_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "quarto_executable", str(tmp_path / "nope"))
    assert resolve_quarto_executable() is None


def test_resolve_executable_install_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "quarto_executable", None)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "quarto").write_text("#!/bin/sh\n")
    monkeypatch.setattr(type(settings), "quarto_install_path", property(lambda _self: tmp_path))
    assert resolve_quarto_executable() == bin_dir / "quarto"


@pytest.mark.asyncio
async def test_render_report_marks_failed_when_quarto_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-existent quarto binary must surface as a failed sidecar, not raise."""
    bad = tmp_path / "no-such-quarto"
    await quarto_render.render_report(
        name="rep",
        qmd_path=_write_min_qmd(tmp_path),
        data_sql={},
        formats=[QuartoReportFormat.DOCX],
        render_dir=tmp_path / "out",
        quarto_executable=bad,
        timeout_seconds=10.0,
    )
    state = read_status(tmp_path / "out")
    assert state is not None
    assert state["status"] == "failed"
    assert state["error"]


def _write_min_qmd(tmp_path: Path) -> Path:
    qmd = tmp_path / "rep.qmd"
    qmd.write_text("---\ntitle: T\n---\n\nHello.\n")
    return qmd
