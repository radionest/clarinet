#!/usr/bin/env python3
"""Clarinet CLI - Simple management utility for Clarinet framework.

Following KISS and YAGNI principles - minimal, practical implementation.
"""

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

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


def run_server(
    host: str | None = None, port: int | None = None, with_frontend: bool = False
) -> None:
    """Run the Clarinet development server."""
    host = host or settings.host or "127.0.0.1"
    port = port or settings.port or 8000

    if with_frontend:
        # Run both backend and frontend
        logger.info(f"Starting Clarinet server with frontend at http://{host}:{port}")
        asyncio.run(run_with_frontend(host, port))
    else:
        # Run only backend
        import uvicorn

        logger.info(f"Starting Clarinet server at http://{host}:{port}")

        uvicorn.run(
            "src.api.app:app",
            host=host,
            port=port,
            reload=getattr(settings, "debug", True),
            log_level="info" if getattr(settings, "debug", True) else "warning",
        )


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


async def run_with_frontend(host: str, port: int) -> None:
    """Run both backend and frontend servers concurrently."""
    # Enable frontend in settings
    os.environ["CLARINET_FRONTEND_ENABLED"] = "true"

    # Check if frontend is built
    # Frontend is part of the installed clarinet library
    import src

    library_path = Path(src.__file__).parent
    frontend_path = library_path / "frontend"
    build_file = frontend_path / "build" / "dev" / "javascript" / "clarinet.mjs"

    if not build_file.exists():
        logger.info("Frontend not built. Building now...")
        # Ensure Gleam is installed
        if not await _check_command_exists("gleam"):
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

        # Build frontend
        try:
            logger.info("Building frontend...")
            await _run_gleam_command(
                ["gleam", "build", "--target", "javascript"], cwd=frontend_path
            )
            logger.info("Frontend built successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to build frontend: {e}")
            sys.exit(1)

    # Create tasks for both servers
    backend_task = None
    frontend_task = None

    async def run_backend() -> None:
        """Run the backend server using uvicorn programmatically."""
        import uvicorn

        logger.info(f"Starting backend server at http://{host}:{port}")

        config = uvicorn.Config(
            "src.api.app:app",
            host=host,
            port=port,
            reload=getattr(settings, "debug", True),
            log_level="info" if getattr(settings, "debug", True) else "warning",
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def run_frontend_watch() -> None:
        """Run the frontend build in watch mode."""
        logger.info("Starting frontend watch mode...")

        # Check if entr is available for watching
        if await _check_command_exists("entr"):
            # Use entr for watching
            watch_cmd = [
                "sh",
                "-c",
                "find src -name '*.gleam' | entr -c gleam build --target javascript",
            ]
            # Run watch command with entr
            process = await asyncio.create_subprocess_exec(
                *watch_cmd,
                cwd=frontend_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async def log_output(stream: asyncio.StreamReader, prefix: str) -> None:
                async for line in stream:
                    decoded = line.decode().strip()
                    if decoded:
                        logger.info(f"{prefix}: {decoded}")

            if process.stdout and process.stderr:
                await asyncio.gather(
                    log_output(process.stdout, "Frontend"),
                    log_output(process.stderr, "Frontend"),
                    process.wait(),
                )
        else:
            # Fallback to periodic rebuild
            logger.info("'entr' not found. Using periodic rebuild (every 2 seconds)...")
            while True:
                try:
                    # Check for changes and rebuild
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

    # Signal handler for graceful shutdown
    def signal_handler(_signum: int, _frame: Any) -> None:
        logger.info("\nShutting down servers...")
        if backend_task:
            backend_task.cancel()
        if frontend_task:
            frontend_task.cancel()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info(f"Starting Clarinet with frontend at http://{host}:{port}")
    logger.info("Press Ctrl+C to stop both servers")

    try:
        # Run both servers concurrently
        backend_task = asyncio.create_task(run_backend())
        frontend_task = asyncio.create_task(run_frontend_watch())

        await asyncio.gather(backend_task, frontend_task)
    except asyncio.CancelledError:
        logger.info("Servers stopped")
    except Exception as e:
        logger.error(f"Error running servers: {e}")
        sys.exit(1)


async def init_database() -> None:
    """Initialize the database with tables and default data."""
    from src.utils.bootstrap import initialize_application_data

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
    import src

    library_path = Path(src.__file__).parent
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
    import src

    library_path = Path(src.__file__).parent
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
    import src

    library_path = Path(src.__file__).parent
    frontend_path = library_path / "frontend"
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
    run_parser.add_argument(
        "--with-frontend", action="store_true", help="Also start the frontend development server"
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

    args = parser.parse_args()

    if args.command == "init":
        init_project(args.path)
    elif args.command == "run":
        run_server(args.host, args.port, args.with_frontend)
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
    elif args.command == "frontend":
        handle_frontend_command(args)
    elif args.command == "session":
        from src.cli.session_management import (
            cleanup,
            cleanup_all,
            cleanup_once,
            list_user_sessions,
            revoke_user_sessions_cmd,
            stats,
        )

        if args.session_command == "cleanup":
            cleanup(args.days)
        elif args.session_command == "cleanup-once":
            cleanup_once()
        elif args.session_command == "stats":
            stats()
        elif args.session_command == "revoke-user":
            revoke_user_sessions_cmd(args.user_id, args.keep_current)
        elif args.session_command == "list-user":
            list_user_sessions(args.user_id)
        elif args.session_command == "cleanup-all":
            cleanup_all()
        else:
            session_parser.print_help()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
