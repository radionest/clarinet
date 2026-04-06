"""
Database manager for the Clarinet framework.

This module provides a centralized database connection manager
that avoids global state and supports both sync and async operations.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from ..settings import DatabaseDriver, settings
from ..utils.logger import logger


def _pydantic_json_serializer(obj: Any) -> str:
    """JSON serializer that handles Pydantic/SQLModel objects in JSON columns."""

    def default(o: Any) -> Any:
        if hasattr(o, "model_dump"):
            return o.model_dump()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    return json.dumps(obj, default=default)


def get_async_database_url() -> str:
    """Return the configured database URL using an async-capable driver.

    Centralizes sync→async driver conversion so callers never have to parse
    URL strings themselves (historically a source of bugs when the URL carries
    a ``+driver`` suffix).

    Raises:
        ValueError: if the configured driver has no async counterpart.
    """
    match settings.database_driver:
        case DatabaseDriver.SQLITE:
            return settings.database_url.replace("sqlite:", "sqlite+aiosqlite:", 1)
        case DatabaseDriver.POSTGRESQL:
            return settings.database_url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
        case DatabaseDriver.POSTGRESQL_ASYNC:
            return settings.database_url
        case _:
            raise ValueError(f"Async not supported for {settings.database_driver}")


class DatabaseManager:
    """
    Manages database connections and sessions without global state.

    This class provides lazy initialization and centralized management
    of both synchronous and asynchronous database connections.
    """

    def __init__(self) -> None:
        """Initialize the database manager with empty connections."""
        self._async_engine: AsyncEngine | None = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._is_initialized = False

    def _create_async_engine(self) -> AsyncEngine:
        """Create and configure the asynchronous database engine."""
        async_url = get_async_database_url()

        if settings.database_driver == "sqlite":
            engine = create_async_engine(
                async_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool if (settings.debug and ":memory:" in async_url) else None,
                echo=False,
                json_serializer=_pydantic_json_serializer,
            )

            # WAL mode for file-based SQLite only — enables concurrent readers
            # during auth token writes. Skipped for :memory: (StaticPool, no contention).
            if ":memory:" not in async_url:

                @event.listens_for(engine.sync_engine, "connect")
                def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
                    cursor = dbapi_connection.cursor()
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                    cursor.close()
        else:
            engine = create_async_engine(
                async_url,
                echo=False,
                pool_size=20,
                max_overflow=0,
                json_serializer=_pydantic_json_serializer,
            )

        logger.info(f"Async database engine created: {async_url}")
        return engine

    @property
    def async_engine(self) -> AsyncEngine:
        """Get or create the asynchronous database engine."""
        if self._async_engine is None:
            self._async_engine = self._create_async_engine()
        return self._async_engine

    @property
    def async_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Get or create the async session factory."""
        if self._async_session_factory is None:
            self._async_session_factory = async_sessionmaker(
                self.async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return self._async_session_factory

    async def create_db_and_tables_async(self) -> None:
        """Create database tables asynchronously."""
        logger.info("Creating database tables (async)...")
        async with self.async_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        logger.info("Database tables created (async)")

    async def drop_db_and_tables_async(self) -> None:
        """Drop all database tables asynchronously."""
        logger.info("Dropping database tables (async)...")
        async with self.async_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
        logger.info("Database tables dropped (async)")

    @asynccontextmanager
    async def get_async_session_context(self) -> AsyncGenerator[AsyncSession]:
        """
        Get an async database session as a context manager.

        Usage:
            async with db_manager.get_async_session_context() as session:
                session.add(model_instance)
                await session.commit()

        Yields:
            AsyncSession: SQLModel async session
        """
        async with self.async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except SQLAlchemyError as e:
                logger.error(f"Database error: {e}")
                await session.rollback()
                raise
            finally:
                await session.close()

    async def get_async_session(self) -> AsyncGenerator[AsyncSession]:
        """
        Get an async database session for FastAPI dependency.

        Usage:
            @app.get("/items")
            async def get_items(
                session: AsyncSession = Depends(db_manager.get_async_session)
            ):
                result = await session.execute(select(Item))
                return result.all()

        Yields:
            AsyncSession: SQLModel async session
        """
        async with self.get_async_session_context() as session:
            await session.connection()
            yield session

    async def close(self) -> None:
        """Close all database connections."""
        if self._async_engine:
            await self._async_engine.dispose()
            logger.info("Async database engine disposed")

    def __repr__(self) -> str:
        """String representation of the DatabaseManager."""
        return (
            f"<DatabaseManager("
            f"driver={settings.database_driver}, "
            f"async_initialized={self._async_engine is not None}"
            f")>"
        )


# Create a singleton instance
db_manager = DatabaseManager()
