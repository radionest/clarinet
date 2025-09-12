"""
Database utilities for the Clarinet framework.

This module provides database connection management, session handling,
and utilities for database operations.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from .db_manager import db_manager


def init_async_engine() -> None:
    """Initialize the async database engine and session factory.

    This function exists for backward compatibility.
    The DatabaseManager handles initialization lazily.
    """
    # DatabaseManager handles lazy initialization
    # This function is now a no-op for compatibility
    pass


async def create_db_and_tables_async() -> None:
    """Create database tables asynchronously."""
    await db_manager.create_db_and_tables_async()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session as a FastAPI dependency.

    Usage:
        @app.get("/items")
        async def get_items(session: AsyncSession = Depends(get_async_session)):
            result = await session.execute(select(Item))
            return result.scalars().all()

    Yields:
        AsyncSession: SQLModel async session
    """
    async for session in db_manager.get_async_session():
        yield session
