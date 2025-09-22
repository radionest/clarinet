#!/usr/bin/env python3
"""Clarinet CLI - Simple management utility for Clarinet framework.

Following KISS and YAGNI principles - minimal, practical implementation.
"""

import argparse
import asyncio
import json
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
    from src.utils.bootstrap import initialize_application_data

    logger.info("Initializing database...")
    await db_manager.create_db_and_tables_async()
    await initialize_application_data()  # Changed from add_default_user_roles
    logger.info("Database initialized successfully")


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
    elif args.command == "admin":
        if args.admin_command == "create":
            import getpass

            from src.utils.bootstrap import create_admin_user

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

            from src.utils.admin import reset_admin_password

            password = getpass.getpass("Enter new password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                logger.error("Passwords do not match")
                sys.exit(1)

            asyncio.run(reset_admin_password(args.username, password))
        else:
            admin_parser.print_help()
    elif args.command == "init-migrations":
        from src.utils.migrations import init_alembic_in_project

        init_alembic_in_project()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
