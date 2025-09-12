"""Database migration utilities using Alembic."""

import asyncio
from pathlib import Path
from textwrap import dedent

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import Script, ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.exceptions import MigrationError
from src.settings import settings
from src.utils.logger import logger


def generate_alembic_ini(project_path: Path | None = None) -> str:
    """Generate alembic.ini content from settings.

    Args:
        project_path: Path to the project directory

    Returns:
        Content for alembic.ini file
    """
    if project_path is None:
        project_path = Path.cwd()

    # Get database URL from settings
    db_url = settings.database_url

    # Generate alembic.ini content
    content = dedent(f"""
    # A generic, single database configuration.

    [alembic]
    # path to migration scripts
    script_location = alembic

    # template used to generate migration file names; The default value is %%(rev)s_%%(slug)s
    # Uncomment the line below if you want the files to be prepended with date and time
    # file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s

    # sys.path path, will be prepended to sys.path if present.
    # defaults to the current working directory.
    prepend_sys_path = .

    # timezone to use when rendering the date within the migration file
    # as well as the filename.
    # If specified, requires the python>=3.9 or backports.zoneinfo library.
    # Any required deps can installed by adding `alembic[tz]` to the pip requirements
    # string value is passed to ZoneInfo()
    # leave blank for localtime
    # timezone =

    # max length of characters to apply to the "slug" field
    # truncate_slug_length = 40

    # set to 'true' to run the environment during
    # the 'revision' command, regardless of autogenerate
    # revision_environment = false

    # set to 'true' to allow .pyc and .pyo files without
    # a source .py file to be detected as revisions in the
    # versions/ directory
    # sourceless = false

    # version location specification; This defaults
    # to alembic/versions.  When using multiple version
    # directories, initial revisions must be specified with --version-path.
    # The path separator used here should be the separator specified by "version_path_separator" below.
    # version_locations = %%(here)s/bar:%%(here)s/bat:alembic/versions

    # version path separator; As mentioned above, this is the character used to split
    # version_locations. The default separator is OS dependent, but a forward slash is
    # recommended. Valid values for version_path_separator are:
    #
    # version_path_separator = :
    # version_path_separator = ;
    # version_path_separator = space
    version_path_separator = os  # Use os.pathsep.
    # Default and recommended is 'os', which uses os.pathsep.
    # If this key is omitted entirely, it falls back to the legacy behavior of
    # splitting on ':' on Windows and ';' on all other platforms.

    # set to 'true' to search source files recursively
    # in each "version_locations" directory
    # new in Alembic version 1.10
    # recursive_version_locations = false

    # the output encoding used when revision files
    # are written from script.py.mako
    # output_encoding = utf-8

    sqlalchemy.url = {db_url}


    [post_write_hooks]
    # post_write_hooks defines scripts or Python functions that are run
    # on newly generated revision scripts.  See the documentation for further
    # detail and examples

    # format using "black" - use the console_scripts runner, against the "black" entrypoint
    # hooks = black
    # black.type = console_scripts
    # black.entrypoint = black
    # black.options = -l 79 REVISION_SCRIPT_FILENAME

    # lint with attempts to fix using "ruff" - use the exec runner, execute a binary
    # hooks = ruff
    # ruff.type = exec
    # ruff.executable = %(here)s/.venv/bin/ruff
    # ruff.options = --fix REVISION_SCRIPT_FILENAME

    # Logging configuration
    [loggers]
    keys = root,sqlalchemy,alembic

    [handlers]
    keys = console

    [formatters]
    keys = generic

    [logger_root]
    level = WARN
    handlers = console
    qualname =

    [logger_sqlalchemy]
    level = WARN
    handlers =
    qualname = sqlalchemy.engine

    [logger_alembic]
    level = INFO
    handlers =
    qualname = alembic

    [handler_console]
    class = StreamHandler
    args = (sys.stderr,)
    level = NOTSET
    formatter = generic

    [formatter_generic]
    format = %(levelname)-5.5s [%(name)s] %(message)s
    datefmt = %H:%M:%S
    """)

    return content.strip()


