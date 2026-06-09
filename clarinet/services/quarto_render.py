"""Core Quarto rendering: materialize SQL data, run the ``quarto`` CLI, and
record progress in a ``status.json`` sidecar.

This module is deliberately free of web/app state so it runs identically in the
pipeline worker process and in the in-process fallback (``pipeline_enabled``
False). Callers pass plain paths plus the already-resolved SQL text for each
data report — the worker never touches ``app.state``.

Security: the quarto subprocess runs with an environment built from scratch
(:func:`_build_render_env`), so secrets (``CLARINET_*``, ``DATABASE_URL``,
service token, AMQP credentials) never reach the Python code chunks Quarto
executes. Data reaches chunks only as pre-rendered CSV files.
"""

import asyncio
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clarinet.exceptions.domain import QuartoRenderError
from clarinet.models.quarto_report import QuartoRenderStatus, QuartoReportFormat
from clarinet.repositories.report_repository import ReportRepository
from clarinet.settings import settings
from clarinet.utils.logger import logger
from clarinet.utils.report_formatters import to_csv

_STATUS_FILE = "status.json"


def resolve_quarto_executable() -> Path | None:
    """Locate the quarto binary: explicit setting → install dir → PATH.

    Returns ``None`` when no binary is found so callers can raise a typed
    :class:`~clarinet.exceptions.domain.QuartoNotInstalledError` (503).
    """
    if settings.quarto_executable:
        explicit = Path(settings.quarto_executable)
        return explicit if explicit.exists() else None
    installed = settings.quarto_install_path / "bin" / "quarto"
    if installed.exists():
        return installed
    found = shutil.which("quarto")
    return Path(found) if found else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_status(
    render_dir: Path,
    *,
    name: str,
    render_id: str,
    status: QuartoRenderStatus,
    formats: list[QuartoReportFormat],
    ready: dict[str, bool] | None = None,
    error: str | None = None,
    created_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    """Atomically write the status sidecar the API polls (write-temp + replace)."""
    render_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "name": name,
        "render_id": render_id,
        "status": status.value,
        "formats": [f.value for f in formats],
        "ready": ready if ready is not None else {f.value: False for f in formats},
        "error": error,
        "created_at": created_at or _now_iso(),
        "finished_at": finished_at,
    }
    tmp = render_dir / f"{_STATUS_FILE}.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(render_dir / _STATUS_FILE)


def read_status(render_dir: Path) -> dict[str, Any] | None:
    """Read and parse the status sidecar; ``None`` when absent or unreadable."""
    status_path = render_dir / _STATUS_FILE
    if not status_path.is_file():
        return None
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"Failed to read Quarto status sidecar {status_path}: {exc}")
        return None
    return data if isinstance(data, dict) else None


