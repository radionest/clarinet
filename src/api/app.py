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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.exception_handlers import setup_exception_handlers

# Use relative imports for development
from src.api.routers import auth as auth
from src.api.routers import slicer  # slicer doesn't use database, no async version needed,
from src.api.routers import study as study
from src.api.routers import task as task
from src.api.routers import user as user
from src.settings import settings
from src.utils.bootstrap import (
    add_default_user_roles,
    create_demo_task_designs_from_json,
)
from src.utils.db_manager import db_manager
from src.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """
    Application lifespan context manager.

    Creates database tables, adds default roles, and loads task types.
    """
    # Initialize database using DatabaseManager

    await db_manager.create_db_and_tables_async()
    logger.info("Database initialized with async support")

    # Setup default configuration
    await add_default_user_roles()
    await create_demo_task_designs_from_json("./tasks/", demo_suffix="")

    logger.info("Application startup complete")

    try:
        yield
    finally:
        # Cleanup database connections on shutdown
        await db_manager.close()
        logger.info("Application shutdown")


# noinspection PyTypeChecker
def create_app(root_path: str = "/") -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        root_path: The root path for the application

    Returns:
        Configured FastAPI application
    """
    # Import and rebuild models to resolve forward references
    from src.models import SeriesRead, StudyRead, TaskRead

    TaskRead.model_rebuild()
    StudyRead.model_rebuild()
    SeriesRead.model_rebuild()
    app = FastAPI(
        title="Clarinet",
        description="A Framework for Medical Image Analysis and Annotation",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
        root_path=root_path,
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

    # Mount static files
    static_dir = settings.get_static_dir()
    app.mount(
        "/static",
        StaticFiles(directory=static_dir),
        name="static",
    )

    # Setup exception handlers using decorators
    setup_exception_handlers(app)

    # Include routers with /api prefix for backend endpoints
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(user.router, prefix="/api/user", tags=["Users"])
    app.include_router(task.router, prefix="/api/task", tags=["Tasks"])
    app.include_router(study.router, prefix="/api/study", tags=["Studies"])
    app.include_router(slicer.router, prefix="/api/slicer", tags=["Slicer"])

    # Serve frontend if enabled
    if settings.frontend_enabled:
        frontend_path = Path(__file__).parent.parent / "frontend"
        frontend_build = frontend_path / "build" / "dev" / "javascript"
        frontend_static = frontend_path / "static"

        # Mount JavaScript build files
        if frontend_build.exists():
            app.mount("/js", StaticFiles(directory=str(frontend_build)), name="frontend_js")
            logger.info(f"Mounted frontend JavaScript from {frontend_build}")

        # Mount frontend static files
        if frontend_static.exists():
            # Override /static mount for frontend
            app.mount(
                "/static", StaticFiles(directory=str(frontend_static)), name="frontend_static"
            )
            logger.info(f"Mounted frontend static files from {frontend_static}")

        # Mount user custom static files with higher priority
        if (
            settings.project_path
            and settings.project_static_path
            and settings.project_static_path.exists()
        ):
            app.mount(
                "/static/custom",
                StaticFiles(directory=str(settings.project_static_path)),
                name="custom_static",
            )
            logger.info(f"Mounted custom static files from {settings.project_static_path}")

        # SPA fallback - all non-API routes serve index.html
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> FileResponse:
            """Serve the SPA for all non-API routes."""
            # Skip API and static routes
            if (
                full_path.startswith("api/")
                or full_path.startswith("static/")
                or full_path.startswith("js/")
            ):
                return FileResponse(status_code=404, path=str(frontend_static / "404.html"))

            index_file = frontend_static / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))

            return FileResponse(status_code=404, path=str(frontend_static / "404.html"))

    return app


# Create default application instance
app = create_app(root_path=settings.root_url)
