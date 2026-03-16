"""Fixtures for Schemathesis API schema testing.

Uses ASGI transport (no running server) with in-memory SQLite.
Auth is bypassed via dependency overrides — same pattern as tests/conftest.py.
Lifespan is replaced with a no-op to avoid db_manager/reconcile_config conflicts.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio
import schemathesis
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from clarinet.api.app import app
from clarinet.api.auth_config import current_active_user, current_superuser
from clarinet.models import *  # noqa: F403
from clarinet.models.user import User
from clarinet.settings import Settings
from clarinet.utils.database import get_async_session


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """No-op lifespan — schema tests manage their own DB."""
    yield


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Test settings with external services disabled."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="schema-test-secret-key",
        jwt_algorithm="HS256",
        jwt_expire_minutes=30,
        debug=True,
        pipeline_enabled=False,
        recordflow_enabled=False,
        dicomweb_enabled=False,
        frontend_enabled=False,
    )


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """In-memory SQLite engine for schema tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def _session_factory(test_engine):
    """Session factory bound to the shared engine."""
    return sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session")
async def mock_superuser(_session_factory) -> User:
    """Create mock superuser for schema tests."""
    from clarinet.utils.auth import get_password_hash

    async with _session_factory() as session:
        user = User(
            id=uuid4(),
            email="schema-test@example.com",
            hashed_password=get_password_hash("mock"),
            is_active=True,
            is_verified=True,
            is_superuser=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        session.expunge(user)
        return user


@pytest.fixture(scope="session")
def schema_app(mock_superuser, _session_factory, test_settings):
    """FastAPI app with auth bypassed and lifespan disabled.

    Replaces the real lifespan (which uses db_manager directly)
    with a no-op. Schema tests manage their own DB via test_engine.

    Each request gets a fresh session from the factory to prevent
    PendingRollbackError cascading across requests.
    """
    # Replace lifespan to avoid db_manager/reconcile_config conflicts
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan

    async def override_get_session():
        async with _session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: mock_superuser
    app.dependency_overrides[current_superuser] = lambda: mock_superuser

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = lambda: test_settings
    except (ImportError, AttributeError):
        pass

    yield app

    app.dependency_overrides.clear()
    app.router.lifespan_context = original_lifespan


@pytest.fixture(scope="session")
def api_schema(schema_app):
    """Load OpenAPI schema from ASGI app for Schemathesis.

    Uses app.openapi() directly instead of from_asgi() to avoid
    triggering the lifespan on schema fetch.
    """
    schema_dict = schema_app.openapi()
    loaded = schemathesis.openapi.from_dict(schema_dict)
    loaded.app = schema_app  # Enables ASGI transport for test calls
    loaded.location = "/openapi.json"
    return loaded
