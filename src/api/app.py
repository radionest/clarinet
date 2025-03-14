"""
Main API application module for Clarinet.

This module creates and configures the FastAPI application with all routers,
middleware, and static files.
"""

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncGenerator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Use relative imports for development
from src.settings import settings
from src.utils.database import create_db_and_tables
from src.utils.bootstrap import (
    create_demo_task_types_from_json,
    add_default_user_roles,
)
from src.utils.logger import logger
from src.api.routers import (
    auth,
    render,
    slicer,
    study,
    task,
    user,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.
   
    Creates database tables, adds default roles, and loads task types.
    """
    # Initialize database
    create_db_and_tables()
   
    # Setup default configuration
    add_default_user_roles()
    create_demo_task_types_from_json("./tasks/", demo_suffix="")
   
    logger.info("Application startup complete")
    yield
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
    
    # Include routers
    app.include_router(auth.router)
    app.include_router(user.router, prefix="/user", tags=["Users"])
    app.include_router(task.router, prefix="/task", tags=["Tasks"])
    app.include_router(study.router, prefix="/study", tags=["Studies"])
    app.include_router(slicer.router, prefix="/slicer", tags=["Slicer"])
    
    # Mount rendering routes
    render_app = FastAPI(debug=settings.debug)
    render_app.include_router(render.router)
    app.mount("/render", render_app)
    
    return app


# Create default application instance
app = create_app(root_path=settings.root_url)