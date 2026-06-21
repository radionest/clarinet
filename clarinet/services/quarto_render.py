"""Core Quarto rendering: materialize SQL data, run the ``quarto`` CLI, and
record progress in a ``status.json`` sidecar.

This module is deliberately free of web/app state so it runs identically in the
pipeline worker process and in the in-process fallback (``pipeline_enabled``
False). Callers pass plain paths plus an authenticated :class:`ClarinetClient`;
data reports are fetched as CSV from the reports API, so the renderer host
needs neither DB credentials nor the project's ``*.sql`` files. The ``.qmd``
itself is copied into ``render_dir`` by the dispatching service, so the
renderer host does not need the project's reports folder either.

Security: the quarto subprocess runs with an environment built from scratch
(:func:`build_render_env`), so secrets (``CLARINET_*``, ``DATABASE_URL``,
service token, AMQP credentials) never reach the Python code chunks Quarto
executes. Data reaches chunks only as pre-rendered CSV files.
"""

import asyncio
import json
import os
import re
import shutil
import site
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clarinet.client import ClarinetClient
from clarinet.exceptions.domain import QuartoRenderError
from clarinet.models.quarto_report import QuartoRenderStatus, QuartoReportFormat, QuartoReportKind
from clarinet.services.events.bus import get_event_bus
from clarinet.services.events.models import TaskProgressEvent
from clarinet.settings import settings
from clarinet.utils.logger import logger
from clarinet.utils.quarto_discovery import parse_book_metadata

_STATUS_FILE = "status.json"

# Render failures that smell like a broken/incomplete kernel interpreter — the
# production class of errors `_kernel_diagnostics` can explain.
_KERNEL_ERROR_MARKERS = re.compile(r"ModuleNotFoundError|Jupyter is not available")


def _is_executable(path: Path) -> bool:
    """True when ``path`` is a regular file with the execute bit set."""
    return path.is_file() and os.access(path, os.X_OK)


def resolve_quarto_executable() -> Path | None:
    """Locate the quarto binary: explicit setting → install dir → PATH.

    Verifies the candidate is an executable file (not just present) so a stale
    directory or a non-executable file does not slip through and fail later at
    subprocess launch. Returns ``None`` when no binary is found so callers can
    raise a typed :class:`~clarinet.exceptions.domain.QuartoNotInstalledError`.
    """
    if settings.quarto_executable:
        explicit = Path(settings.quarto_executable)
        return explicit if _is_executable(explicit) else None
    installed = settings.quarto_install_path / "bin" / "quarto"
    if _is_executable(installed):
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

    # Opportunistic SSE push (admins only; user_id=None). No-op in the TaskIQ
    # worker process where no bus is registered — there the poller still drives
    # updates. write_status always runs via asyncio.to_thread, so use the
    # thread-safe publish.
    bus = get_event_bus()
    if bus is not None:
        bus.publish_threadsafe(
            TaskProgressEvent(task="quarto_render", task_id=render_id, payload=payload)
        )


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


def status_mtime(render_dir: Path) -> float | None:
    """Last modification time of the status sidecar; ``None`` when absent.

    The renderer rewrites the sidecar at every attempt start and after each
    completed format, so the mtime tracks liveness across multi-format renders
    and pipeline retries — unlike ``created_at``, which is preserved.
    """
    try:
        return (render_dir / _STATUS_FILE).stat().st_mtime
    except OSError:
        return None


