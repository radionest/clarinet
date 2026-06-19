"""Unit tests for clarinet.services.quarto_render (status sidecar, env, resolve)."""

import os
import site
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from clarinet.client import ClarinetAPIError, ClarinetClient
from clarinet.exceptions.domain import QuartoRenderError
from clarinet.models.quarto_report import QuartoRenderStatus, QuartoReportFormat
from clarinet.services import quarto_render
from clarinet.services.quarto_render import (
    build_render_env,
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
    monkeypatch.delenv("PYTHONPATH", raising=False)

    env = build_render_env(tmp_path, tmp_path / "tmp")

    assert "CLARINET_DATABASE_PASSWORD" not in env
    assert "CLARINET_SECRET_KEY" not in env
    assert "DATABASE_URL" not in env
    # Only the explicitly-allowed keys are present. PYTHONPATH is always set
    # (to render_dir) so a chunk can import the staged report_schemas module.
    assert set(env) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "LANG",
        "LC_ALL",
        "QUARTO_PYTHON",
        "PYTHONUSERBASE",
        "PYTHONPATH",
    }
    assert env["HOME"] == str(tmp_path)
    assert env["TMPDIR"] == str(tmp_path / "tmp")
    assert env["QUARTO_PYTHON"] == sys.executable
    assert env["PATH"] == "/usr/bin:/bin"
    # Compare with the call, not a literal — Windows CI resolves %APPDATA%\Python.
    assert env["PYTHONUSERBASE"] == site.getuserbase()
    # No inherited PYTHONPATH (delenv above) → render_dir alone.
    assert env["PYTHONPATH"] == str(tmp_path)


def test_build_render_env_passes_pythonpath_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PYTHONPATH is a package search path, not a secret — the kernel must
    resolve the same packages as the worker process. render_dir leads (for
    report_schemas import); the inherited PYTHONPATH follows."""
    monkeypatch.setenv("PYTHONPATH", "/opt/extra-packages")

    env = build_render_env(tmp_path, tmp_path / "tmp")

    assert env["PYTHONPATH"] == f"{tmp_path}{os.pathsep}/opt/extra-packages"


_skip_windows = pytest.mark.skipif(
    sys.platform == "win32", reason="fake executables are POSIX sh scripts"
)


def _write_fake_executable(path: Path, stderr_line: str) -> Path:
    """A stand-in binary that prints ``stderr_line`` to stderr and exits 1."""
    path.write_text(f'#!/bin/sh\necho "{stderr_line}" >&2\nexit 1\n')
    path.chmod(0o755)
    return path


@_skip_windows
@pytest.mark.asyncio
async def test_run_quarto_enriches_kernel_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A kernel-shaped quarto failure gets the import-probe hint appended.

    ``QUARTO_PYTHON`` must point at a fake failing interpreter: in the dev venv
    the real imports succeed, so the "lacks dependencies" branch would never run.
    """
    fake_quarto = _write_fake_executable(
        tmp_path / "quarto", "ModuleNotFoundError: No module named 'yaml'"
    )
    fake_python = _write_fake_executable(
        tmp_path / "python", "ModuleNotFoundError: No module named 'nbclient'"
    )
    monkeypatch.setattr(
        quarto_render,
        "build_render_env",
        lambda render_dir, tmp_dir: {"QUARTO_PYTHON": str(fake_python)},
    )
    render_dir = tmp_path / "out"
    render_dir.mkdir()

    with pytest.raises(QuartoRenderError) as exc_info:
        await quarto_render._run_quarto(
            _write_min_qmd(render_dir), QuartoReportFormat.DOCX, render_dir, fake_quarto, 30.0
        )

    message = str(exc_info.value)
    assert "No module named 'yaml'" in message
    assert "No module named 'nbclient'" in message
    assert "pip install --upgrade clarinet" in message


@_skip_windows
@pytest.mark.asyncio
async def test_run_quarto_no_enrichment_without_markers(tmp_path: Path) -> None:
    """Non-kernel failures (e.g. LaTeX) must not trigger the import probe."""
    fake_quarto = _write_fake_executable(tmp_path / "quarto", "LaTeX Error: File not found")
    render_dir = tmp_path / "out"
    render_dir.mkdir()

    with pytest.raises(QuartoRenderError) as exc_info:
        await quarto_render._run_quarto(
            _write_min_qmd(render_dir), QuartoReportFormat.DOCX, render_dir, fake_quarto, 30.0
        )

    message = str(exc_info.value)
    assert "LaTeX Error" in message
    assert "Kernel diagnostics" not in message


