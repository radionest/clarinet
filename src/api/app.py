"""
Main API application module for Clarinet.

This module creates and configures the FastAPI application with all routers,
middleware, and static files.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.api.exception_handlers import setup_exception_handlers

# Use relative imports for development
from src.api.routers import auth as auth
from src.api.routers import slicer  # slicer doesn't use database, no async version needed,
from src.api.routers import study as study
from src.api.routers import task as task
from src.api.routers import user as user
from src.settings import settings
from src.utils.admin import ensure_admin_exists
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

    # Ensure admin exists
    try:
        await ensure_admin_exists()
    except RuntimeError as e:
        logger.critical(f"Startup failed: {e}")
        # In production, you might want to exit
        if not settings.debug:
            raise

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

    # Mount static files only if frontend is enabled
    # No static files when frontend is disabled

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
            # Skip API routes
            if full_path.startswith("api/"):
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="API endpoint not found")

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
