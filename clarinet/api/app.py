"""
Main API application module for Clarinet.

This module creates and configures the FastAPI application with all routers,
middleware, and static files.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

try:
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


async def _init_recordflow(app: FastAPI) -> None:
    """Initialize RecordFlow engine and attach to app state."""
    from clarinet.client import ClarinetClient
    from clarinet.services.recordflow import RecordFlowEngine, discover_and_load_flows

    client = ClarinetClient(
        base_url=settings.api_base_url,
        username=settings.admin_email,
        password=settings.admin_password,
        auto_login=False,
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

    Creates database tables, adds default roles, and loads record types.
    """
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

    if settings.recordflow_enabled:
        try:
            await _init_recordflow(app)
        except Exception as e:
            logger.error(f"Failed to initialize RecordFlow engine: {e}")
            app.state.recordflow_engine = None

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
            logger.error(f"Failed to start pipeline broker: {e}")
            app.state.pipeline_broker = None

    if settings.session_cleanup_enabled:
        await session_cleanup_service.start()
        logger.info("Session cleanup service started")

    # Initialize DICOM association semaphore
    from clarinet.services.dicom.operations import DicomOperations

    DicomOperations.set_association_semaphore(settings.dicom_max_concurrent_associations)

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


def create_app(root_path: str = "/") -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        root_path: The root path for the application

    Returns:
        Configured FastAPI application
    """
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
    app.include_router(health.router, prefix="/api", tags=["Health"])

    # Mount DICOMweb proxy router (conditional on settings)
    if settings.dicomweb_enabled:
        app.include_router(dicomweb.router, prefix="/dicom-web", tags=["DICOMweb"])
        logger.info("DICOMweb proxy enabled at /dicom-web")

    # OHIF Viewer directory (checked at request time for SPA routing)
    ohif_dir = settings.ohif_path
    if settings.ohif_enabled:
        ohif_index = ohif_dir / "index.html"
        if ohif_index.exists():
            logger.info(f"OHIF Viewer enabled at /ohif (serving from {ohif_dir})")
        else:
            logger.warning(
                "OHIF enabled but index.html not found. "
                "Run 'clarinet ohif install' to download OHIF Viewer."
            )

    # Serve frontend if enabled
    if settings.frontend_enabled:
        # Find first existing static directory
        static_dir = None
        for dir_path in settings.static_directories:
            if dir_path.exists():
                static_dir = dir_path
                logger.info(f"Using static files from {static_dir}")
                break
            else:
                logger.debug(f"Static directory {dir_path} does not exist")

        if not static_dir:
            logger.warning(
                "No static directories found. Run 'make frontend-build' to build the frontend."
            )

        # Serve index.html for all non-API routes (SPA support)
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> FileResponse:
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

            # Check if static directory exists
            if not static_dir:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=404,
                    detail="Frontend not built. Run 'make frontend-build' to build the frontend.",
                )

            # Try to serve the requested file first
            requested_file = static_dir / full_path
            if requested_file.exists() and requested_file.is_file():
                return FileResponse(requested_file)

            # Serve index.html for all other routes (SPA routing)
            index_path = static_dir / "index.html"
            if index_path.exists():
                return FileResponse(index_path)

            # Fallback error if index.html not found
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="index.html not found in static directory")

    return app


# Create default application instance
app = create_app(root_path=settings.root_url)
