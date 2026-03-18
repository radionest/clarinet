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
import urllib.request
from pathlib import Path

from clarinet.settings import settings
from clarinet.utils.db_manager import db_manager
from clarinet.utils.logger import logger


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

# ── Server ───────────────────────────────────────────
port = 8000
host = "127.0.0.1"
debug = true
# root_url = "/"

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
# log_noisy_libraries = ["pynetdicom"]
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

    # Skip frontend watch when pre-built static exists (wheel install).
    # The wheel includes both clarinet/static/ and clarinet/frontend/ source,
    # so checking only static_path is sufficient — no gleam needed.
    static_path = library_path / "static"
    use_prebuilt = static_path.exists()

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


async def _run_pipeline_worker(queues: list[str] | None, workers: int) -> None:
    """Run the pipeline task worker.

    Args:
        queues: Queue names to listen on (auto-detected if None).
        workers: Number of concurrent worker tasks.
    """
    from clarinet.services.pipeline import run_worker

    if not settings.pipeline_enabled:
        logger.warning(
            "Pipeline is disabled (pipeline_enabled=False). "
            "Set CLARINET_PIPELINE_ENABLED=true to enable."
        )

    await run_worker(queues=queues, workers=workers)


async def init_database() -> None:
    """Initialize the database with tables and default data."""
    from clarinet.utils.bootstrap import initialize_application_data

    logger.info("Initializing database...")
    await db_manager.create_db_and_tables_async()
    await initialize_application_data()  # Changed from add_default_user_roles
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


def _patch_ohif_paths(ohif_dir: Path) -> None:
    """Rewrite asset paths in OHIF files for /ohif/ base path.

    Ports the path-patching logic from the former build_ohif.sh into pure Python.
    """
    # --- index.html ---
    index_html = ohif_dir / "index.html"
    if index_html.exists():
        html = index_html.read_text()
        html = html.replace("window.PUBLIC_URL = '/';", "window.PUBLIC_URL = '/ohif/';")
        html = re.sub(r'href="/(?!ohif/)(?!/)', 'href="/ohif/', html)
        html = re.sub(r'src="/(?!ohif/)(?!/)', 'src="/ohif/', html)
        html = re.sub(r'content="/(?!ohif/)(?!/)', 'content="/ohif/', html)
        index_html.write_text(html)

    # --- JS bundles: webpack public path ---
    for js_file in ohif_dir.glob("*.bundle.*.js"):
        content = js_file.read_text()
        patched = content.replace('__webpack_require__.p = "/"', '__webpack_require__.p = "/ohif/"')
        if patched != content:
            js_file.write_text(patched)

    # --- CSS bundle: root-relative url() references ---
    css_file = ohif_dir / "app.bundle.css"
    if css_file.exists():
        css = css_file.read_text()
        css = re.sub(r"url\(/([^o)])", r"url(/ohif/\1", css)
        css_file.write_text(css)

    # --- manifest.json: icon paths ---
    manifest = ohif_dir / "manifest.json"
    if manifest.exists():
        content = manifest.read_text()
        content = content.replace('"/assets/', '"/ohif/assets/')
        manifest.write_text(content)


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


def install_ohif(version: str | None = None, force_config: bool = False) -> None:
    """Download OHIF Viewer from npm and install into runtime directory.

    Args:
        version: OHIF version to install (default from settings).
        force_config: Overwrite existing app-config.js with package template.
    """
    version = version or settings.ohif_default_version
    ohif_dir = settings.ohif_path

    # Idempotent: skip if already installed at this version
    version_file = ohif_dir / ".ohif-version"
    if version_file.exists() and version_file.read_text().strip() == version and not force_config:
        logger.info(f"OHIF Viewer v{version} already installed at {ohif_dir}")
        return

    npm_url = f"https://registry.npmjs.org/@ohif/app/-/app-{version}.tgz"
    logger.info(f"Downloading OHIF Viewer v{version} from npm...")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tarball = tmp_path / "ohif.tgz"

        try:
            urllib.request.urlretrieve(npm_url, tarball)
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

    # Patch paths for /ohif/ base
    logger.info("Patching asset paths for /ohif/ base path...")
    _patch_ohif_paths(ohif_dir)

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
        install_ohif(version=args.version, force_config=args.force_config)
    elif args.ohif_command == "status":
        ohif_status()
    elif args.ohif_command == "uninstall":
        uninstall_ohif()
    else:
        logger.error(f"Unknown ohif command: {args.ohif_command}")
        sys.exit(1)


async def _rabbitmq_clean(dry_run: bool = False) -> None:
    """Delete orphaned test queues/exchanges from RabbitMQ."""
    from clarinet.services.pipeline.rabbitmq_cleanup import cleanup_test_resources

    result = await cleanup_test_resources(
        host=settings.rabbitmq_host,
        management_port=settings.rabbitmq_management_port,
        login=settings.rabbitmq_login,
        password=settings.rabbitmq_password,
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
        host=settings.rabbitmq_host,
        management_port=settings.rabbitmq_management_port,
        login=settings.rabbitmq_login,
        password=settings.rabbitmq_password,
    )

    print("RabbitMQ Queue Statistics")
    print(f"  Total queues:     {stats['total_queues']}")
    print(f"  Test queues:      {stats['test_queues']}")
    print(f"  Total exchanges:  {stats['total_exchanges']}")
    print(f"  Test exchanges:   {stats['test_exchanges']}")
    print(f"  Stuck messages:   {stats['stuck_messages']}")


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
    db_subparsers.add_parser("init", help="Initialize database with tables")

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

    # ohif status
    ohif_subparsers.add_parser("status", help="Show OHIF Viewer installation status")

    # ohif uninstall
    ohif_subparsers.add_parser("uninstall", help="Remove OHIF Viewer runtime files")

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
            # Normalize queue names: "gpu" -> "clarinet.gpu"
            queues = [q if "." in q else f"clarinet.{q}" for q in queues]
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_run_pipeline_worker(queues, args.workers))
    elif args.command == "init-migrations":
        from clarinet.utils.migrations import init_alembic_in_project

        init_alembic_in_project()
    elif args.command == "rabbitmq":
        if args.rabbitmq_command == "clean":
            asyncio.run(_rabbitmq_clean(dry_run=args.dry_run))
        elif args.rabbitmq_command == "status":
            asyncio.run(_rabbitmq_status())
        else:
            rabbitmq_parser.print_help()
    elif args.command == "frontend":
        handle_frontend_command(args)
    elif args.command == "ohif":
        handle_ohif_command(args)
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