async def render_report(
    *,
    name: str,
    qmd_path: Path,
    data_reports: list[str],
    formats: list[QuartoReportFormat],
    render_dir: Path,
    quarto_executable: Path,
    timeout_seconds: float,
    client: ClarinetClient,
    kind: QuartoReportKind = QuartoReportKind.FILE,
    project_subdir: str | None = None,
) -> None:
    """Render the report to ``formats`` inside ``render_dir``.

    Materializes each declared data report as ``data/<name>.csv`` by fetching
    it from the reports API via ``client``, then runs ``quarto render`` once
    per format.  Progress and failures are recorded in the status sidecar;
    this coroutine never raises to its caller so a fire-and-forget dispatch
    cannot crash the worker loop.

    When ``kind`` is FILE the ``.qmd`` must already be inside ``render_dir``
    (the dispatching service copies it there); only its file name is used.
    When ``kind`` is BOOK, ``render_dir/<project_subdir>/`` holds the staged
    book project; ``quarto render`` is run over that directory and the lone
    output-dir artifact is normalized to ``render_dir/report.<ext>``.
    """
    created_at = _now_iso()
    existing = await asyncio.to_thread(read_status, render_dir)
    if existing and existing.get("created_at"):
        created_at = str(existing["created_at"])

    await asyncio.to_thread(
        write_status,
        render_dir,
        name=name,
        render_id=render_dir.name,
        status=QuartoRenderStatus.RUNNING,
        formats=formats,
        created_at=created_at,
    )

    ready: dict[str, bool] = {f.value: False for f in formats}
    try:
        if kind is QuartoReportKind.BOOK:
            if project_subdir is None:
                raise QuartoRenderError(f"book '{name}': missing project_subdir")
            work_dir = render_dir / project_subdir
            quarto_yml = work_dir / "_quarto.yml"
            if not await asyncio.to_thread(quarto_yml.is_file):
                raise QuartoRenderError(
                    f"book '{name}': _quarto.yml not found in staged project dir"
                )
            await _materialize_data(data_reports, work_dir, client)
            yml_text = await asyncio.to_thread(quarto_yml.read_text, "utf-8")
            _title, _desc, _data, output_dir = parse_book_metadata(yml_text, name)

            async def render_one(fmt: QuartoReportFormat) -> None:
                await _run_quarto_book(
                    work_dir, fmt, output_dir, render_dir, quarto_executable, timeout_seconds
                )
        else:
            # Only the file name from the payload is trusted: the .qmd must already
            # sit inside render_dir (copied there by QuartoReportService), so a
            # hostile qmd_path cannot make the renderer read an arbitrary file.
            work_qmd = render_dir / qmd_path.name
            if not await asyncio.to_thread(work_qmd.is_file):
                raise QuartoRenderError(f"template '{qmd_path.name}' not found in render dir")
            await _materialize_data(data_reports, render_dir, client)

            async def render_one(fmt: QuartoReportFormat) -> None:
                await _run_quarto(work_qmd, fmt, render_dir, quarto_executable, timeout_seconds)

        # Shared across both kinds: render each format, then flip its sidecar bit.
        for fmt in formats:
            await render_one(fmt)
            ready[fmt.value] = True
            await asyncio.to_thread(
                write_status,
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
        await asyncio.to_thread(
            write_status,
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

    await asyncio.to_thread(
        write_status,
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


async def _materialize_data(
    data_reports: list[str], render_dir: Path, client: ClarinetClient
) -> None:
    """Fetch each declared SQL report as CSV from the API and write ``data/<name>.csv``.

    The SQL executes server-side (``GET /admin/reports/{name}/download``) with
    the same read-only transaction, ``SELECT``/``WITH`` validation and
    statement timeout as a manual download — the renderer host needs neither
    DB credentials nor the ``*.sql`` files. The per-request timeout mirrors
    the server's SQL budget so a slow (but legal) report is not cut off by the
    httpx default.
    """
    if not data_reports:
        return
    data_dir = render_dir / "data"
    await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
    timeout = settings.reports_query_timeout_seconds + 30
    for report_name in data_reports:
        csv_bytes = await client.download_report(report_name, request_timeout=timeout)
        await asyncio.to_thread((data_dir / f"{report_name}.csv").write_bytes, csv_bytes)


async def _invoke_quarto(
    extra_args: list[str],
    fmt: QuartoReportFormat,
    work_dir: Path,
    render_dir: Path,
    quarto_executable: Path,
    timeout_seconds: float,
) -> None:
    """Run ``quarto render <extra_args>`` in ``work_dir``; raise on timeout/non-zero.

    Shared by the single-file (:func:`_run_quarto`) and book (:func:`_run_quarto_book`)
    paths. The environment is built from scratch (``build_render_env``) so secrets never
    reach executed chunks; output collection is left to the caller.
    """
    tmp_dir = render_dir / "tmp"
    await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)
    env = build_render_env(render_dir, tmp_dir)

    proc = await asyncio.create_subprocess_exec(
        str(quarto_executable),
        "render",
        *extra_args,
        cwd=str(work_dir),
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
        message = f"quarto render failed (exit {proc.returncode}) for {fmt.value}: {detail[:2000]}"
        if _KERNEL_ERROR_MARKERS.search(detail):
            message += await _kernel_diagnostics(env)
        raise QuartoRenderError(message)


async def _run_quarto(
    qmd_path: Path,
    fmt: QuartoReportFormat,
    render_dir: Path,
    quarto_executable: Path,
    timeout_seconds: float,
) -> None:
    """Run ``quarto render`` for a single ``.qmd`` format; raise on non-zero / missing output."""
    output_name = f"report.{fmt.extension}"
    logger.info(f"Rendering {qmd_path.name} → {output_name} via quarto ({fmt.value})")
    await _invoke_quarto(
        [qmd_path.name, "--to", fmt.value, "--output", output_name],
        fmt,
        render_dir,
        render_dir,
        quarto_executable,
        timeout_seconds,
    )
    if not await asyncio.to_thread((render_dir / output_name).is_file):
        raise QuartoRenderError(f"quarto produced no {output_name} for format {fmt.value}")


async def _run_quarto_book(
    work_dir: Path,
    fmt: QuartoReportFormat,
    output_dir: str,
    render_dir: Path,
    quarto_executable: Path,
    timeout_seconds: float,
) -> None:
    """Render a Quarto *book* project to a single ``fmt`` file, normalized to
    ``render_dir/report.<ext>``.

    A book project ignores ``--output`` and writes into its ``output-dir`` under a
    title-derived name, so we render the whole project (``cwd=work_dir``) and then
    collect the lone artifact of the requested format from ``work_dir/<output_dir>``.
    """
    logger.info(f"Rendering book {work_dir.name} → {fmt.value} via quarto")
    await _invoke_quarto(
        [".", "--to", fmt.value], fmt, work_dir, render_dir, quarto_executable, timeout_seconds
    )
    await asyncio.to_thread(_collect_book_artifact, work_dir / output_dir, fmt, render_dir)


def _collect_book_artifact(output_path: Path, fmt: QuartoReportFormat, render_dir: Path) -> None:
    """Copy the single rendered ``*.<ext>`` from a book's output-dir to ``render_dir/report.<ext>``.

    Raises :class:`QuartoRenderError` when zero or more than one candidate exists — a
    book that emitted nothing (silent failure) or several files (ambiguous) must not be
    served as a successful render.
    """
    candidates = sorted(output_path.glob(f"*.{fmt.extension}")) if output_path.is_dir() else []
    if not candidates:
        raise QuartoRenderError(
            f"book produced no {fmt.extension} in {output_path.name}/ for format {fmt.value}"
        )
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise QuartoRenderError(
            f"book produced {len(candidates)} {fmt.extension} files in {output_path.name}/ "
            f"({names}); expected exactly one"
        )
    shutil.copy2(candidates[0], render_dir / f"report.{fmt.extension}")


async def _kernel_diagnostics(env: dict[str, str]) -> str:
    """Probe the kernel interpreter for the imports quarto's jupyter engine needs.

    Runs only after a render failed with a kernel-shaped error, with the same
    environment the render used, so the probe sees exactly what the kernel saw.
    Returns an actionable hint to append to the error message, or ``""`` when
    the probe itself fails — a broken diagnostic must not mask the render error.
    """
    interpreter = env.get("QUARTO_PYTHON", sys.executable)
    try:
        proc = await asyncio.create_subprocess_exec(
            interpreter,
            "-c",
            "import yaml, nbformat, nbclient, jupyter_client, ipykernel, pandas",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ""
    except OSError:
        return ""
    if proc.returncode == 0:
        return f"\nKernel diagnostics: imports OK in {interpreter}; the failure is elsewhere."
    err_lines = stderr.decode(errors="replace").strip().splitlines()
    reason = err_lines[-1] if err_lines else "import failed"
    return (
        f"\nKernel diagnostics: {interpreter} lacks report kernel dependencies ({reason})."
        " The render kernel uses the worker's interpreter — reinstall clarinet into it"
        " (`pip install --upgrade clarinet`)."
    )


def build_render_env(render_dir: Path, tmp_dir: Path) -> dict[str, str]:
    """Minimal environment for the quarto subprocess.

    Built from scratch (not a copy of ``os.environ``) so DB URL, service token,
    AMQP credentials and all ``CLARINET_*`` settings never reach the executed
    Python chunks. ``QUARTO_PYTHON`` points at the current interpreter so the
    report's Jupyter kernel uses this venv (pandas, etc.); ``HOME`` / ``XDG_*``
    are redirected into the render dir so Jupyter writes nothing to the real
    user home.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(render_dir),
        "TMPDIR": str(tmp_dir),
        "XDG_CACHE_HOME": str(render_dir / ".cache"),
        "XDG_DATA_HOME": str(render_dir / ".local" / "share"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "QUARTO_PYTHON": sys.executable,
        # HOME is redirected into render_dir (write isolation; ProtectHome=true
        # on the shipped systemd units leaves no real home), which as a side
        # effect would hide user-site (~/.local) — where pip --user deployments
        # keep clarinet itself and its deps. Restore package visibility
        # explicitly: the kernel must resolve the same packages as the worker.
        "PYTHONUSERBASE": site.getuserbase(),
    }
    # Same motivation: kernel package visibility == worker process visibility.
    # render_dir leads so a chunk can `import report_schemas` (the generated
    # pandera module the dispatcher stages here); the inherited PYTHONPATH
    # follows. Both are search paths, not secrets.
    inherited = os.environ.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{render_dir}{os.pathsep}{inherited}" if inherited else str(render_dir)
    return env
