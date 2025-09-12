"""Database migration utilities using Alembic."""

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import Script, ScriptDirectory
from src.exceptions import MigrationError
from src.settings import settings
from src.utils.logger import logger


def get_alembic_config() -> Config:
    """Get Alembic configuration.

    Returns:
        Alembic Config object configured with project settings.
    """
    # Get the project root directory
    project_root = Path(__file__).parent.parent.parent
    alembic_ini = project_root / "alembic.ini"

    if not alembic_ini.exists():
        raise FileNotFoundError(f"Alembic configuration not found at {alembic_ini}")

    config = Config(str(alembic_ini))
    # Set the script location to absolute path
    config.set_main_option("script_location", str(project_root / "alembic"))
    # Set the database URL from settings
    config.set_main_option("sqlalchemy.url", settings.database_url)

    return config


def create_migration(
    message: str, autogenerate: bool = True
) -> Script | list[Script | None] | None:
    """Create a new migration.

    Args:
        message: Description of the migration
        autogenerate: Whether to auto-generate migration from model changes

    Returns:
        Path to the created migration file
    """
    config = get_alembic_config()

    logger.info(f"Creating migration: {message}")

    if autogenerate:
        # Auto-generate migration from model changes
        revision = command.revision(config, message=message, autogenerate=True)
    else:
        # Create empty migration
        revision = command.revision(config, message=message)

    logger.info(f"Created migration: {revision}")
    return revision


def run_migrations(target: str = "head") -> None:
    """Apply database migrations.

    Args:
        target: Migration target (e.g., "head", "+1", "-1", specific revision)
    """
    config = get_alembic_config()

    logger.info(f"Running migrations to: {target}")

    if target == "head":
        command.upgrade(config, target)
    elif target.startswith("-"):
        command.downgrade(config, target)
    else:
        command.upgrade(config, target)

    logger.info("Migrations completed successfully")


def get_current_revision() -> str | None:
    """Get the current database migration revision.

    Returns:
        Current revision ID or None if no migrations applied
    """
    get_alembic_config()

    # Create engine to check current revision
    engine = create_engine(settings.database_url)

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current_rev = context.get_current_revision()

    engine.dispose()

    return current_rev


def get_pending_migrations() -> list[str]:
    """Get list of pending migrations.

    Returns:
        List of pending migration revision IDs
    """
    config = get_alembic_config()
    script_dir = ScriptDirectory.from_config(config)

    current = get_current_revision()
    head = script_dir.get_current_head()

    if current == head:
        return []

    # Get all revisions between current and head
    pending = []
    if not (current and head):
        raise MigrationError
    for revision in script_dir.walk_revisions(head, current):
        if revision.revision != current:
            pending.append(revision.revision)

    return pending


def get_migration_history() -> list[tuple[str, str | set[str], str]]:
    """Get migration history.

    Returns:
        List of tuples (revision_id, branch_labels, message)
    """
    config = get_alembic_config()
    script_dir = ScriptDirectory.from_config(config)

    history = []
    for revision in script_dir.walk_revisions():
        history.append((revision.revision, revision.branch_labels or "", revision.doc or ""))

    return history


async def run_migrations_async(target: str = "head") -> None:
    """Apply database migrations asynchronously.

    Args:
        target: Migration target (e.g., "head", "+1", "-1", specific revision)
    """
    # Run alembic in subprocess to avoid blocking
    cmd = ["alembic", "upgrade" if not target.startswith("-") else "downgrade", target]

    logger.info(f"Running async migrations to: {target}")

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode() if stderr else "Unknown error"
        logger.error(f"Migration failed: {error_msg}")
        raise RuntimeError(f"Migration failed: {error_msg}")

    logger.info("Async migrations completed successfully")


async def check_database_initialized() -> bool:
    """Check if database has been initialized with migrations.

    Returns:
        True if alembic_version table exists, False otherwise
    """
    # Convert URL for async if needed
    db_url = settings.database_url
    if db_url.startswith("sqlite:"):
        db_url = db_url.replace("sqlite:", "sqlite+aiosqlite:", 1)
    elif db_url.startswith("postgresql:"):
        db_url = db_url.replace("postgresql:", "postgresql+asyncpg:", 1)

    engine = create_async_engine(db_url)

    try:
        async with engine.connect() as conn:
            # Check if alembic_version table exists
            if db_url.startswith("sqlite"):
                result = await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                    )
                )
            else:
                result = await conn.execute(text("SELECT to_regclass('alembic_version')"))

            return result.scalar() is not None
    finally:
        await engine.dispose()


async def initialize_database() -> None:
    """Initialize database with migrations if not already done."""
    if not await check_database_initialized():
        logger.info("Database not initialized, running migrations...")
        await run_migrations_async("head")
        logger.info("Database initialized successfully")
    else:
        # Check for pending migrations
        pending = get_pending_migrations()
        if pending:
            logger.info(f"Found {len(pending)} pending migrations")
            await run_migrations_async("head")
        else:
            logger.info("Database is up to date")


def rollback_migration(steps: int = 1) -> None:
    """Rollback migrations by specified number of steps.

    Args:
        steps: Number of migrations to rollback
    """
    config = get_alembic_config()
    target = f"-{steps}"

    logger.info(f"Rolling back {steps} migration(s)")
    command.downgrade(config, target)
    logger.info("Rollback completed successfully")


def show_migration_sql(target: str = "head", offline: bool = False) -> str:
    """Generate SQL for migrations without applying them.

    Args:
        target: Migration target
        offline: Whether to generate offline SQL

    Returns:
        SQL statements as string
    """
    config = get_alembic_config()

    if offline:
        # Generate offline SQL
        command.upgrade(config, target, sql=True)
    else:
        # Use online mode to generate SQL
        command.upgrade(config, target, sql=True)

    # Note: The SQL is printed to stdout by Alembic
    return "SQL generated and printed to console"


# CLI helper functions for common operations
def cli_init() -> None:
    """Initialize Alembic for the project (already done)."""
    logger.info("Alembic is already initialized for this project")


def cli_create(message: str) -> None:
    """Create a new migration from CLI."""
    try:
        migration_file = create_migration(message, autogenerate=True)
        logger.info(f"Created migration: {migration_file}")
    except Exception as e:
        logger.error(f"Failed to create migration: {e}")
        raise


def cli_upgrade(target: str = "head") -> None:
    """Apply migrations from CLI."""
    try:
        run_migrations(target)
    except Exception as e:
        logger.error(f"Failed to apply migrations: {e}")
        raise


def cli_downgrade(steps: int = 1) -> None:
    """Rollback migrations from CLI."""
    try:
        rollback_migration(steps)
    except Exception as e:
        logger.error(f"Failed to rollback migrations: {e}")
        raise


def cli_current() -> None:
    """Show current migration revision."""
    current = get_current_revision()
    if current:
        logger.info(f"Current revision: {current}")
    else:
        logger.info("No migrations applied yet")


def cli_history() -> None:
    """Show migration history."""
    history = get_migration_history()
    current = get_current_revision()

    for rev_id, _labels, message in history:
        marker = " (current)" if rev_id == current else ""
        logger.info(f"{rev_id}{marker}: {message}")


def cli_pending() -> None:
    """Show pending migrations."""
    pending = get_pending_migrations()
    if pending:
        logger.info(f"Pending migrations: {', '.join(pending)}")
    else:
        logger.info("No pending migrations")
