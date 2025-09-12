import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from alembic import context

# Import all models to ensure they're registered with SQLModel
from src.models.base import BaseModel, DicomQueryLevel, TaskStatus  # noqa: F401
from src.models.patient import Patient  # noqa: F401
from src.models.study import Series, Study  # noqa: F401
from src.models.task import Task, TaskDesign  # noqa: F401
from src.models.user import HTTPSession, User, UserRole, UserRolesLink  # noqa: F401

# Import settings and models
from src.settings import settings
from src.utils.logger import logger

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the database URL from settings
config.set_main_option("sqlalchemy.url", settings.database_url)

# Add your model's MetaData object here
# for 'autogenerate' support
target_metadata = SQLModel.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations using the provided connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context."""

    # Handle SQLite URL conversion for async
    db_url = settings.database_url
    if db_url.startswith("sqlite:"):
        # Convert sqlite:// to sqlite+aiosqlite://
        db_url = db_url.replace("sqlite:", "sqlite+aiosqlite:", 1)
    elif db_url.startswith("postgresql:"):
        # Convert postgresql:// to postgresql+asyncpg://
        db_url = db_url.replace("postgresql:", "postgresql+asyncpg:", 1)

    logger.info(
        f"Running migrations with database: {db_url.split('@')[-1] if '@' in db_url else db_url}"
    )

    connectable = create_async_engine(
        db_url,
        poolclass=pool.NullPool,
        echo=False,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    logger.info("Running migrations in offline mode")
    run_migrations_offline()
else:
    logger.info("Running migrations in online mode")
    run_migrations_online()
