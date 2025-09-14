"""
Main API application module for Clarinet.

This module creates and configures the FastAPI application with all routers,
middleware, and static files.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

    # Include routers
    app.include_router(auth.router)
    app.include_router(user.router, prefix="/user", tags=["Users"])
    app.include_router(task.router, prefix="/task", tags=["Tasks"])
    app.include_router(study.router, prefix="/study", tags=["Studies"])
    app.include_router(slicer.router, prefix="/slicer", tags=["Slicer"])

    return app


# Create default application instance
app = create_app(root_path=settings.root_url)
