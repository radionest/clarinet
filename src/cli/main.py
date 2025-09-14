#!/usr/bin/env python3
"""Clarinet CLI - Simple management utility for Clarinet framework.

Following KISS and YAGNI principles - minimal, practical implementation.
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

from src.settings import settings
from src.utils.db_manager import db_manager
from src.utils.logger import logger


def init_project(path: str) -> None:
    """Initialize a new Clarinet project in the specified directory."""
    project_path = Path(path).resolve()

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
    settings_content = """# Clarinet Configuration File

# Server settings
port = 8000
host = "127.0.0.1"
debug = true

# Database settings
database_driver = "sqlite"
database_name = "clarinet"

# Storage settings
storage_path = "./data"

# Security (change in production!)
secret_key = "change-this-secret-key-in-production"
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


def run_server(host: str | None = None, port: int | None = None) -> None:
    """Run the Clarinet development server."""
    import uvicorn

    host = host or settings.host or "127.0.0.1"
    port = port or settings.port or 8000

    logger.info(f"Starting Clarinet server at http://{host}:{port}")

    uvicorn.run(
        "src.api.app:app",
        host=host,
        port=port,
        reload=getattr(settings, "debug", True),
        log_level="info" if getattr(settings, "debug", True) else "warning",
    )


async def init_database() -> None:
    """Initialize the database with tables and default data."""
    from src.utils.bootstrap import add_default_user_roles

    logger.info("Initializing database...")
    await db_manager.create_db_and_tables_async()
    await add_default_user_roles()
    logger.info("Database initialized successfully")


def install_frontend() -> None:
    """Install Gleam and frontend dependencies."""
    # Check if Gleam is installed
    try:
        result = subprocess.run(["gleam", "--version"], check=True, capture_output=True, text=True)
        logger.info(f"Gleam already installed: {result.stdout.strip()}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.info("Installing Gleam...")
        try:
            # Install Gleam using official installation script
            subprocess.run(["sh", "-c", "curl -fsSL https://gleam.run/install.sh | sh"], check=True)
            logger.info("Gleam installed successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install Gleam: {e}")
            sys.exit(1)

    # Install frontend dependencies
    frontend_path = Path("src/frontend")
    if frontend_path.exists():
        logger.info("Installing frontend dependencies...")
        try:
            subprocess.run(["gleam", "deps", "download"], cwd=frontend_path, check=True)
            logger.info("Frontend dependencies installed successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install frontend dependencies: {e}")
            sys.exit(1)
    else:
        logger.error(f"Frontend directory not found: {frontend_path}")
        sys.exit(1)


def build_frontend(watch: bool = False) -> None:
    """Build the frontend application."""
    frontend_path = Path("src/frontend")
    if not frontend_path.exists():
        logger.error(f"Frontend directory not found: {frontend_path}")
        sys.exit(1)

    try:
        if watch:
            logger.info("Starting frontend build in watch mode...")
            subprocess.run(
                ["gleam", "build", "--target", "javascript"], cwd=frontend_path, check=True
            )
            # Note: Gleam doesn't have a built-in watch mode, would need external tool
            logger.info(
                "Note: Gleam doesn't have built-in watch mode. Consider using entr or similar."
            )
        else:
            logger.info("Building frontend...")
            subprocess.run(
                ["gleam", "build", "--target", "javascript"], cwd=frontend_path, check=True
            )
            logger.info("Frontend built successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to build frontend: {e}")
        sys.exit(1)


def clean_frontend() -> None:
    """Clean frontend build artifacts."""
    frontend_path = Path("src/frontend")
    build_dir = frontend_path / "build"

    if build_dir.exists():
        logger.info(f"Cleaning build directory: {build_dir}")
        shutil.rmtree(build_dir)
        logger.info("Frontend build artifacts cleaned")
    else:
        logger.info("No build artifacts to clean")


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

    # run command
    run_parser = subparsers.add_parser("run", help="Run the development server")
    run_parser.add_argument(
        "--host", type=str, default=None, help="Host to bind to (default: 127.0.0.1)"
    )
    run_parser.add_argument(
        "--port", type=int, default=None, help="Port to bind to (default: 8000)"
    )

    # db command
    db_parser = subparsers.add_parser("db", help="Database management")
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    db_subparsers.add_parser("init", help="Initialize database with tables")

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

    args = parser.parse_args()

    if args.command == "init":
        init_project(args.path)
    elif args.command == "run":
        run_server(args.host, args.port)
    elif args.command == "db":
        if args.db_command == "init":
            asyncio.run(init_database())
        else:
            db_parser.print_help()
    elif args.command == "init-migrations":
        from src.utils.migrations import init_alembic_in_project

        init_alembic_in_project()
    elif args.command == "frontend":
        handle_frontend_command(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
