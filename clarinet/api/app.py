"""
Main API application module for Clarinet.

This module creates and configures the FastAPI application with all routers,
middleware, and static files.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from string import Template

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware

try:
    import orjson  # noqa: F401
    from fastapi.responses import ORJSONResponse

    _default_response_class: type[ORJSONResponse] | None = ORJSONResponse
except ImportError:
    _default_response_class = None

from clarinet.api.exception_handlers import setup_exception_handlers

# Use relative imports for development
from clarinet.api.routers import admin as admin
from clarinet.api.routers import auth as auth
from clarinet.api.routers import dicom as dicom
from clarinet.api.routers import dicomweb as dicomweb
from clarinet.api.routers import health as health
from clarinet.api.routers import info as info
from clarinet.api.routers import pipeline as pipeline
from clarinet.api.routers import record as record
from clarinet.api.routers import slicer  # slicer doesn't use database, no async version needed,
from clarinet.api.routers import study as study
from clarinet.api.routers import user as user
from clarinet.api.routers import viewer as viewer
from clarinet.exceptions.domain import RecordFlowError
from clarinet.services.session_cleanup import session_cleanup_service
from clarinet.settings import settings
from clarinet.utils.admin import ensure_admin_exists
from clarinet.utils.bootstrap import (
    add_default_user_roles,
    reconcile_config,
)
from clarinet.utils.db_manager import db_manager
from clarinet.utils.file_registry_resolver import load_project_file_registry
from clarinet.utils.fs import shutdown_fs_executor
from clarinet.utils.logger import logger


class StartupError(SystemExit):
    """Raised when an enabled component fails to initialize at startup."""

    def __init__(self, component: str, reason: str, hint: str) -> None:
        self.component = component
        self.reason = reason
        self.hint = hint
        message = (
            f"\n{'=' * 60}\n"
            f"STARTUP FAILED: {component}\n"
            f"{'=' * 60}\n"
            f"Reason: {reason}\n\n"
            f"To fix, either:\n"
            f"  1. {hint}\n"
            f"  2. Disable the component: set CLARINET_{component.upper().replace(' ', '_')}_ENABLED=false\n"
            f"{'=' * 60}\n"
        )
        super().__init__(message)


def _check_frontend() -> None:
    """Verify frontend static files exist when frontend is enabled."""
    if not settings.frontend_enabled:
        return
    for dir_path in settings.static_directories:
        if dir_path.exists():
            return
    raise StartupError(
        component="Frontend",
        reason="No static directories found.",
        hint="Build the frontend: make frontend-build",
    )


def _check_ohif() -> None:
    """Verify OHIF Viewer files exist when OHIF is enabled."""
    if not settings.ohif_enabled:
        return
    ohif_index = settings.ohif_path / "index.html"
    if not ohif_index.exists():
        raise StartupError(
            component="OHIF",
            reason=f"index.html not found at {settings.ohif_path}",
            hint="Install OHIF Viewer: clarinet ohif install",
        )


async def _init_recordflow(app: FastAPI) -> None:
    """Initialize RecordFlow engine and attach to app state."""
    from clarinet.client import ClarinetClient
    from clarinet.services.recordflow import RecordFlowEngine, discover_and_load_flows

    client = ClarinetClient(
        base_url=settings.effective_api_base_url,
        username=settings.admin_email,
        password=settings.admin_password,
        auto_login=False,
        verify_ssl=settings.api_verify_ssl,
    )

    engine = RecordFlowEngine(client)

    flow_paths = [Path(p) for p in settings.recordflow_paths]
    if flow_paths:
        discover_and_load_flows(engine, flow_paths)

    app.state.recordflow_engine = engine
    logger.info("RecordFlow engine initialized")


async def _shutdown_recordflow(app: FastAPI) -> None:
    """Close RecordFlow client connection."""
    try:
        await app.state.recordflow_engine.clarinet_client.close()
        logger.info("RecordFlow client closed")
    except AttributeError as e:
        raise RecordFlowError("Cant find clarinet web client in recordflow engine.") from e


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """
    Application lifespan context manager.

    Verifies alembic migrations are applied, then creates any missing tables,
    adds default roles, and loads record types.
    """
    # Fail-fast: alembic migrations must be applied before any DB access.
    # All production projects are initialized via ``clarinet init-migrations``.
    from clarinet.exceptions import MigrationError
    from clarinet.utils.migrations import verify_migrations_applied

    try:
        verify_migrations_applied()
    except MigrationError as e:
        # ``verify_migrations_applied`` encodes a case-specific remediation in
        # the message ("... Run: clarinet init-migrations" / "... Run: clarinet
        # db migrate" / "... Run: clarinet db migrate status"). Split it out so
        # the StartupError banner shows the *right* command for each case
        # instead of collapsing everything into a single generic hint.
        msg = str(e)
        reason, sep, hint_cmd = msg.rpartition(" Run: ")
        if sep:
            reason = reason.rstrip()
            hint = f"Run: {hint_cmd.strip()}"
        else:
            reason = msg
            hint = "Run: clarinet db migrate"
        raise StartupError(
            component="Database",
            reason=reason,
            hint=hint,
        ) from e

    await db_manager.create_db_and_tables_async()
    logger.info("Database initialized with async support")

    await add_default_user_roles()

    # Reconcile RecordType definitions
    reconcile_result = await reconcile_config()
    app.state.config_mode = settings.config_mode
    app.state.config_tasks_path = settings.config_tasks_path
    logger.info(
        f"Config reconcile ({settings.config_mode} mode): "
        f"{len(reconcile_result.created)} created, "
        f"{len(reconcile_result.updated)} updated, "
        f"{len(reconcile_result.orphaned)} orphaned"
    )

    # Load project file registry for API use
    app.state.project_file_registry = await load_project_file_registry(settings.config_tasks_path)

    # Load custom schema hydrators from tasks folder
    from clarinet.services.schema_hydration import load_custom_hydrators

    hydrator_count = load_custom_hydrators(settings.config_tasks_path)
    if hydrator_count:
        logger.info(f"Loaded {hydrator_count} custom schema hydrator(s)")

    # Load custom slicer context hydrators from tasks folder
    from clarinet.services.slicer.context_hydration import load_custom_slicer_hydrators

    slicer_hydrator_count = load_custom_slicer_hydrators(settings.config_tasks_path)
    if slicer_hydrator_count:
        logger.info(f"Loaded {slicer_hydrator_count} custom slicer context hydrator(s)")

    try:
        await ensure_admin_exists()
    except RuntimeError as e:
        logger.critical(f"Startup failed: {e}")
        if not settings.debug:
            raise

    # Fail-fast: verify enabled components are available
    _check_frontend()
    _check_ohif()

    # Initialize viewer plugin registry
    from clarinet.services.viewer import build_viewer_registry
    from clarinet.services.viewer.registry import ViewerConfig

    try:
        viewer_configs = {name: ViewerConfig(**cfg) for name, cfg in settings.viewers.items()}
        app.state.viewer_registry = build_viewer_registry(viewer_configs)
    except Exception as e:
        raise StartupError(
            component="Viewers",
            reason=str(e),
            hint="Fix [viewers.*] configuration in settings.toml",
        ) from e

    if settings.recordflow_enabled:
        try:
            await _init_recordflow(app)
        except Exception as e:
            raise StartupError(
                component="RecordFlow",
                reason=str(e),
                hint="Check RecordFlow configuration and flow paths",
            ) from e

    if settings.pipeline_enabled:
        try:
            from clarinet.services.pipeline import get_broker, sync_pipeline_definitions

            broker = get_broker()
            await broker.startup()
            app.state.pipeline_broker = broker
            logger.info("Pipeline broker started")

            count = await sync_pipeline_definitions()
            logger.info(f"Synced {count} pipeline definition(s) to database")
        except Exception as e:
            raise StartupError(
                component="Pipeline",
                reason=str(e),
                hint="Start RabbitMQ or check CLARINET_BROKER_URL",
            ) from e

    if settings.session_cleanup_enabled:
        await session_cleanup_service.start()
        logger.info("Session cleanup service started")

    # Initialize DICOM association semaphore
    from clarinet.services.dicom.operations import DicomOperations

    DicomOperations.set_association_semaphore(settings.dicom_max_concurrent_associations)

    # Start Storage SCP for C-MOVE mode
    if settings.dicom_retrieve_mode in ("c-move", "c-move-study"):
        from clarinet.services.dicom.scp import get_storage_scp

        scp = get_storage_scp()
        scp.start(
            aet=settings.dicom_aet,
            port=settings.dicom_port,
            ip=settings.dicom_ip,
        )
        app.state.storage_scp = scp
        logger.info(
            f"Storage SCP started on port {settings.dicom_port} "
            f"(AET: {settings.dicom_aet}, mode: c-move)"
        )

    # Initialize DICOMweb cache singleton
    if settings.dicomweb_enabled:
        from clarinet.services.dicomweb.cache import DicomWebCache

        cache_dir = Path(settings.storage_path) / "dicomweb_cache"
        app.state.dicomweb_cache = DicomWebCache(
            base_dir=cache_dir,
            ttl_hours=settings.dicomweb_cache_ttl_hours,
            max_size_gb=settings.dicomweb_cache_max_size_gb,
            memory_ttl_minutes=settings.dicomweb_memory_cache_ttl_minutes,
            memory_max_entries=settings.dicomweb_memory_cache_max_entries,
            storage_path=Path(settings.storage_path),
            disk_write_concurrency=settings.dicomweb_disk_write_concurrency,
        )
        logger.info("DICOMweb cache initialized (two-tier: memory + disk)")

        if settings.dicomweb_cache_cleanup_enabled:
            from clarinet.services.dicomweb.cleanup import DicomWebCacheCleanupService

            app.state.dicomweb_cleanup = DicomWebCacheCleanupService(cache=app.state.dicomweb_cache)
            await app.state.dicomweb_cleanup.start()

    logger.info("Application startup complete")

    try:
        yield
    finally:
        # Stop Storage SCP
        if hasattr(app.state, "storage_scp"):
            from clarinet.services.dicom.scp import shutdown_storage_scp

            shutdown_storage_scp()
            logger.info("Storage SCP stopped")

        # Stop DICOMweb cache cleanup service
        if hasattr(app.state, "dicomweb_cleanup"):
            await app.state.dicomweb_cleanup.stop()

        # Shutdown DICOMweb cache (flush pending disk writes)
        if settings.dicomweb_enabled and hasattr(app.state, "dicomweb_cache"):
            await app.state.dicomweb_cache.shutdown()

        if settings.session_cleanup_enabled:
            await session_cleanup_service.stop()
            logger.info("Session cleanup service stopped")

        if settings.pipeline_enabled and getattr(app.state, "pipeline_broker", None):
            await app.state.pipeline_broker.shutdown()
            logger.info("Pipeline broker stopped")

        if settings.recordflow_enabled:
            await _shutdown_recordflow(app)

        shutdown_fs_executor()

        await db_manager.close()
        logger.info("Application shutdown")


def create_app(root_path: str = "") -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        root_path: The root path for the application

    Returns:
        Configured FastAPI application
    """
    # Normalize: strip trailing slashes ("/nir_liver/" → "/nir_liver", "/" → "")
    root_path = root_path.rstrip("/")

    # Import and rebuild models to resolve forward references
    from clarinet.models import RecordRead, SeriesRead, StudyRead

    RecordRead.model_rebuild()
    StudyRead.model_rebuild()
    SeriesRead.model_rebuild()
    app = FastAPI(
        title=settings.project_name,
        description=settings.project_description,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
        root_path=root_path,
        default_response_class=_default_response_class or JSONResponse,
    )

    # Configure CORS
    origins = ["http://localhost", "http://localhost:8080", "*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Compress responses (JS bundles, JSON, HTML) for faster delivery
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Strip literal "null" query-param values so FastAPI treats them as absent
    if settings.coerce_null_query_params:
        from clarinet.api.middleware import NullQueryParamMiddleware

        app.add_middleware(NullQueryParamMiddleware)

    # Mount static files only if frontend is enabled
    # No static files when frontend is disabled

    # Setup exception handlers using decorators
    setup_exception_handlers(app)

    # Include routers with /api prefix for backend endpoints
    app.include_router(info.router, prefix="/api")
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(user.router, prefix="/api/user", tags=["Users"])
    app.include_router(record.router, prefix="/api/records", tags=["Records"])
    app.include_router(study.router, prefix="/api")
    app.include_router(slicer.router, prefix="/api/slicer", tags=["Slicer"])
    app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
    app.include_router(dicom.router, prefix="/api/dicom", tags=["DICOM"])
    app.include_router(pipeline.router, prefix="/api/pipelines", tags=["Pipelines"])
    app.include_router(viewer.router, prefix="/api/records", tags=["Viewers"])
    app.include_router(health.router, prefix="/api", tags=["Health"])

    # Mount DICOMweb proxy router (conditional on settings)
    if settings.dicomweb_enabled:
        app.include_router(dicomweb.router, prefix="/dicom-web", tags=["DICOMweb"])
        logger.info("DICOMweb proxy enabled at /dicom-web")

    # OHIF Viewer directory (checked at request time for SPA routing)
    ohif_dir = settings.ohif_path
    if settings.ohif_enabled:
        logger.info(f"OHIF Viewer enabled at /ohif (serving from {ohif_dir})")

    # Serve frontend if enabled
    if settings.frontend_enabled:
        # Collect all existing static directories (project-level first, built-in last)
        static_dirs: list[Path] = []
        for dir_path in settings.static_directories:
            if dir_path.exists():
                static_dirs.append(dir_path)
                logger.info(f"Static files directory: {dir_path}")
            else:
                logger.debug(f"Static directory {dir_path} does not exist")

        if not static_dirs:
            # Should not happen: _check_frontend() in lifespan catches this.
            logger.error("No static directories found after startup")

        # Cache rendered index.html with $BASE_PATH substituted
        _index_html_cache: dict[str, str] = {}

        def _render_index(index_path: Path) -> str:
            """Read index.html, substitute $BASE_PATH template variable, cache result."""
            key = str(index_path)
            if key not in _index_html_cache:
                tmpl = Template(index_path.read_text(encoding="utf-8"))
                _index_html_cache[key] = tmpl.safe_substitute(BASE_PATH=root_path)
            return _index_html_cache[key]

        # Serve index.html for all non-API routes (SPA support)
        @app.get("/{full_path:path}", response_model=None)
        async def serve_spa(full_path: str) -> FileResponse | HTMLResponse:
            """Serve SPA for all non-API routes."""
            # Skip API and DICOMweb routes
            if full_path.startswith(("api/", "dicom-web/")):
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="Not found")

            # OHIF Viewer SPA routing: serve static files or fall back to index.html
            if full_path.startswith("ohif/"):
                if settings.ohif_enabled and ohif_dir.exists():
                    # Try to serve the exact static file
                    ohif_file = ohif_dir / full_path.removeprefix("ohif/")
                    if ohif_file.exists() and ohif_file.is_file():
                        return FileResponse(ohif_file)
                    # SPA fallback — serve index.html for client-side routing
                    ohif_idx = ohif_dir / "index.html"
                    if ohif_idx.exists():
                        return FileResponse(ohif_idx)
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=404,
                    detail="OHIF Viewer not installed. Run 'clarinet ohif install'.",
                )

            # Check if any static directory exists
            if not static_dirs:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=404,
                    detail="Frontend not built. Run 'make frontend-build' to build the frontend.",
                )

            # Try to serve the requested file (project-level overrides built-in)
            for sd in static_dirs:
                base = sd.resolve()
                candidate = (base / full_path).resolve()
                try:
                    candidate.relative_to(base)
                except ValueError:
                    continue
                if candidate.is_file():
                    return FileResponse(candidate)

            # Serve index.html for all other routes (SPA routing)
            for sd in static_dirs:
                index_path = sd / "index.html"
                if index_path.exists():
                    return HTMLResponse(_render_index(index_path))

            # Fallback error if index.html not found
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="index.html not found in static directory")

    return app


# Create default application instance
app = create_app(root_path=settings.root_url)