async def render_report(
    *,
    name: str,
    qmd_path: Path,
    data_sql: dict[str, str],
    formats: list[QuartoReportFormat],
    render_dir: Path,
    quarto_executable: Path,
    timeout_seconds: float,
) -> None:
    """Render ``qmd_path`` to ``formats`` inside ``render_dir``.

    Materializes each ``data_sql`` query as ``data/<name>.csv`` (reusing the
    SQL-report read-only execution + CSV formatter), copies the ``.qmd`` into
    the render dir, then runs ``quarto render`` once per format. Progress and
    failures are recorded in the status sidecar; this coroutine never raises to
    its caller so a fire-and-forget dispatch cannot crash the worker loop.
    """
    created_at = _now_iso()
    existing = read_status(render_dir)
    if existing and existing.get("created_at"):
        created_at = str(existing["created_at"])

    write_status(
        render_dir,
        name=name,
        render_id=render_dir.name,
        status=QuartoRenderStatus.RUNNING,
        formats=formats,
        created_at=created_at,
    )

    ready: dict[str, bool] = {f.value: False for f in formats}
    try:
        await _materialize_data(data_sql, render_dir)
        work_qmd = render_dir / qmd_path.name
        shutil.copy2(qmd_path, work_qmd)
        for fmt in formats:
            await _run_quarto(work_qmd, fmt, render_dir, quarto_executable, timeout_seconds)
            ready[fmt.value] = True
            write_status(
                render_dir,
                name=name,
                render_id=render_dir.name,
                status=QuartoRenderStatus.RUNNING,
                formats=formats,
                ready=ready,
                created_at=created_at,
            )
    except Exception as exc:
        logger.opt(exception=exc).error(f"Quarto render '{name}' failed")
        write_status(
            render_dir,
            name=name,
            render_id=render_dir.name,
            status=QuartoRenderStatus.FAILED,
            formats=formats,
            ready=ready,
            error=f"{type(exc).__name__}: {exc}",
            created_at=created_at,
            finished_at=_now_iso(),
        )
        return

    write_status(
        render_dir,
        name=name,
        render_id=render_dir.name,
        status=QuartoRenderStatus.DONE,
        formats=formats,
        ready=ready,
        created_at=created_at,
        finished_at=_now_iso(),
    )
    logger.info(f"Quarto render '{name}' ({render_dir.name}) finished: {list(ready)}")


async def _materialize_data(data_sql: dict[str, str], render_dir: Path) -> None:
    """Execute each SQL report and write ``data/<name>.csv`` for the chunks.

    Reuses :class:`ReportRepository` so the same read-only transaction,
    ``SELECT``/``WITH`` validation and statement timeout apply. DB credentials
    are never exposed to the rendered document — only these CSV files are.
    """
    if not data_sql:
        return
    data_dir = render_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    repo = ReportRepository()
    for report_name, sql in data_sql.items():
        columns, rows = await repo.execute_report(sql)
        csv_buffer = to_csv(columns, rows)
        (data_dir / f"{report_name}.csv").write_bytes(csv_buffer.getvalue())


async def _run_quarto(
    qmd_path: Path,
    fmt: QuartoReportFormat,
    render_dir: Path,
    quarto_executable: Path,
    timeout_seconds: float,
) -> None:
    """Run ``quarto render`` for a single format; raise on non-zero / timeout."""
    output_name = f"report.{fmt.extension}"
    tmp_dir = render_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env = _build_render_env(render_dir, tmp_dir)
    logger.info(f"Rendering {qmd_path.name} → {output_name} via quarto ({fmt.value})")

    proc = await asyncio.create_subprocess_exec(
        str(quarto_executable),
        "render",
        qmd_path.name,
        "--to",
        fmt.value,
        "--output",
        output_name,
        cwd=str(render_dir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise QuartoRenderError(
            f"quarto render timed out after {timeout_seconds:.0f}s for format {fmt.value}"
        ) from exc

    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        raise QuartoRenderError(
            f"quarto render failed (exit {proc.returncode}) for {fmt.value}: {detail[:2000]}"
        )
    if not (render_dir / output_name).is_file():
        raise QuartoRenderError(f"quarto produced no {output_name} for format {fmt.value}")


def _build_render_env(render_dir: Path, tmp_dir: Path) -> dict[str, str]:
    """Minimal environment for the quarto subprocess.

    Built from scratch (not a copy of ``os.environ``) so DB URL, service token,
    AMQP credentials and all ``CLARINET_*`` settings never reach the executed
    Python chunks. ``QUARTO_PYTHON`` points at the current interpreter so the
    report's Jupyter kernel uses this venv (pandas, etc.); ``HOME`` / ``XDG_*``
    are redirected into the render dir so Jupyter writes nothing to the real
    user home.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(render_dir),
        "TMPDIR": str(tmp_dir),
        "XDG_CACHE_HOME": str(render_dir / ".cache"),
        "XDG_DATA_HOME": str(render_dir / ".local" / "share"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "QUARTO_PYTHON": sys.executable,
    }