@_skip_windows
@pytest.mark.asyncio
async def test_run_quarto_reports_imports_ok_when_probe_passes(tmp_path: Path) -> None:
    """A kernel-shaped failure with a healthy interpreter (the dev venv — real
    ``build_render_env``) points the reader away from the kernel."""
    fake_quarto = _write_fake_executable(tmp_path / "quarto", "Jupyter is not available")
    render_dir = tmp_path / "out"
    render_dir.mkdir()

    with pytest.raises(QuartoRenderError) as exc_info:
        await quarto_render._run_quarto(
            _write_min_qmd(render_dir), QuartoReportFormat.DOCX, render_dir, fake_quarto, 30.0
        )

    message = str(exc_info.value)
    assert "Jupyter is not available" in message
    assert "imports OK" in message
    assert "the failure is elsewhere" in message


@pytest.mark.asyncio
async def test_kernel_diagnostics_swallows_probe_failure(tmp_path: Path) -> None:
    """A probe that cannot even start must not mask the render error."""
    result = await quarto_render._kernel_diagnostics(
        {"QUARTO_PYTHON": str(tmp_path / "no-such-python")}
    )
    assert result == ""


def test_resolve_executable_explicit_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "quarto"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
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
    (bin_dir / "quarto").chmod(0o755)
    monkeypatch.setattr(type(settings), "quarto_install_path", property(lambda _self: tmp_path))
    assert resolve_quarto_executable() == bin_dir / "quarto"


@pytest.mark.asyncio
async def test_render_report_marks_failed_when_quarto_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-existent quarto binary must surface as a failed sidecar, not raise."""
    bad = tmp_path / "no-such-quarto"
    render_dir = tmp_path / "out"
    render_dir.mkdir()
    await quarto_render.render_report(
        name="rep",
        qmd_path=_write_min_qmd(render_dir),
        data_reports=[],
        formats=[QuartoReportFormat.DOCX],
        render_dir=render_dir,
        quarto_executable=bad,
        timeout_seconds=10.0,
        client=AsyncMock(spec=ClarinetClient),
    )
    state = read_status(render_dir)
    assert state is not None
    assert state["status"] == "failed"
    assert state["error"]


@pytest.mark.asyncio
async def test_render_report_marks_failed_when_qmd_not_in_render_dir(tmp_path: Path) -> None:
    """A qmd_path whose file is absent from render_dir fails fast (FAILED sidecar)."""
    await quarto_render.render_report(
        name="rep",
        qmd_path=_write_min_qmd(tmp_path),  # outside render_dir
        data_reports=[],
        formats=[QuartoReportFormat.DOCX],
        render_dir=tmp_path / "out",
        quarto_executable=tmp_path / "quarto",
        timeout_seconds=10.0,
        client=AsyncMock(spec=ClarinetClient),
    )
    state = read_status(tmp_path / "out")
    assert state is not None
    assert state["status"] == "failed"
    assert "not found in render dir" in state["error"]


@pytest.mark.asyncio
async def test_materialize_data_writes_csv_from_api(tmp_path: Path) -> None:
    """Data CSVs come from the reports API via the client — no DB, no *.sql files."""
    client = AsyncMock(spec=ClarinetClient)
    client.download_report.return_value = b"\xef\xbb\xbfa,b\r\n1,2\r\n"

    await quarto_render._materialize_data(["rep1", "rep2"], tmp_path, client)

    assert (tmp_path / "data" / "rep1.csv").read_bytes() == b"\xef\xbb\xbfa,b\r\n1,2\r\n"
    assert (tmp_path / "data" / "rep2.csv").read_bytes() == b"\xef\xbb\xbfa,b\r\n1,2\r\n"
    # Per-request timeout must cover the server-side SQL budget (httpx default
    # is seconds; report SQL may legally run for minutes).
    _, kwargs = client.download_report.call_args
    assert kwargs["request_timeout"] == settings.reports_query_timeout_seconds + 30


@pytest.mark.asyncio
async def test_render_report_marks_failed_when_data_fetch_fails(tmp_path: Path) -> None:
    """An API error while materializing data lands in the sidecar, not raised."""
    render_dir = tmp_path / "out"
    render_dir.mkdir()
    client = AsyncMock(spec=ClarinetClient)
    client.download_report.side_effect = ClarinetAPIError("report not found", status_code=404)

    await quarto_render.render_report(
        name="rep",
        qmd_path=_write_min_qmd(render_dir),
        data_reports=["missing"],
        formats=[QuartoReportFormat.DOCX],
        render_dir=render_dir,
        quarto_executable=tmp_path / "quarto",
        timeout_seconds=10.0,
        client=client,
    )
    state = read_status(render_dir)
    assert state is not None
    assert state["status"] == "failed"
    assert "report not found" in state["error"]


@pytest.mark.asyncio
async def test_request_render_copies_qmd_into_render_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The service stages the .qmd inside render_dir so the worker host never
    needs the project's reports folder."""
    from clarinet.models.quarto_report import QuartoReportTemplate
    from clarinet.services.quarto_report_service import (
        QuartoReportRegistry,
        QuartoReportService,
    )
    from clarinet.services.report_service import ReportRegistry

    qmd = _write_min_qmd(tmp_path)
    template = QuartoReportTemplate(name="rep", title="T", description="", data_reports=[])
    service = QuartoReportService(QuartoReportRegistry([(template, qmd)]), ReportRegistry([]))
    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))
    monkeypatch.setattr(quarto_render, "resolve_quarto_executable", lambda: tmp_path / "quarto")
    dispatch = AsyncMock()
    monkeypatch.setattr(service, "_dispatch", dispatch)

    state = await service.request_render("rep", [QuartoReportFormat.DOCX])

    assert state.status is QuartoRenderStatus.PENDING
    _, dispatched_qmd, _, _, render_dir = dispatch.call_args.args
    assert dispatched_qmd == render_dir / qmd.name
    assert dispatched_qmd.is_file()


