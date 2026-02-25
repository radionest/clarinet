"""
Database manager for the Clarinet framework.

This module provides a centralized database connection manager
that avoids global state and supports both sync and async operations.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from ..settings import settings
from ..utils.logger import logger


def _pydantic_json_serializer(obj: Any) -> str:
    """JSON serializer that handles Pydantic/SQLModel objects in JSON columns."""

    def default(o: Any) -> Any:
        if hasattr(o, "model_dump"):
            return o.model_dump()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    return json.dumps(obj, default=default)


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

    def _get_async_database_url(self) -> str:
        """Convert database URL to async version."""
        if settings.database_driver == "sqlite":
            return f"sqlite+aiosqlite:///{settings.database_name}.db"
        elif settings.database_driver == "postgresql+psycopg2":
            return settings.database_url.replace("postgresql+psycopg2", "postgresql+asyncpg")
        elif settings.database_driver == "postgresql+asyncpg":
            return settings.database_url
        else:
            raise ValueError(f"Async not supported for {settings.database_driver}")

    def _create_async_engine(self) -> AsyncEngine:
        """Create and configure the asynchronous database engine."""
        async_url = self._get_async_database_url()

        if settings.database_driver == "sqlite":
            engine = create_async_engine(
                async_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool if settings.debug else None,
                echo=settings.debug,
                json_serializer=_pydantic_json_serializer,
            )
        else:
            engine = create_async_engine(
                async_url,
                echo=settings.debug,
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