def generate_alembic_env(
    project_path: Path | None = None,  # noqa: ARG001
) -> str:
    """Generate env.py content for Alembic.

    Args:
        project_path: Path to the project directory

    Returns:
        Content for env.py file
    """
    content = dedent("""
    import sys
    from logging.config import fileConfig
    from pathlib import Path

    from sqlalchemy import engine_from_config
    from sqlalchemy import pool

    from alembic import context

    # Add project root to path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    # Import all models from the framework
    from src.models import *  # noqa: F403, F401
    from src.models.base import Base

    # this is the Alembic Config object, which provides
    # access to the values within the .ini file in use.
    config = context.config

    # Interpret the config file for Python logging.
    # This line sets up loggers basically.
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)

    # add your model's MetaData object here
    # for 'autogenerate' support
    target_metadata = Base.metadata

    # other values from the config, defined by the needs of env.py,
    # can be acquired:
    # my_important_option = config.get_main_option("my_important_option")
    # ... etc.


    def run_migrations_offline() -> None:
        \"\"\"Run migrations in 'offline' mode.

        This configures the context with just a URL
        and not an Engine, though an Engine is acceptable
        here as well.  By skipping the Engine creation
        we don't even need a DBAPI to be available.

        Calls to context.execute() here emit the given string to the
        script output.

        \"\"\"
        url = config.get_main_option("sqlalchemy.url")
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )

        with context.begin_transaction():
            context.run_migrations()


    def run_migrations_online() -> None:
        \"\"\"Run migrations in 'online' mode.

        In this scenario we need to create an Engine
        and associate a connection with the context.

        \"\"\"
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

        with connectable.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata
            )

            with context.begin_transaction():
                context.run_migrations()


    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
    """)

    return content.strip()


def get_alembic_config(project_path: Path | None = None) -> Config:
    """Get Alembic configuration.

    Args:
        project_path: Path to the project directory. If None, uses current directory.

    Returns:
        Alembic Config object configured with project settings.
    """
    if project_path is None:
        project_path = Path.cwd()

    alembic_ini = project_path / "alembic.ini"

    if not alembic_ini.exists():
        raise FileNotFoundError(
            f"Alembic configuration not found at {alembic_ini}. "
            f"Run 'clarinet init-migrations' to initialize Alembic for this project."
        )

    config = Config(str(alembic_ini))
    # Set the script location to absolute path
    config.set_main_option("script_location", str(project_path / "alembic"))
    # Set the database URL from settings
    config.set_main_option("sqlalchemy.url", settings.database_url)

    return config


def create_migration(
    message: str, autogenerate: bool = True, project_path: Path | None = None
) -> Script | list[Script | None] | None:
    """Create a new migration.

    Args:
        message: Description of the migration
        autogenerate: Whether to auto-generate migration from model changes
        project_path: Path to the project directory

    Returns:
        Path to the created migration file
    """
    config = get_alembic_config(project_path)

    logger.info(f"Creating migration: {message}")

    if autogenerate:
        # Auto-generate migration from model changes
        revision = command.revision(config, message=message, autogenerate=True)
    else:
        # Create empty migration
        revision = command.revision(config, message=message)

    logger.info(f"Created migration: {revision}")
    return revision


def run_migrations(target: str = "head", project_path: Path | None = None) -> None:
    """Apply database migrations.

    Args:
        target: Migration target (e.g., "head", "+1", "-1", specific revision)
        project_path: Path to the project directory
    """
    config = get_alembic_config(project_path)

    logger.info(f"Running migrations to: {target}")

    if target == "head":
        command.upgrade(config, target)
    elif target.startswith("-"):
        command.downgrade(config, target)
    else:
        command.upgrade(config, target)

    logger.info("Migrations completed successfully")


def get_current_revision(project_path: Path | None = None) -> str | None:
    """Get the current database migration revision.

    Args:
        project_path: Path to the project directory

    Returns:
        Current revision ID or None if no migrations applied
    """
    get_alembic_config(project_path)

    # Create engine to check current revision
    engine = create_engine(settings.database_url)

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current_rev = context.get_current_revision()

    engine.dispose()

    return current_rev


def get_pending_migrations(project_path: Path | None = None) -> list[str]:
    """Get list of pending migrations.

    Args:
        project_path: Path to the project directory

    Returns:
        List of pending migration revision IDs
    """
    config = get_alembic_config(project_path)
    script_dir = ScriptDirectory.from_config(config)

    current = get_current_revision(project_path)
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