@pytest.mark.asyncio
async def test_request_render_stages_schema_module_into_render_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A sibling report_schemas.py is staged next to the .qmd so a chunk can
    import it; absence is fine (the copy-qmd test above ships no schema)."""
    from clarinet.models.quarto_report import QuartoReportTemplate
    from clarinet.services.quarto_report_service import (
        QuartoReportRegistry,
        QuartoReportService,
    )
    from clarinet.services.report_service import ReportRegistry

    qmd = _write_min_qmd(tmp_path)
    (tmp_path / "report_schemas.py").write_text("# generated\n", encoding="utf-8")
    template = QuartoReportTemplate(name="rep", title="T", description="", data_reports=[])
    service = QuartoReportService(QuartoReportRegistry([(template, qmd)]), ReportRegistry([]))
    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))
    monkeypatch.setattr(quarto_render, "resolve_quarto_executable", lambda: tmp_path / "quarto")
    dispatch = AsyncMock()
    monkeypatch.setattr(service, "_dispatch", dispatch)

    await service.request_render("rep", [QuartoReportFormat.DOCX])

    _, _, _, _, render_dir = dispatch.call_args.args
    assert (render_dir / "report_schemas.py").read_text() == "# generated\n"


@pytest.mark.asyncio
async def test_request_render_fails_fast_on_empty_service_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Declared data reports + empty internal service token → immediate FAILED
    sidecar with an actionable message, not a cryptic 401 minutes later."""
    from clarinet.models.quarto_report import QuartoReportTemplate
    from clarinet.models.report import ReportTemplate
    from clarinet.services.quarto_report_service import (
        QuartoReportRegistry,
        QuartoReportService,
    )
    from clarinet.services.report_service import ReportRegistry

    qmd = _write_min_qmd(tmp_path)
    template = QuartoReportTemplate(name="rep", title="T", description="", data_reports=["stats"])
    report_registry = ReportRegistry(
        [(ReportTemplate(name="stats", title="S", description=""), "SELECT 1")]
    )
    service = QuartoReportService(QuartoReportRegistry([(template, qmd)]), report_registry)
    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))
    monkeypatch.setattr(quarto_render, "resolve_quarto_executable", lambda: tmp_path / "quarto")
    monkeypatch.setattr(type(settings), "effective_service_token", property(lambda _self: ""))

    state = await service.request_render("rep", [QuartoReportFormat.DOCX])

    assert state.status is QuartoRenderStatus.FAILED
    assert "service token" in (state.error or "")


