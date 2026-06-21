#!/usr/bin/env python3
"""Clarinet CLI - Simple management utility for Clarinet framework.

Following KISS and YAGNI principles - minimal, practical implementation.
"""

import argparse
import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from clarinet.settings import settings
from clarinet.utils.logger import logger
from clarinet.utils.quarto_scaffold import scaffold_quarto_report


def init_project(path: str, template: str | None = None) -> None:
    """Initialize a new Clarinet project in the specified directory.

    Args:
        path: Destination directory for the new project.
        template: Optional template name to bootstrap from.
    """
    project_path = Path(path).resolve()

    if template is not None:
        from clarinet.cli.templates import copy_template

        copy_template(template, str(project_path))
        logger.info(f"Project initialized from template '{template}' at {project_path}")
        return

    # Create directory structure
    directories = [
        project_path / "tasks",
        project_path / "static",
        project_path / "data",
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created directory: {directory}")

    # Create settings.toml
    settings_content = """\
# Clarinet Configuration File
# Docs: all options can also be set via CLARINET_ env vars

# ── Project ──────────────────────────────────────────
project_name = "My Project"
# project_description = "Medical Imaging Framework"
# project_title = "My Project"  # browser tab <title>; defaults to project_name

# ── Server ───────────────────────────────────────────
port = 8000
host = "127.0.0.1"
debug = true
# root_url = ""

# ── Database ─────────────────────────────────────────
database_driver = "sqlite"
database_name = "clarinet"
# database_host = "localhost"
# database_port = 5432
# database_username = "postgres"
# database_password = "postgres"

# ── Storage ──────────────────────────────────────────
storage_path = "./data"
# anon_id_prefix = "CLARINET"
# anon_names_list = ""         # path to names list for anonymization

# ── Security (change in production!) ─────────────────
secret_key = "change-this-secret-key-in-production"

# ── Roles ────────────────────────────────────────────
# extra_roles = []             # e.g. ["doctor_CT", "surgeon"]

# ── Admin ────────────────────────────────────────────
# admin_username = "admin"
# admin_email = "admin@clarinet.ru"
# admin_password = ""          # required in production
# admin_auto_create = true
# admin_require_strong_password = false

# ── Session ──────────────────────────────────────────
# cookie_name = "clarinet_session"
# session_expire_hours = 24
# session_sliding_refresh = true
# session_absolute_timeout_days = 30
# session_idle_timeout_minutes = 60
# session_concurrent_limit = 5
# session_ip_check = false
# session_secure_cookie = true
# session_cache_ttl_seconds = 30

# ── Session cleanup ──────────────────────────────────
# session_cleanup_enabled = true
# session_cleanup_interval = 3600
# session_cleanup_batch_size = 1000
# session_cleanup_retention_days = 30

# ── Frontend ─────────────────────────────────────────
# frontend_enabled = true

# ── Config mode ──────────────────────────────────────
# config_mode = "toml"                          # "toml" or "python"
# config_tasks_path = "./tasks/"
# config_delete_orphans = false
# config_record_types_file = "record_types.py"  # python mode only
# config_files_catalog_file = "files_catalog.py"
# config_context_hydrators_file = "context_hydrators.py"
# config_schema_hydrators_file = "hydrators.py"

# ── RecordFlow ───────────────────────────────────────
# recordflow_enabled = false
# recordflow_paths = []        # e.g. ["./tasks/workflows"]

# ── Pipeline (RabbitMQ) ──────────────────────────────
# pipeline_enabled = false
# pipeline_default_timeout = 3600
# pipeline_retry_count = 3
# pipeline_retry_delay = 5
# pipeline_retry_max_delay = 120
# pipeline_worker_prefetch = 10
# pipeline_result_backend_url = "" # Redis URL, optional

# ── RabbitMQ connection ──────────────────────────────
# rabbitmq_host = "localhost"
# rabbitmq_port = 5672
# rabbitmq_login = "guest"
# rabbitmq_password = "guest"
# rabbitmq_exchange = "clarinet"
# rabbitmq_management_port = 15672
# rabbitmq_max_consumers = 0

# ── Worker capabilities ──────────────────────────────
# have_gpu = false
# have_dicom = false
# have_quarto = false
# have_keras = false
# have_torch = false

# ── DICOM local node ────────────────────────────────
# dicom_aet = "CLARINET"
# dicom_port = 11112
# dicom_ip = ""
# dicom_max_pdu = 16384
# dicom_max_concurrent_associations = 8
# dicom_log_identifiers = false

# ── PACS remote (backend DICOM service) ──────────────
# pacs_host = "localhost"
# pacs_port = 4242
# pacs_aet = "ORTHANC"

# ── 3D Slicer ────────────────────────────────────────
# slicer_script_paths = []
# slicer_port = 2016
# slicer_timeout = 10.0

# ── DICOMweb proxy ───────────────────────────────────
# dicomweb_enabled = true
# dicomweb_cache_ttl_hours = 24
# dicomweb_cache_max_size_gb = 10.0
# dicomweb_memory_cache_ttl_minutes = 30
# dicomweb_memory_cache_max_entries = 200
# dicomweb_cache_cleanup_enabled = true
# dicomweb_cache_cleanup_interval = 86400
# dicomweb_disk_write_concurrency = 4

# ── OHIF Viewer ──────────────────────────────────────
# ohif_enabled = true
# ohif_default_version = "3.12.0"

# ── Anonymization ────────────────────────────────────
# anon_uid_salt = "clarinet-anon-salt-change-in-production"
# anon_save_to_disk = true
# anon_send_to_pacs = false
# anon_failure_threshold = 0.5

# ── Series filter ────────────────────────────────────
# series_filter_excluded_modalities = ["SR","KO","PR","DOC","RTDOSE","RTPLAN","RTSTRUCT","REG","FID","RWV"]
# series_filter_min_instance_count = 0
# series_filter_unknown_modality_policy = "include"
# series_filter_on_import = false

# ── Logging ──────────────────────────────────────────
# log_level = "INFO"
# log_to_file = true
# log_dir = ""                 # defaults to {storage_path}/logs
# log_rotation = "20 MB"
# log_retention = "1 week"
# log_serialize = true
# log_console_level = ""       # defaults to log_level
# log_noisy_libraries = ["pynetdicom", "aiormq", "aio_pika", "pamqp"]
# log_silenced_libraries = ["pydicom"]
"""

    settings_file = project_path / "settings.toml"
    if not settings_file.exists():
        settings_file.write_text(settings_content)
        logger.info(f"Created settings file: {settings_file}")

    # Create example task design
    example_task = {
        "name": "Example Task",
        "description": "An example task design",
        "fields": [{"name": "field1", "type": "text", "label": "Example Field", "required": True}],
    }

    task_file = project_path / "tasks" / "example.json"
    if not task_file.exists():
        task_file.write_text(json.dumps(example_task, indent=2))
        logger.info(f"Created example task: {task_file}")

    # Create .env example
    env_example = """# Environment variables (optional)
# CLARINET_DATABASE_URL=postgresql://user:pass@localhost/dbname
# CLARINET_JWT_SECRET_KEY=your-secret-key
"""

    env_file = project_path / ".env.example"
    env_file.write_text(env_example)
    logger.info(f"Created .env.example: {env_file}")

    logger.info(f"Project initialized at {project_path}")


def run_server(host: str | None = None, port: int | None = None, headless: bool = False) -> None:
    """Run the Clarinet development server."""
    host = host or settings.host or "127.0.0.1"
    port = port or settings.port or 8000

    if headless:
        # Run only backend
        import uvicorn

        logger.info(f"Starting Clarinet server (headless) at http://{host}:{port}")

        uvicorn.run(
            "clarinet.api.app:app",
            host=host,
            port=port,
            reload=getattr(settings, "debug", True),
            log_level="info" if getattr(settings, "debug", True) else "warning",
        )
    else:
        # Run both backend and frontend
        logger.info(f"Starting Clarinet server with frontend at http://{host}:{port}")
        asyncio.run(run_with_frontend(host, port))


async def _run_gleam_command(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> tuple[int, str, str]:
    """Execute Gleam command asynchronously (DRY principle).

    Returns:
        Tuple of (returncode, stdout, stderr)
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    returncode = process.returncode if process.returncode is not None else -1

    if check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, args, stdout, stderr)

    return returncode, stdout.decode(), stderr.decode()


async def _check_command_exists(command: str) -> bool:
    """Check if a command exists in PATH asynchronously."""
    try:
        process = await asyncio.create_subprocess_exec(
            "which",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()
        return process.returncode == 0
    except Exception:
        return False


async def _install_gleam() -> None:
    """Install Gleam via the official installation script."""
    logger.info("Installing Gleam...")
    try:
        install_process = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            "curl -fsSL https://gleam.run/install.sh | sh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await install_process.communicate()
        stderr = stderr_bytes.decode() if stderr_bytes else ""
        if install_process.returncode != 0:
            logger.error(f"Failed to install Gleam: {stderr}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to install Gleam: {e}")
        sys.exit(1)


async def _ensure_frontend_built(frontend_path: Path) -> None:
    """Build frontend if not already built, installing Gleam if needed.

    When running from a wheel install, pre-built static files already exist
    in ``clarinet/static/`` — Gleam is NOT required.  The build-from-source
    path is only taken when working from a git checkout.
    """
    # 1. Pre-built frontend in package (wheel / make frontend-build)
    static_path = Path(__file__).parent.parent / "static"
    if static_path.exists():
        logger.debug("Using pre-built frontend from package")
        return

    # 2. Already compiled from source (dev workflow)
    build_file = frontend_path / "build" / "dev" / "javascript" / "clarinet.mjs"
    if build_file.exists():
        return

    # 3. Source available — compile with Gleam
    if not frontend_path.exists():
        logger.error(
            "Frontend not available. Either:\n"
            "  1. Install from wheel (includes pre-built frontend)\n"
            "  2. Build from source: make frontend-build"
        )
        sys.exit(1)

    logger.info("Frontend not built. Building now...")
    if not await _check_command_exists("gleam"):
        await _install_gleam()

    try:
        logger.info("Building frontend...")
        await _run_gleam_command(["gleam", "build", "--target", "javascript"], cwd=frontend_path)
        logger.info("Frontend built successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to build frontend: {e}")
        sys.exit(1)


async def _log_subprocess_output(stream: asyncio.StreamReader, prefix: str) -> None:
    """Log lines from a subprocess stream."""
    async for line in stream:
        decoded = line.decode().strip()
        if decoded:
            logger.info(f"{prefix}: {decoded}")


async def _run_frontend_with_entr(frontend_path: Path) -> None:
    """Watch frontend with entr for auto-rebuild."""
    watch_cmd = [
        "sh",
        "-c",
        "find src -name '*.gleam' | entr -c gleam build --target javascript",
    ]
    process = await asyncio.create_subprocess_exec(
        *watch_cmd,
        cwd=frontend_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if process.stdout and process.stderr:
        await asyncio.gather(
            _log_subprocess_output(process.stdout, "Frontend"),
            _log_subprocess_output(process.stderr, "Frontend"),
            process.wait(),
        )


async def _run_frontend_periodic(frontend_path: Path) -> None:
    """Fallback: periodically rebuild frontend."""
    logger.info("'entr' not found. Using periodic rebuild (every 2 seconds)...")
    while True:
        try:
            result = await asyncio.create_subprocess_exec(
                "gleam",
                "build",
                "--target",
                "javascript",
                cwd=frontend_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await result.communicate()

            if result.returncode != 0:
                logger.error(f"Frontend build failed: {stderr.decode()}")
            else:
                output = stdout.decode().strip() if stdout else ""
                if output and "Compiling" in output:
                    logger.info("Frontend rebuilt")
        except Exception as e:
            logger.error(f"Frontend watch error: {e}")

        await asyncio.sleep(2)


async def run_with_frontend(host: str, port: int) -> None:
    """Run both backend and frontend servers concurrently."""
    os.environ["CLARINET_FRONTEND_ENABLED"] = "true"

    import clarinet

    library_path = Path(clarinet.__file__).parent
    frontend_path = library_path / "frontend"

    await _ensure_frontend_built(frontend_path)

    import uvicorn

    config = uvicorn.Config(
        "clarinet.api.app:app",
        host=host,
        port=port,
        reload=getattr(settings, "debug", True),
        log_level="info" if getattr(settings, "debug", True) else "warning",
    )
    server = uvicorn.Server(config)
    frontend_task = None

    async def run_backend() -> None:
        logger.info(f"Starting backend server at http://{host}:{port}")
        await server.serve()

    # Signal handler for graceful shutdown
    loop = asyncio.get_running_loop()

    def signal_handler() -> None:
        logger.info("Shutting down servers...")
        server.should_exit = True
        if frontend_task:
            frontend_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    logger.info(f"Starting Clarinet with frontend at http://{host}:{port}")
    logger.info("Press Ctrl+C to stop both servers")

    # Skip frontend watch when running from wheel install.
    # Wheel has clarinet/static/ but NOT clarinet/frontend/gleam.toml (excluded from build).
    # Dev checkout has gleam.toml — so its presence means "watch mode available".
    static_path = library_path / "static"
    has_gleam_source = (frontend_path / "gleam.toml").exists()
    use_prebuilt = static_path.exists() and not has_gleam_source

    if not use_prebuilt:
        watch_fn = (
            _run_frontend_with_entr
            if await _check_command_exists("entr")
            else _run_frontend_periodic
        )

    try:
        backend_task = asyncio.create_task(run_backend())
        if not use_prebuilt:
            frontend_task = asyncio.create_task(watch_fn(frontend_path))
        await backend_task
    except Exception as e:
        logger.error(f"Error running servers: {e}")
        sys.exit(1)
    finally:
        if frontend_task is not None:
            frontend_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await frontend_task
        logger.info("Servers stopped")


def _parse_dicom_scp_arg(value: str) -> tuple[str, int]:
    """Parse ``--dicom AET:PORT`` argument.

    Returns:
        Tuple of (AE title, port number).
    """
    parts = value.rsplit(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        logger.error(f"Invalid --dicom format: '{value}'. Expected AET:PORT (e.g. WORKER:4006)")
        sys.exit(1)
    try:
        port = int(parts[1])
    except ValueError:
        logger.error(f"Invalid port in --dicom: '{parts[1]}'. Must be an integer")
        sys.exit(1)
    if not (1 <= port <= 65535):
        logger.error(f"Invalid port in --dicom: {port}. Must be between 1 and 65535")
        sys.exit(1)
    return parts[0], port


async def _run_pipeline_worker(
    queues: list[str] | None,
    workers: int,
    dicom_scp: tuple[str, int] | None = None,
    log_file: str | None = None,
) -> None:
    """Run the pipeline task worker.

    Args:
        queues: Queue names to listen on (auto-detected if None).
        workers: Number of concurrent worker tasks.
        dicom_scp: Optional (AET, port) to start a Storage SCP for C-MOVE.
        log_file: Optional override for the worker log file path.
    """
    from clarinet.services.pipeline import run_worker

    if dicom_scp:
        aet, port = dicom_scp
        settings.dicom_aet = aet
        settings.dicom_port = port
        settings.dicom_retrieve_mode = "c-move"
        settings.have_dicom = True

    if not settings.pipeline_enabled:
        logger.warning(
            "Pipeline is disabled (pipeline_enabled=False). "
            "Set CLARINET_PIPELINE_ENABLED=true to enable."
        )

    await run_worker(
        queues=queues,
        workers=workers,
        start_scp=dicom_scp is not None,
        log_file=log_file,
    )


async def init_database() -> None:
    """Initialize the database: apply alembic migrations + create admin user."""
    from clarinet.utils.bootstrap import initialize_application_data
    from clarinet.utils.migrations import cli_upgrade

    logger.info("Initializing database...")
    # Apply all alembic migrations instead of SQLModel.metadata.create_all
    cli_upgrade("head")
    await initialize_application_data()
    logger.info("Database initialized successfully")


def install_frontend() -> None:
    """Install Gleam and frontend dependencies."""
    # Run async function in sync context
    asyncio.run(_install_frontend_async())


async def _install_frontend_async() -> None:
    """Async implementation of frontend installation."""
    # Check if Gleam is installed
    try:
        _, stdout, _ = await _run_gleam_command(["gleam", "--version"])
        logger.info(f"Gleam already installed: {stdout.strip()}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.info("Installing Gleam...")
        try:
            # Install Gleam using official installation script
            install_process = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "curl -fsSL https://gleam.run/install.sh | sh",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await install_process.communicate()
            stderr = stderr_bytes.decode() if stderr_bytes else ""
            if install_process.returncode != 0:
                logger.error(f"Failed to install Gleam: {stderr}")
                sys.exit(1)
            logger.info("Gleam installed successfully")
        except Exception as e:
            logger.error(f"Failed to install Gleam: {e}")
            sys.exit(1)

    # Install frontend dependencies
    import clarinet

    library_path = Path(clarinet.__file__).parent
    frontend_path = library_path / "frontend"
    if frontend_path.exists():
        logger.info("Installing frontend dependencies...")
        try:
            await _run_gleam_command(["gleam", "deps", "download"], cwd=frontend_path)
            logger.info("Frontend dependencies installed successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install frontend dependencies: {e}")
            sys.exit(1)
    else:
        logger.error(f"Frontend directory not found: {frontend_path}")
        sys.exit(1)


def build_frontend(watch: bool = False) -> None:
    """Build the frontend application."""
    asyncio.run(_build_frontend_async(watch))


async def _build_frontend_async(watch: bool = False) -> None:
    """Async implementation of frontend build."""
    import clarinet

    library_path = Path(clarinet.__file__).parent
    frontend_path = library_path / "frontend"
    if not frontend_path.exists():
        logger.error(f"Frontend directory not found: {frontend_path}")
        sys.exit(1)

    try:
        if watch:
            logger.info("Starting frontend build in watch mode...")
            await _run_gleam_command(
                ["gleam", "build", "--target", "javascript"], cwd=frontend_path
            )
            # Note: Gleam doesn't have a built-in watch mode, would need external tool
            logger.info(
                "Note: Gleam doesn't have built-in watch mode. Consider using entr or similar."
            )
        else:
            logger.info("Building frontend...")
            await _run_gleam_command(
                ["gleam", "build", "--target", "javascript"], cwd=frontend_path
            )
            logger.info("Frontend built successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to build frontend: {e}")
        sys.exit(1)


def clean_frontend() -> None:
    """Clean frontend build artifacts."""
    import clarinet

    library_path = Path(clarinet.__file__).parent
    frontend_path = library_path / "frontend"
    build_dir = frontend_path / "build"

    if build_dir.exists():
        logger.info(f"Cleaning build directory: {build_dir}")
        shutil.rmtree(build_dir)
        logger.info("Frontend build artifacts cleaned")
    else:
        logger.info("No build artifacts to clean")


def _patch_ohif_paths(ohif_dir: Path, base_path: str = "") -> None:
    """Rewrite asset paths in OHIF files for sub-path deployment.

    Args:
        ohif_dir: Directory containing OHIF Viewer files.
        base_path: Root URL prefix (e.g. "/liver_nir" or "").
    """
    # Normalize: ensure leading slash, no trailing slash
    base_path = ("/" + base_path.strip("/")).rstrip("/") if base_path else ""
    ohif_prefix = f"{base_path}/ohif/"
    ohif_base = f"{base_path}/ohif"

    # --- index.html ---
    index_html = ohif_dir / "index.html"
    if index_html.exists():
        html = index_html.read_text(encoding="utf-8")
        html = html.replace("window.PUBLIC_URL = '/';", f"window.PUBLIC_URL = '{ohif_prefix}';")
        html = re.sub(r'href="/(?!ohif/)(?!/)', f'href="{ohif_prefix}', html)
        html = re.sub(r'src="/(?!ohif/)(?!/)', f'src="{ohif_prefix}', html)
        html = re.sub(r'content="/(?!ohif/)(?!/)', f'content="{ohif_prefix}', html)
        index_html.write_text(html, encoding="utf-8")

    # --- JS bundles: webpack public path ---
    for js_file in ohif_dir.glob("*.bundle.*.js"):
        content = js_file.read_text(encoding="utf-8")
        patched = content.replace(
            '__webpack_require__.p = "/"', f'__webpack_require__.p = "{ohif_prefix}"'
        )
        if patched != content:
            js_file.write_text(patched, encoding="utf-8")

    # --- CSS bundle: root-relative url() references ---
    css_file = ohif_dir / "app.bundle.css"
    if css_file.exists():
        css = css_file.read_text(encoding="utf-8")
        # Rewrite url(/X) to url(<ohif_prefix>X), skip already-prefixed URLs
        escaped_prefix = re.escape(ohif_prefix)
        css = re.sub(
            rf"url\(/(?!{escaped_prefix[1:]})([^)])",
            rf"url({ohif_prefix}\1",
            css,
        )
        css_file.write_text(css, encoding="utf-8")

    # --- manifest.json: icon paths ---
    manifest = ohif_dir / "manifest.json"
    if manifest.exists():
        content = manifest.read_text(encoding="utf-8")
        content = content.replace('"/assets/', f'"{ohif_prefix}assets/')
        manifest.write_text(content, encoding="utf-8")

    # --- app-config.js: routerBasename and DICOMweb paths ---
    config_file = ohif_dir / "app-config.js"
    if config_file.exists():
        config = config_file.read_text(encoding="utf-8")
        config = config.replace("routerBasename: '/ohif'", f"routerBasename: '{ohif_base}'")
        dicomweb_path = f"{base_path}/dicom-web"
        config = config.replace("'/dicom-web'", f"'{dicomweb_path}'")
        config_file.write_text(config, encoding="utf-8")


def _clean_ohif_dir(ohif_dir: Path, preserve_config: bool) -> None:
    """Remove files from OHIF dir, preserving .ohif-version and optionally app-config.js."""
    if not ohif_dir.exists():
        return
    keep = {".ohif-version"}
    if preserve_config:
        keep.add("app-config.js")
    for item in ohif_dir.iterdir():
        if item.name in keep:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def _install_config_template(ohif_dir: Path) -> None:
    """Copy app-config.js template from package to runtime directory."""
    import clarinet

    template = Path(clarinet.__file__).parent / "ohif" / "app-config.js"
    if template.exists():
        shutil.copy2(template, ohif_dir / "app-config.js")
        logger.info("Installed app-config.js from package template")
    else:
        logger.warning("app-config.js template not found in package")


_DOWNLOAD_TIMEOUT = 30.0


def _download_file(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` with a socket timeout.

    ``urllib.request.urlretrieve`` has no timeout and hangs forever on a dead
    network. The timeout bounds each socket operation (connect/read), not the
    total download time, so large but live downloads are unaffected.
    """
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as resp, dest.open("wb") as f:
        shutil.copyfileobj(resp, f)


def install_ohif(
    version: str | None = None,
    force_config: bool = False,
    from_file: str | None = None,
) -> None:
    """Download OHIF Viewer from npm and install into runtime directory.

    Args:
        version: OHIF version to install (default from settings).
        force_config: Overwrite existing app-config.js with package template.
        from_file: Path to a local .tgz tarball (skip download).
    """
    version = version or settings.ohif_default_version
    ohif_dir = settings.ohif_path

    # Idempotent: skip if already installed at this version
    version_file = ohif_dir / ".ohif-version"
    if version_file.exists() and version_file.read_text().strip() == version and not force_config:
        logger.info(f"OHIF Viewer v{version} already installed at {ohif_dir}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if from_file:
            tarball = Path(from_file)
            if not tarball.exists():
                logger.error(f"OHIF tarball not found: {from_file}")
                sys.exit(1)
            logger.info(f"Installing OHIF Viewer v{version} from {from_file}")
        else:
            npm_url = f"https://registry.npmjs.org/@ohif/app/-/app-{version}.tgz"
            logger.info(f"Downloading OHIF Viewer v{version} from npm...")
            tarball = tmp_path / "ohif.tgz"
            try:
                _download_file(npm_url, tarball)
            except Exception as e:
                logger.error(f"Failed to download OHIF v{version}: {e}")
                sys.exit(1)

        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(tmp_path)

        dist_dir = tmp_path / "package" / "dist"
        if not dist_dir.exists():
            logger.error("dist/ directory not found in @ohif/app package")
            sys.exit(1)

        # Prepare target directory
        ohif_dir.mkdir(parents=True, exist_ok=True)
        preserve_config = not force_config and (ohif_dir / "app-config.js").exists()
        _clean_ohif_dir(ohif_dir, preserve_config=preserve_config)

        # Copy built files
        shutil.copytree(dist_dir, ohif_dir, dirs_exist_ok=True)

        # Install config template if needed
        if not preserve_config:
            _install_config_template(ohif_dir)

    # Patch paths for sub-path deployment
    base_path = settings.root_url
    logger.info(f"Patching asset paths for {base_path}/ohif/ base path...")
    _patch_ohif_paths(ohif_dir, base_path=base_path)

    # Write version marker
    version_file.write_text(version)
    logger.info(f"OHIF Viewer v{version} installed to {ohif_dir}")


def ohif_status() -> None:
    """Show OHIF Viewer installation status."""
    ohif_dir = settings.ohif_path
    version_file = ohif_dir / ".ohif-version"
    index_file = ohif_dir / "index.html"

    if not ohif_dir.exists():
        print("OHIF Viewer: not installed")
        print(f"  Expected path: {ohif_dir}")
        print("  Run 'clarinet ohif install' to install")
        return

    version = version_file.read_text().strip() if version_file.exists() else "unknown"
    has_index = index_file.exists()

    print(f"OHIF Viewer: v{version}")
    print(f"  Path: {ohif_dir}")
    print(f"  index.html: {'found' if has_index else 'MISSING'}")
    print(f"  Config: {'found' if (ohif_dir / 'app-config.js').exists() else 'MISSING'}")

    if not has_index:
        print("  WARNING: index.html missing, viewer may not work")


def uninstall_ohif() -> None:
    """Remove OHIF Viewer runtime files."""
    ohif_dir = settings.ohif_path
    if not ohif_dir.exists():
        logger.info("OHIF Viewer not installed, nothing to remove")
        return
    shutil.rmtree(ohif_dir)
    logger.info(f"OHIF Viewer removed from {ohif_dir}")


def handle_ohif_command(args: argparse.Namespace) -> None:
    """Handle OHIF-related commands."""
    if args.ohif_command == "install":
        install_ohif(
            version=args.version,
            force_config=args.force_config,
            from_file=getattr(args, "from_file", None),
        )
    elif args.ohif_command == "status":
        ohif_status()
    elif args.ohif_command == "uninstall":
        uninstall_ohif()
    else:
        logger.error(f"Unknown ohif command: {args.ohif_command}")
        sys.exit(1)


def _quarto_tarball_version(tarball: Path) -> str | None:
    """Read the Quarto version from the tarball's ``quarto-<version>/`` top dir.

    Only the first few member headers are read — no full decompression.
    Returns None when the archive layout is unexpected.
    """
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            for _ in range(5):
                member = tf.next()
                if member is None:
                    break
                match = re.match(r"(?:\./)?quarto-(\d[^/]*)(?:/|$)", member.name)
                if match:
                    return match.group(1)
    except (tarfile.TarError, OSError) as e:
        logger.warning(f"Failed to inspect Quarto tarball {tarball}: {e}")
    return None


def install_quarto(version: str | None = None, from_file: str | None = None) -> None:
    """Install the Quarto CLI into the runtime directory.

    Quarto is a self-contained tarball that bundles pandoc + typst, so neither
    a system pandoc nor LaTeX is required. It is NOT a pip package — this
    mirrors ``clarinet ohif install``: download from GitHub releases, or use a
    local tarball (``from_file``) for air-gapped hosts (e.g. Astra Linux).

    Args:
        version: Quarto version to install (default from settings).
        from_file: Path to a local ``quarto-*-linux-amd64.tar.gz`` (skip download).
    """
    tarball = Path(from_file) if from_file else None
    if tarball is not None:
        if not tarball.exists():
            logger.error(f"Quarto tarball not found: {from_file}")
            sys.exit(1)
        if version is None:
            # Without this, the version marker would record the settings
            # default instead of what the tarball actually contains.
            version = _quarto_tarball_version(tarball)
            if version is None:
                logger.warning(
                    f"Could not determine the Quarto version from {from_file}; "
                    f"assuming default v{settings.quarto_default_version}"
                )
    version = version or settings.quarto_default_version
    quarto_dir = settings.quarto_install_path

    # Idempotent: skip if already installed at this version.
    version_file = quarto_dir / ".quarto-version"
    if version_file.exists() and version_file.read_text().strip() == version:
        logger.info(f"Quarto v{version} already installed at {quarto_dir}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if tarball is not None:
            logger.info(f"Installing Quarto v{version} from {from_file}")
        else:
            url = (
                "https://github.com/quarto-dev/quarto-cli/releases/download/"
                f"v{version}/quarto-{version}-linux-amd64.tar.gz"
            )
            logger.info(f"Downloading Quarto v{version} from {url} ...")
            tarball = tmp_path / "quarto.tar.gz"
            try:
                _download_file(url, tarball)
            except Exception as e:
                logger.error(f"Failed to download Quarto v{version}: {e}")
                sys.exit(1)

        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(tmp_path, filter="data")  # reject members escaping tmp_path

        # The tarball's top-level directory is ``quarto-<version>/`` (bin/, share/).
        extracted = next(
            (p for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("quarto")),
            None,
        )
        if extracted is None or not (extracted / "bin" / "quarto").exists():
            logger.error("Could not find quarto/bin/quarto inside the tarball")
            sys.exit(1)

        if quarto_dir.exists():
            shutil.rmtree(quarto_dir)
        quarto_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted, quarto_dir, dirs_exist_ok=True)

    # Ensure the launcher is executable, then write the version marker.
    (quarto_dir / "bin" / "quarto").chmod(0o755)
    version_file.write_text(version)
    logger.info(f"Quarto v{version} installed to {quarto_dir}")
    logger.info("Run 'clarinet quarto status' to verify (runs 'quarto check').")


def quarto_status() -> None:
    """Show Quarto installation status and run ``quarto check``."""
    from clarinet.services.quarto_render import build_render_env, resolve_quarto_executable

    executable = resolve_quarto_executable()
    if executable is None:
        print("Quarto: not installed")
        print(f"  Expected path: {settings.quarto_install_path / 'bin' / 'quarto'}")
        print("  Run 'clarinet quarto install' (add '--from-file <tarball>' to install offline)")
        return

    print(f"Quarto executable: {executable}")
    version_file = settings.quarto_install_path / ".quarto-version"
    if version_file.exists():
        print(f"  Installed version marker: {version_file.read_text().strip()}")

    # `quarto check` reports the bundled pandoc/typst and the Python/Jupyter
    # setup used to execute .qmd code chunks. On older hosts (e.g. Astra Linux
    # SE 1.7) it surfaces glibc incompatibilities immediately.
    try:
        # Quarto's startup dotenv loader reads .env/.env.example from the CWD
        # and aborts when example vars are undefined — run the check from an
        # empty temp dir so it never depends on the operator's project files.
        # The check also runs in the same minimal environment real renders use
        # (build_render_env), so a green status reproduces render conditions
        # instead of the operator's shell environment.
        with tempfile.TemporaryDirectory() as check_cwd:
            tmp_dir = Path(check_cwd) / "tmp"
            tmp_dir.mkdir()
            env = build_render_env(Path(check_cwd), tmp_dir)
            print(f"Kernel interpreter: {env['QUARTO_PYTHON']}")
            result = subprocess.run(
                [str(executable), "check"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=check_cwd,
                env=env,
            )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
            print("  Status: 'quarto check' reported problems (see above)")
    except Exception as e:
        print(f"  Status: failed to run 'quarto check': {e}")


def uninstall_quarto() -> None:
    """Remove the installed Quarto CLI."""
    quarto_dir = settings.quarto_install_path
    if not quarto_dir.exists():
        print("Quarto: not installed")
        return
    shutil.rmtree(quarto_dir)
    logger.info(f"Quarto removed from {quarto_dir}")


def cleanup_quarto_renders(days: int) -> None:
    """Delete rendered Quarto outputs older than ``days``.

    Each render leaves a ``<name>/<render_id>/`` directory under the output
    path (the ``.qmd`` copy, materialized CSVs — which may hold report data —
    and the DOCX/PDF). They are not needed once downloaded; pruning them bounds
    disk use and limits how long report data sits on disk.
    """
    if days < 1:
        logger.error("--days must be >= 1 (refusing to wipe all renders)")
        sys.exit(1)
    output_path = settings.get_quarto_output_path()
    if not output_path.is_dir():
        print(f"No Quarto output directory at {output_path}; nothing to clean")
        return
    cutoff = time.time() - days * 86400
    removed = 0
    for report_dir in output_path.iterdir():
        if not report_dir.is_dir():
            continue
        for render_dir in report_dir.iterdir():
            if render_dir.is_dir() and render_dir.stat().st_mtime < cutoff:
                try:
                    shutil.rmtree(render_dir)
                except OSError as exc:
                    logger.warning(f"Failed to remove {render_dir}: {exc}")
                    continue
                removed += 1
        with contextlib.suppress(OSError):
            report_dir.rmdir()  # remove the report folder if now empty
    logger.info(
        f"Quarto cleanup: removed {removed} render(s) older than {days}d from {output_path}"
    )


def generate_report_types() -> None:
    """Generate ``review/report_schemas.py`` (pandera) from the ``*.sql`` reports.

    Connects to the configured PostgreSQL database to read each report's result
    column types (no rows fetched), then writes one module the ``*.qmd`` reports
    import for typed, dtype-coerced DataFrames. Re-run after adding or changing
    a report; commit the result.
    """
    asyncio.run(_generate_report_types())


def cmd_quarto_new(args: argparse.Namespace) -> None:
    """Handle ``clarinet quarto new`` — scaffold a .qmd + reference.docx."""
    from clarinet.exceptions.domain import QuartoNotInstalledError, QuartoScaffoldError

    formats = {"docx": ["docx"], "pdf": ["pdf"], "both": ["docx", "pdf"]}[args.format]
    data_reports = [item.strip() for item in args.data.split(",") if item.strip()]
    from_docx = Path(args.from_docx) if args.from_docx else None
    try:
        scaffold_quarto_report(
            args.name,
            title=args.title,
            description=args.description,
            lang=args.lang,
            formats=formats,
            data_reports=data_reports,
            from_docx=from_docx,
            force=args.force,
        )
    except (QuartoScaffoldError, QuartoNotInstalledError) as exc:
        logger.error(str(exc))
        sys.exit(1)


async def _generate_report_types() -> None:
    from clarinet.exceptions.domain import ReportQueryError
    from clarinet.repositories.report_repository import ReportColumn, ReportRepository
    from clarinet.utils.db_manager import db_manager
    from clarinet.utils.report_discovery import discover_report_templates
    from clarinet.utils.report_schema_codegen import (
        ReportSpec,
        duplicate_column_names,
        render_schemas_module,
    )

    reports_dir = settings.get_reports_path()
    discovered = discover_report_templates(reports_dir)
    if not discovered:
        logger.warning(f"No *.sql reports found in {reports_dir}; nothing to generate")
        return

    repo = ReportRepository()
    specs: list[ReportSpec] = []
    try:
        for template, sql in discovered:
            columns: list[ReportColumn] = await repo.describe_report(sql)
            specs.append((template.name, columns))
            logger.info(f"Report '{template.name}': {len(columns)} column(s)")
            if dups := duplicate_column_names(columns):
                logger.warning(
                    f"Report '{template.name}': duplicate column name(s) {dups} — "
                    "alias them in SQL or coercion won't apply to the later copy"
                )
    except ReportQueryError as exc:
        # opt(exception=) so the underlying planner/SQL error (preserved on
        # __cause__) reaches the operator — there is no API handler on the CLI
        # path to log the traceback for us.
        logger.opt(exception=exc).error(f"Type generation failed: {exc}")
        sys.exit(1)
    finally:
        await db_manager.close()

    out_path = reports_dir / "report_schemas.py"
    out_path.write_text(render_schemas_module(specs), encoding="utf-8")
    logger.info(
        f"Wrote {out_path} ({len(specs)} schema(s)). Commit it — the .qmd reports import it."
    )


def handle_quarto_command(args: argparse.Namespace) -> None:
    """Handle Quarto-related commands."""
    if args.quarto_command == "install":
        install_quarto(version=args.version, from_file=getattr(args, "from_file", None))
    elif args.quarto_command == "status":
        quarto_status()
    elif args.quarto_command == "uninstall":
        uninstall_quarto()
    elif args.quarto_command == "cleanup":
        cleanup_quarto_renders(days=args.days)
    elif args.quarto_command == "gen-types":
        generate_report_types()
    elif args.quarto_command == "new":
        cmd_quarto_new(args)
    else:
        logger.error(f"Unknown quarto command: {args.quarto_command}")
        sys.exit(1)


async def _rabbitmq_clean(dry_run: bool = False) -> None:
    """Delete orphaned test queues/exchanges from RabbitMQ."""
    from clarinet.services.pipeline.rabbitmq_cleanup import cleanup_test_resources

    result = await cleanup_test_resources(
        base_url=settings.rabbitmq_management_base_url,
        auth=settings.rabbitmq_management_auth,
        dry_run=dry_run,
    )

    if dry_run:
        print(
            f"Found {result['queues_found']} test queues, {result['exchanges_found']} test exchanges"
        )
        print("Run without --dry-run to delete them.")
    else:
        print(f"Deleted {result['queues_deleted']} queues, {result['exchanges_deleted']} exchanges")


async def _rabbitmq_status() -> None:
    """Show RabbitMQ queue statistics."""
    from clarinet.services.pipeline.rabbitmq_cleanup import get_queue_stats

    stats = await get_queue_stats(
        base_url=settings.rabbitmq_management_base_url,
        auth=settings.rabbitmq_management_auth,
    )

    print("RabbitMQ Queue Statistics")
    print(f"  Total queues:     {stats['total_queues']}")
    print(f"  Test queues:      {stats['test_queues']}")
    print(f"  Total exchanges:  {stats['total_exchanges']}")
    print(f"  Test exchanges:   {stats['test_exchanges']}")
    print(f"  Stuck messages:   {stats['stuck_messages']}")


async def _rabbitmq_purge_stale(queues: list[str] | None = None, force: bool = False) -> None:
    """Purge messages from production queues (migration helper)."""
    from clarinet.services.pipeline.rabbitmq_cleanup import purge_queue_messages

    result = await purge_queue_messages(
        base_url=settings.rabbitmq_management_base_url,
        auth=settings.rabbitmq_management_auth,
        queue_names=queues,
        dry_run=not force,
    )

    if not force:
        print(f"Found {result['messages_found']} messages in production queues")
        print("Run with --force to purge them.")
    else:
        print(f"Purged {result['messages_purged']} messages from {result['queues_purged']} queues")


def handle_frontend_command(args: argparse.Namespace) -> None:
    """Handle frontend-related commands."""
    if args.frontend_command == "install":
        install_frontend()
    elif args.frontend_command == "build":
        build_frontend(watch=args.watch)
    elif args.frontend_command == "clean":
        clean_frontend()
    else:
        logger.error(f"Unknown frontend command: {args.frontend_command}")
        sys.exit(1)


async def _handle_session(args: argparse.Namespace) -> None:
    """Handle session management subcommands.

    Each branch acquires its own AsyncSession via ``get_async_session()``.
    """
    from datetime import UTC, datetime, timedelta
    from typing import Any
    from uuid import UUID

    from sqlalchemy import CursorResult
    from sqlalchemy import delete as sa_delete

    from clarinet.models.auth import AccessToken
    from clarinet.utils.database import get_async_session
    from clarinet.utils.session import (
        cleanup_expired_sessions,
        get_session_stats,
        get_user_sessions,
        revoke_user_sessions,
    )

    if args.session_command == "cleanup":
        async for session in get_async_session():
            deleted = await cleanup_expired_sessions(session)
            if args.days:
                cutoff = datetime.now(UTC) - timedelta(days=args.days)
                stmt = sa_delete(AccessToken).where(
                    AccessToken.created_at < cutoff  # type:ignore[arg-type]
                )
                old_result: CursorResult[Any] = await session.execute(stmt)  # type: ignore[assignment]
                await session.commit()
                if old_result.rowcount > 0:
                    deleted += old_result.rowcount
                    logger.info(
                        f"Removed {old_result.rowcount} sessions older than {args.days} days"
                    )
            print(f"Cleaned up {deleted} sessions")

    elif args.session_command == "cleanup-once":
        from clarinet.services.session_cleanup import SessionCleanupService

        deleted = await SessionCleanupService().cleanup_once()
        print(f"Cleaned up {deleted} sessions")

    elif args.session_command == "stats":
        async for session in get_async_session():
            stats = await get_session_stats(session)
            print("Session Statistics:")
            print(f"  Total:        {stats['total']}")
            print(f"  Active:       {stats['active']}")
            print(f"  Expired:      {stats['expired']}")
            print(f"  Avg duration: {stats['average_duration_hours']}h")
            print("  Active sessions by age:")
            for bucket, count in stats["by_age"].items():
                label = bucket.replace("_", " ")
                print(f"    {label}: {count}")

    elif args.session_command == "revoke-user":
        try:
            user_id = UUID(args.user_id)
        except ValueError:
            logger.error(f"Invalid user ID (expected UUID): {args.user_id}")
            sys.exit(1)
        async for session in get_async_session():
            count = await revoke_user_sessions(session, user_id)
            print(f"Revoked {count} sessions for user {user_id}")

    elif args.session_command == "list-user":
        try:
            user_id = UUID(args.user_id)
        except ValueError:
            logger.error(f"Invalid user ID (expected UUID): {args.user_id}")
            sys.exit(1)
        async for session in get_async_session():
            sessions = await get_user_sessions(session, user_id, active_only=False)
            if not sessions:
                print(f"No sessions found for user {user_id}")
                return
            now = datetime.now(UTC)
            print(f"Sessions for user {user_id} ({len(sessions)} total):")
            for s in sessions:
                status = "active" if s.expires_at > now else "expired"
                print(
                    f"  {s.token[:8]}... | {status} | "
                    f"created: {s.created_at.isoformat()} | "
                    f"expires: {s.expires_at.isoformat()}"
                )

    elif args.session_command == "cleanup-all":
        async for session in get_async_session():
            stmt = sa_delete(AccessToken)
            all_result: CursorResult[Any] = await session.execute(stmt)  # type: ignore[assignment]
            await session.commit()
            print(f"Deleted all {all_result.rowcount} sessions")


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="clarinet", description="Clarinet Framework CLI - Medical Image Analysis Framework"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize a new Clarinet project")
    init_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path where to create the project (default: current directory)",
    )
    init_parser.add_argument(
        "--template",
        "-t",
        help="Initialize from a template (use --list-templates to see available)",
    )
    init_parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List available project templates",
    )

    # run command
    run_parser = subparsers.add_parser("run", help="Run the development server")
    run_parser.add_argument(
        "--host", type=str, default=None, help="Host to bind to (default: 127.0.0.1)"
    )
    run_parser.add_argument(
        "--port", type=int, default=None, help="Port to bind to (default: 8000)"
    )
    run_parser.add_argument(
        "--headless", action="store_true", help="Run API server only, without the frontend"
    )

    # db command
    db_parser = subparsers.add_parser("db", help="Database management")
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    db_subparsers.add_parser("init", help="Initialize database with tables and admin user")

    # db migrate (default: upgrade to head)
    migrate_parser = db_subparsers.add_parser(
        "migrate", help="Run pending database migrations (default: upgrade to head)"
    )
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_command")
    migrate_subparsers.add_parser("status", help="Show current and pending migrations")
    migrate_subparsers.add_parser("history", help="Show migration history")
    migrate_down_parser = migrate_subparsers.add_parser("down", help="Rollback migrations")
    migrate_down_parser.add_argument(
        "steps", nargs="?", type=int, default=1, help="Number of migrations to rollback"
    )
    migrate_create_parser = migrate_subparsers.add_parser(
        "create", help="Create a new migration (autogenerate from models)"
    )
    migrate_create_parser.add_argument("-m", "--message", required=True, help="Migration message")

    # admin command
    admin_parser = subparsers.add_parser("admin", help="Admin user management")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command")

    # admin create subcommand
    admin_create = admin_subparsers.add_parser("create", help="Create admin user")
    admin_create.add_argument("--username", type=str, default=None, help="Admin username")
    admin_create.add_argument("--email", type=str, default=None, help="Admin email")
    admin_create.add_argument(
        "--password", type=str, default=None, help="Admin password (will prompt if not provided)"
    )

    # admin reset-password subcommand
    admin_reset = admin_subparsers.add_parser("reset-password", help="Reset admin password")
    admin_reset.add_argument(
        "--username", type=str, default="admin", help="Admin username to reset"
    )

    # init-migrations command
    subparsers.add_parser("init-migrations", help="Initialize Alembic migrations for the project")

    # frontend command
    frontend_parser = subparsers.add_parser("frontend", help="Frontend management commands")
    frontend_subparsers = frontend_parser.add_subparsers(dest="frontend_command")

    # frontend install
    frontend_subparsers.add_parser("install", help="Install Gleam and frontend dependencies")

    # frontend build
    build_parser = frontend_subparsers.add_parser("build", help="Build frontend for production")
    build_parser.add_argument("--watch", action="store_true", help="Watch for changes and rebuild")

    # frontend clean
    frontend_subparsers.add_parser("clean", help="Clean build artifacts")

    # ohif command
    ohif_parser = subparsers.add_parser("ohif", help="OHIF Viewer management commands")
    ohif_subparsers = ohif_parser.add_subparsers(dest="ohif_command")

    # ohif install
    ohif_install_parser = ohif_subparsers.add_parser(
        "install", help="Download and install OHIF Viewer"
    )
    ohif_install_parser.add_argument(
        "--version", type=str, default=None, help="OHIF version (default: from settings)"
    )
    ohif_install_parser.add_argument(
        "--force-config",
        action="store_true",
        help="Overwrite existing app-config.js with package template",
    )
    ohif_install_parser.add_argument(
        "--from-file",
        type=str,
        default=None,
        help="Path to local .tgz tarball (skip download)",
    )

    # ohif status
    ohif_subparsers.add_parser("status", help="Show OHIF Viewer installation status")

    # ohif uninstall
    ohif_subparsers.add_parser("uninstall", help="Remove OHIF Viewer runtime files")

    # quarto command (Quarto CLI for *.qmd reports rendered to DOCX/PDF)
    quarto_parser = subparsers.add_parser("quarto", help="Quarto CLI management commands")
    quarto_subparsers = quarto_parser.add_subparsers(dest="quarto_command")

    quarto_install_parser = quarto_subparsers.add_parser(
        "install", help="Install the Quarto CLI (renders *.qmd reports to DOCX/PDF)"
    )
    quarto_install_parser.add_argument(
        "--version", type=str, default=None, help="Quarto version (default: from settings)"
    )
    quarto_install_parser.add_argument(
        "--from-file",
        type=str,
        default=None,
        help="Path to a local quarto-*-linux-amd64.tar.gz (offline / air-gapped install)",
    )

    quarto_subparsers.add_parser(
        "status", help="Show Quarto installation status (runs 'quarto check')"
    )
    quarto_subparsers.add_parser("uninstall", help="Remove the installed Quarto CLI")

    quarto_cleanup_parser = quarto_subparsers.add_parser(
        "cleanup", help="Delete rendered Quarto outputs older than N days"
    )
    quarto_cleanup_parser.add_argument(
        "--days", type=int, default=30, help="Retention in days (default: 30)"
    )

    quarto_subparsers.add_parser(
        "gen-types",
        help="Generate review/report_schemas.py (pandera) from *.sql reports for typed .qmd DataFrames",
    )

    quarto_new_parser = quarto_subparsers.add_parser(
        "new", help="Scaffold a new Quarto report (.qmd + reference.docx)"
    )
    quarto_new_parser.add_argument("name", help="Report name → <name>.qmd")
    quarto_new_parser.add_argument("--title", help="Front-matter title (default: <name>)")
    quarto_new_parser.add_argument("--description", default="", help="Front-matter description")
    quarto_new_parser.add_argument("--lang", default="ru", help="Document language (default: ru)")
    quarto_new_parser.add_argument(
        "--format", default="docx", choices=["docx", "pdf", "both"], help="Output format(s)"
    )
    quarto_new_parser.add_argument(
        "--data", default="", help="Comma-separated SQL report names for clarinet.data"
    )
    quarto_new_parser.add_argument(
        "--from-docx", help="Existing .docx whose styles become reference.docx"
    )
    quarto_new_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing .qmd / reference.docx"
    )

    # worker command
    worker_parser = subparsers.add_parser("worker", help="Run pipeline task worker")
    worker_parser.add_argument(
        "--queues",
        nargs="*",
        default=None,
        help="Queue names to listen on (default: auto-detect from capabilities)",
    )
    worker_parser.add_argument(
        "--workers", type=int, default=2, help="Number of concurrent worker tasks (default: 2)"
    )
    worker_parser.add_argument(
        "--dicom",
        type=str,
        default=None,
        metavar="AET:PORT",
        help="Start Storage SCP for C-MOVE retrieval (e.g. WORKER:4006)",
    )
    worker_parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Override worker log file (absolute path or filename relative to log_dir)",
    )

    # rabbitmq command
    rabbitmq_parser = subparsers.add_parser("rabbitmq", help="RabbitMQ management commands")
    rabbitmq_subparsers = rabbitmq_parser.add_subparsers(dest="rabbitmq_command")

    # rabbitmq clean
    rabbitmq_clean_parser = rabbitmq_subparsers.add_parser(
        "clean", help="Delete orphaned test queues/exchanges"
    )
    rabbitmq_clean_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be deleted without deleting"
    )

    # rabbitmq status
    rabbitmq_subparsers.add_parser("status", help="Show RabbitMQ queue statistics")

    # rabbitmq purge-stale
    purge_parser = rabbitmq_subparsers.add_parser(
        "purge-stale", help="Purge messages from production queues"
    )
    purge_parser.add_argument(
        "--force", action="store_true", help="Actually purge (default: dry-run)"
    )
    purge_parser.add_argument(
        "--queue", type=str, action="append", help="Specific queue(s) to purge"
    )

    # session command
    session_parser = subparsers.add_parser("session", help="Session management commands")
    session_subparsers = session_parser.add_subparsers(dest="session_command")

    # session cleanup
    cleanup_parser = session_subparsers.add_parser("cleanup", help="Clean up expired sessions")
    cleanup_parser.add_argument(
        "--days", type=int, default=30, help="Remove sessions older than N days"
    )

    # session cleanup-once
    session_subparsers.add_parser("cleanup-once", help="Run session cleanup once")

    # session stats
    session_subparsers.add_parser("stats", help="Show session statistics")

    # session revoke-user
    revoke_parser = session_subparsers.add_parser(
        "revoke-user", help="Revoke all sessions for a user"
    )
    revoke_parser.add_argument("user_id", help="User UUID")
    revoke_parser.add_argument(
        "--keep-current", action="store_true", help="Keep current session active"
    )

    # session list-user
    list_parser = session_subparsers.add_parser("list-user", help="List sessions for a user")
    list_parser.add_argument("user_id", help="User UUID")

    # session cleanup-all
    session_subparsers.add_parser("cleanup-all", help="Remove ALL sessions (dangerous!)")

    # anon command
    anon_parser = subparsers.add_parser("anon", help="Anonymization helpers")
    anon_subparsers = anon_parser.add_subparsers(dest="anon_command")

    anon_migrate = anon_subparsers.add_parser(
        "migrate-paths",
        help=(
            "Move anonymized dcm_anon directories from one disk_path_template "
            "layout to another (no DB changes)"
        ),
        description=(
            "Move anonymized dcm_anon directories from one disk_path_template "
            "layout to another (no DB changes).\n\n"
            "Supported placeholders (full reference in clarinet.utils.path_template):\n"
            "  {anon_patient_id}   anonymized patient identifier\n"
            "  {anon_study_uid}    anonymized study UID (or original if not set)\n"
            "  {anon_series_uid}   anonymized series UID (or original if not set)\n"
            "  {patient_id}        original DICOM PatientID\n"
            "  {patient_auto_id}   monotonic per-patient counter\n"
            "  {anon_id_prefix}    settings.anon_id_prefix (default 'anon')\n"
            "  {study_uid}         original study UID\n"
            "  {series_uid}        original series UID\n"
            "  {study_date}        YYYYMMDD\n"
            "  {study_modalities}  sorted modalities joined by '_' (e.g. CT_SR)\n"
            "  {series_modality}   series modality\n"
            "  {series_num}        DICOM SeriesNumber, zero-padded to 5 digits\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    anon_migrate.add_argument(
        "--from",
        dest="from_template",
        required=True,
        help=("Source template, e.g. '{anon_patient_id}/{anon_study_uid}/{anon_series_uid}'"),
    )
    anon_migrate.add_argument(
        "--to",
        dest="to_template",
        required=True,
        help="Target template (same placeholders and validation rules as --from)",
    )
    anon_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned moves without touching the filesystem",
    )
    anon_migrate.add_argument(
        "--cleanup-empty",
        action="store_true",
        help="Remove empty parent directories after a successful move",
    )
    anon_migrate.add_argument(
        "--include-working-folder",
        dest="include_working_folder",
        action="store_true",
        help=(
            "Move full series_dir (incl. pipeline outputs) and STUDY/PATIENT-level "
            "Record working_folders, not just dcm_anon. Three passes: SERIES, STUDY, PATIENT."
        ),
    )
    anon_migrate.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "Show DEBUG-level details on stderr: every rendered (old, new) path, "
            "each Series/Record being checked, and 'source missing' reasons. "
            "May produce many lines on large datasets — pair with --dry-run for a preview."
        ),
    )

    anon_scrub = anon_subparsers.add_parser(
        "scrub-db",
        help=(
            "Anonymize the configured database in place for selected patients "
            "(restore a production copy into a scratch DB first)"
        ),
        description=(
            "Anonymize the configured database in place so it can ship as a "
            "test-stand fixture: narrow to the selected patients, strip PHI "
            "(relational columns + record.data + audit-table JSON snapshots), "
            "rewrite the patient MRN to the deterministic anon_id, and audit "
            "the result for surviving PHI.\n\n"
            "Operates on settings.database_* — restore a production copy into a "
            "throwaway scratch database BEFORE running. study.anon_uid / "
            "series.anon_uid / patient.auto_id are preserved so FileRepository "
            "still resolves the anonymized DICOM."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    anon_scrub.add_argument(
        "--patients",
        required=True,
        help="Comma-separated patient ids to keep, or 'all' to scrub without subsetting",
    )
    anon_scrub.add_argument(
        "--out",
        help="Optional path for a pg_dump of the scrubbed DB ('.gz' compresses; PostgreSQL only)",
    )
    anon_scrub.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full scrub + audit, then roll back without persisting",
    )
    anon_scrub.add_argument(
        "--allow-phi-leak",
        action="store_true",
        help="Commit even if the audit finds surviving PHI (logs hits; use only to debug)",
    )
    anon_scrub.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show DEBUG-level details on stderr",
    )

    # deploy command
    deploy_parser = subparsers.add_parser("deploy", help="Generate deployment configurations")
    deploy_subparsers = deploy_parser.add_subparsers(dest="deploy_command")

    systemd_parser = deploy_subparsers.add_parser("systemd", help="Generate systemd unit files")
    systemd_parser.add_argument("--user", help="Service user (default: current user)")
    systemd_parser.add_argument("--group", help="Service group (default: user's primary group)")
    systemd_parser.add_argument("--working-dir", help="Working directory (default: cwd)")
    systemd_parser.add_argument(
        "--workers", type=int, default=2, help="Worker concurrency (default: 2)"
    )
    systemd_parser.add_argument("--output-dir", help="Write files to directory instead of stdout")
    systemd_parser.add_argument(
        "--env-file", help="Path to EnvironmentFile (default: {working-dir}/env)"
    )

    args = parser.parse_args()

    if args.command == "init":
        if getattr(args, "list_templates", False):
            from clarinet.cli.templates import list_templates

            list_templates()
        else:
            init_project(args.path, template=getattr(args, "template", None))
    elif args.command == "run":
        run_server(args.host, args.port, headless=args.headless)
    elif args.command == "db":
        if args.db_command == "init":
            asyncio.run(init_database())
        elif args.db_command == "migrate":
            from clarinet.utils.migrations import (
                cli_create,
                cli_current,
                cli_downgrade,
                cli_history,
                cli_pending,
                cli_upgrade,
            )

            migrate_sub = getattr(args, "migrate_command", None)
            if migrate_sub is None:
                cli_upgrade("head")
            elif migrate_sub == "status":
                cli_current()
                cli_pending()
            elif migrate_sub == "history":
                cli_history()
            elif migrate_sub == "down":
                cli_downgrade(args.steps)
            elif migrate_sub == "create":
                cli_create(args.message)
            else:
                migrate_parser.print_help()
        else:
            db_parser.print_help()
    elif args.command == "admin":
        if args.admin_command == "create":
            import getpass

            from clarinet.utils.bootstrap import create_admin_user

            password = args.password
            if not password:
                password = getpass.getpass("Enter admin password: ")
                confirm = getpass.getpass("Confirm password: ")
                if password != confirm:
                    logger.error("Passwords do not match")
                    sys.exit(1)

            asyncio.run(
                create_admin_user(username=args.username, email=args.email, password=password)
            )
        elif args.admin_command == "reset-password":
            import getpass

            from clarinet.utils.admin import reset_admin_password

            password = getpass.getpass("Enter new password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                logger.error("Passwords do not match")
                sys.exit(1)

            asyncio.run(reset_admin_password(args.username, password))
        else:
            admin_parser.print_help()
    elif args.command == "worker":
        queues = args.queues
        if queues:
            # Normalize queue names: "gpu" -> "{namespace}.gpu" (current project)
            from clarinet.settings import settings as _settings

            ns = _settings.pipeline_task_namespace
            queues = [q if "." in q else f"{ns}.{q}" for q in queues]
        dicom_scp = _parse_dicom_scp_arg(args.dicom) if args.dicom else None
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_pipeline_worker(
                    queues,
                    args.workers,
                    dicom_scp=dicom_scp,
                    log_file=args.log_file,
                )
            )
    elif args.command == "init-migrations":
        from clarinet.utils.migrations import init_alembic_in_project

        init_alembic_in_project()
    elif args.command == "rabbitmq":
        if args.rabbitmq_command == "clean":
            asyncio.run(_rabbitmq_clean(dry_run=args.dry_run))
        elif args.rabbitmq_command == "status":
            asyncio.run(_rabbitmq_status())
        elif args.rabbitmq_command == "purge-stale":
            asyncio.run(_rabbitmq_purge_stale(queues=args.queue, force=args.force))
        else:
            rabbitmq_parser.print_help()
    elif args.command == "frontend":
        handle_frontend_command(args)
    elif args.command == "ohif":
        handle_ohif_command(args)
    elif args.command == "quarto":
        handle_quarto_command(args)
    elif args.command == "session":
        if not args.session_command:
            session_parser.print_help()
        else:
            if args.session_command == "cleanup-all":
                confirm = input("This will delete ALL sessions. Are you sure? [y/N] ")
                if confirm.lower() != "y":
                    print("Aborted.")
                    sys.exit(0)
            asyncio.run(_handle_session(args))
    elif args.command == "anon":
        if args.anon_command == "migrate-paths":
            from clarinet.cli.anon import migrate_paths

            asyncio.run(migrate_paths(args))
        elif args.anon_command == "scrub-db":
            from clarinet.cli.anon_scrub import scrub_db

            asyncio.run(scrub_db(args))
        else:
            anon_parser.print_help()
    elif args.command == "deploy":
        if args.deploy_command == "systemd":
            from clarinet.cli.deploy import generate_systemd

            generate_systemd(args)
        else:
            deploy_parser.print_help()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
