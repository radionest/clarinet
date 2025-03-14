"""
Database utilities for the Clarinet framework.

This module provides database connection management, session handling,
and utilities for database operations.
"""

from contextlib import contextmanager
from typing import Generator, Any, Optional

from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool

from ..settings import settings
from ..utils.logger import logger


# Create database engine based on configuration
match settings.database_driver:
    case "sqlite":
        engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if settings.debug else None
        )
    case "postgresql+psycopg2" | "postgresql+asyncpg":
        logger.info(f'Database:'
                    f'{settings.database_host}:{settings.database_port}'
                    f'/{settings.database_name}')
        engine = create_engine(
            settings.database_url,
            echo=settings.debug
        )
    case _:
        raise ValueError(
            f'Database driver {settings.database_driver} is not supported'
            )


def create_db_and_tables() -> None:
    """Create database tables for all SQLModel models."""
    logger.info("Creating database tables...")
    SQLModel.metadata.create_all(engine)
    logger.info("Database tables created")


def drop_db_and_tables() -> None:
    """Drop all database tables (useful for testing)."""
    logger.info("Dropping database tables...")
    SQLModel.metadata.drop_all(engine)
    logger.info("Database tables dropped")


@contextmanager
def get_session_context() -> Generator[Session, None, None]:
    """Get a database session as a context manager.

    Usage:
        with get_session_context() as session:
            session.add(model_instance)
            session.commit()

    Yields:
        Session: SQLModel session
    """
    session = Session(engine)
    try:
        yield session
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    """Get a database session as a FastAPI dependency.

    Usage:
        @app.get("/items")
        def get_items(session: Session = Depends(get_session)):
            return session.exec(select(Item)).all()

    Yields:
        Session: SQLModel session
    """
    with get_session_context() as session:
        yield session


def get_engine() -> Engine:
    """Get the SQLAlchemy engine.

    Returns:
        Engine: The SQLAlchemy engine
    """
    return engine