@pytest.mark.asyncio
async def test_request_render_unknown_data_report_message_not_double_wrapped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing ``clarinet.data`` report surfaces its own message, not a
    re-wrapped "Quarto report '<sentence>' not found"."""
    from clarinet.exceptions.domain import QuartoReportNotFoundError
    from clarinet.models.quarto_report import QuartoReportTemplate
    from clarinet.services.quarto_report_service import (
        QuartoReportRegistry,
        QuartoReportService,
    )
    from clarinet.services.report_service import ReportRegistry

    qmd = _write_min_qmd(tmp_path)
    template = QuartoReportTemplate(name="rep", title="T", description="", data_reports=["missing"])
    service = QuartoReportService(QuartoReportRegistry([(template, qmd)]), ReportRegistry([]))
    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))
    monkeypatch.setattr(quarto_render, "resolve_quarto_executable", lambda: tmp_path / "quarto")

    with pytest.raises(QuartoReportNotFoundError) as exc_info:
        await service.request_render("rep", [QuartoReportFormat.DOCX])

    assert str(exc_info.value) == "rep: required SQL report 'missing' not found"


@pytest.mark.asyncio
async def test_render_dir_rejects_path_traversal() -> None:
    """name / render_id that escape the output root must raise, not resolve."""
    from clarinet.exceptions.domain import QuartoRenderNotFoundError
    from clarinet.services.quarto_report_service import (
        QuartoReportRegistry,
        QuartoReportService,
    )
    from clarinet.services.report_service import ReportRegistry

    service = QuartoReportService(QuartoReportRegistry([]), ReportRegistry([]))
    with pytest.raises(QuartoRenderNotFoundError):
        await service.get_render_state("../../etc", "passwd")
    with pytest.raises(QuartoRenderNotFoundError):
        await service.get_output_file("..", "..", QuartoReportFormat.DOCX)


def _write_sidecar(
    renders_root: Path, status: QuartoRenderStatus, *, age_seconds: float = 0.0
) -> Path:
    """Write a ``rep``/``rid`` sidecar, optionally backdating its mtime."""
    render_dir = renders_root / "rep" / "rid"
    write_status(
        render_dir,
        name="rep",
        render_id="rid",
        status=status,
        formats=[QuartoReportFormat.DOCX],
        created_at="2026-01-01T00:00:00+00:00",
    )
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(render_dir / "status.json", (old, old))
    return render_dir


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [QuartoRenderStatus.PENDING, QuartoRenderStatus.RUNNING])
async def test_get_render_state_reports_stale_render_as_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: QuartoRenderStatus
) -> None:
    """A sidecar silent for longer than render timeout + grace reads back as
    failed (worker crash guard) — but the file itself is not rewritten."""
    from clarinet.services.quarto_report_service import (
        _STALE_GRACE_SECONDS,
        QuartoReportRegistry,
        QuartoReportService,
    )
    from clarinet.services.report_service import ReportRegistry

    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))
    render_dir = _write_sidecar(
        tmp_path / "renders",
        status,
        age_seconds=settings.quarto_render_timeout_seconds + _STALE_GRACE_SECONDS + 60,
    )
    service = QuartoReportService(QuartoReportRegistry([]), ReportRegistry([]))

    state = await service.get_render_state("rep", "rid")

    assert state.status is QuartoRenderStatus.FAILED
    assert "presumed crashed" in (state.error or "")
    # Read-only override: a live-but-slow worker can still flip the file to done.
    raw = read_status(render_dir)
    assert raw is not None
    assert raw["status"] == status.value


@pytest.mark.asyncio
async def test_get_render_state_keeps_fresh_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from clarinet.services.quarto_report_service import (
        QuartoReportRegistry,
        QuartoReportService,
    )
    from clarinet.services.report_service import ReportRegistry

    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))
    _write_sidecar(tmp_path / "renders", QuartoRenderStatus.RUNNING)
    service = QuartoReportService(QuartoReportRegistry([]), ReportRegistry([]))

    state = await service.get_render_state("rep", "rid")

    assert state.status is QuartoRenderStatus.RUNNING
    assert state.error is None


@pytest.mark.asyncio
async def test_get_render_state_ignores_staleness_for_terminal_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Old done/failed sidecars are history, not crashes — returned as-is."""
    from clarinet.services.quarto_report_service import (
        _STALE_GRACE_SECONDS,
        QuartoReportRegistry,
        QuartoReportService,
    )
    from clarinet.services.report_service import ReportRegistry

    monkeypatch.setattr(settings, "quarto_output_path", str(tmp_path / "renders"))
    _write_sidecar(
        tmp_path / "renders",
        QuartoRenderStatus.DONE,
        age_seconds=settings.quarto_render_timeout_seconds + _STALE_GRACE_SECONDS + 60,
    )
    service = QuartoReportService(QuartoReportRegistry([]), ReportRegistry([]))

    state = await service.get_render_state("rep", "rid")

    assert state.status is QuartoRenderStatus.DONE
    assert state.error is None


def _write_min_qmd(tmp_path: Path) -> Path:
    qmd = tmp_path / "rep.qmd"
    qmd.write_text("---\ntitle: T\n---\n\nHello.\n")
    return qmd