def get_migration_history(
    project_path: Path | None = None,
) -> list[tuple[str, str | set[str], str]]:
    """Get migration history.

    Args:
        project_path: Path to the project directory

    Returns:
        List of tuples (revision_id, branch_labels, message)
    """
    config = get_alembic_config(project_path)
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
        try:
            pending = get_pending_migrations()
            if pending:
                logger.info(f"Found {len(pending)} pending migrations")
                await run_migrations_async("head")
            else:
                logger.info("Database is up to date")
        except FileNotFoundError:
            logger.warning("Alembic not initialized in project, skipping migration check")


def rollback_migration(steps: int = 1, project_path: Path | None = None) -> None:
    """Rollback migrations by specified number of steps.

    Args:
        steps: Number of migrations to rollback
        project_path: Path to the project directory
    """
    config = get_alembic_config(project_path)
    target = f"-{steps}"

    logger.info(f"Rolling back {steps} migration(s)")
    command.downgrade(config, target)
    logger.info("Rollback completed successfully")


def show_migration_sql(
    target: str = "head", offline: bool = False, project_path: Path | None = None
) -> str:
    """Generate SQL for migrations without applying them.

    Args:
        target: Migration target
        offline: Whether to generate offline SQL
        project_path: Path to the project directory

    Returns:
        SQL statements as string
    """
    config = get_alembic_config(project_path)

    if offline:
        # Generate offline SQL
        command.upgrade(config, target, sql=True)
    else:
        # Use online mode to generate SQL
        command.upgrade(config, target, sql=True)

    # Note: The SQL is printed to stdout by Alembic
    return "SQL generated and printed to console"


def init_alembic_in_project(project_path: Path | None = None) -> None:
    """Initialize Alembic in a project.

    Args:
        project_path: Path to the project directory. If None, uses current directory.
    """
    if project_path is None:
        project_path = Path.cwd()

    # Create alembic directory
    alembic_dir = project_path / "alembic"
    alembic_dir.mkdir(exist_ok=True)

    # Create versions directory
    versions_dir = alembic_dir / "versions"
    versions_dir.mkdir(exist_ok=True)

    # Generate and write alembic.ini
    alembic_ini_path = project_path / "alembic.ini"
    if not alembic_ini_path.exists():
        logger.info(f"Creating alembic.ini in {project_path}")
        alembic_ini_content = generate_alembic_ini(project_path)
        alembic_ini_path.write_text(alembic_ini_content)
    else:
        logger.warning(f"alembic.ini already exists in {project_path}")

    # Generate and write env.py
    env_py_path = alembic_dir / "env.py"
    if not env_py_path.exists():
        logger.info(f"Creating env.py in {alembic_dir}")
        env_py_content = generate_alembic_env(project_path)
        env_py_path.write_text(env_py_content)
    else:
        logger.warning(f"env.py already exists in {alembic_dir}")

    # Create script.py.mako
    script_mako_path = alembic_dir / "script.py.mako"
    if not script_mako_path.exists():
        logger.info(f"Creating script.py.mako in {alembic_dir}")
        script_mako_content = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''
        script_mako_path.write_text(script_mako_content.strip())
    else:
        logger.warning(f"script.py.mako already exists in {alembic_dir}")

    logger.info(f"Alembic initialized successfully in {project_path}")
    logger.info(
        'You can now run "alembic revision --autogenerate -m initial" to create your first migration'
    )


# CLI helper functions for common operations
def cli_init() -> None:
    """Initialize Alembic for the current project."""
    init_alembic_in_project()


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
    try:
        current = get_current_revision()
    except FileNotFoundError:
        logger.error("Alembic not initialized. Run 'clarinet init-migrations' first.")
        return
    current = get_current_revision()
    if current:
        logger.info(f"Current revision: {current}")
    else:
        logger.info("No migrations applied yet")


def cli_history() -> None:
    """Show migration history."""
    try:
        history = get_migration_history()
        current = get_current_revision()
    except FileNotFoundError:
        logger.error("Alembic not initialized. Run 'clarinet init-migrations' first.")
        return

    for rev_id, _labels, message in history:
        marker = " (current)" if rev_id == current else ""
        logger.info(f"{rev_id}{marker}: {message}")


def cli_pending() -> None:
    """Show pending migrations."""
    try:
        pending = get_pending_migrations()
    except FileNotFoundError:
        logger.error("Alembic not initialized. Run 'clarinet init-migrations' first.")
        return
    if pending:
        logger.info(f"Pending migrations: {', '.join(pending)}")
    else:
        logger.info("No pending migrations")
