"""Fixtures for Schemathesis API schema testing.

Uses ASGI transport (no running server) with in-memory SQLite.
Auth is bypassed via dependency overrides — same pattern as tests/conftest.py.
Lifespan is replaced with a no-op to avoid db_manager/reconcile_config conflicts.
"""

import copy
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio
import schemathesis
from fastapi import FastAPI
from schemathesis.checks import load_all_checks
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

# Check exclusions are configured in schemathesis.toml [checks.*] section.
# They apply globally (including stateful tests) via ProjectConfig.
load_all_checks()

# ---------------------------------------------------------------------------
# OpenAPI link injection for stateful testing
# ---------------------------------------------------------------------------

# CRUD patterns: POST on collection → links to GET/PATCH/DELETE on item.
# Schemathesis uses these links to build state-machine transitions.
_CRUD_LINKS: list[dict] = [
    {
        "post_path": "/api/records/types",
        "post_status": "201",
        "id_field": "id",
        "targets": [
            ("GetRecordType", "get", "/api/records/types/{record_type_id}", "record_type_id"),
            ("UpdateRecordType", "patch", "/api/records/types/{record_type_id}", "record_type_id"),
            ("DeleteRecordType", "delete", "/api/records/types/{record_type_id}", "record_type_id"),
        ],
    },
    {
        "post_path": "/api/patients",
        "post_status": "201",
        "id_field": "patient_id",
        "targets": [
            ("GetPatient", "get", "/api/patients/{patient_id}", "patient_id"),
            ("DeletePatient", "delete", "/api/patients/{patient_id}", "patient_id"),
            ("AnonymizePatient", "post", "/api/patients/{patient_id}/anonymize", "patient_id"),
        ],
    },
    {
        "post_path": "/api/user/",
        "post_status": "201",
        "id_field": "id",
        "targets": [
            ("GetUser", "get", "/api/user/{user_id}", "user_id"),
            ("UpdateUser", "put", "/api/user/{user_id}", "user_id"),
            ("DeleteUser", "delete", "/api/user/{user_id}", "user_id"),
            ("GetUserRoles", "get", "/api/user/{user_id}/roles", "user_id"),
        ],
    },
    {
        "post_path": "/api/user/roles",
        "post_status": "201",
        "id_field": "name",
        "targets": [
            ("GetRole", "get", "/api/user/roles/{role_name}", "role_name"),
        ],
    },
    {
        "post_path": "/api/studies",
        "post_status": "201",
        "id_field": "study_uid",
        "targets": [
            ("GetStudy", "get", "/api/studies/{study_uid}", "study_uid"),
            ("GetStudySeries", "get", "/api/studies/{study_uid}/series", "study_uid"),
            ("DeleteStudy", "delete", "/api/studies/{study_uid}", "study_uid"),
        ],
    },
    {
        "post_path": "/api/series",
        "post_status": "201",
        "id_field": "series_uid",
        "targets": [
            ("GetSeries", "get", "/api/series/{series_uid}", "series_uid"),
        ],
    },
    {
        "post_path": "/api/records/",
        "post_status": "201",
        "id_field": "id",
        "targets": [
            ("GetRecord", "get", "/api/records/{record_id}", "record_id"),
            ("UpdateRecordStatus", "patch", "/api/records/{record_id}/status", "record_id"),
            ("UpdateRecord", "patch", "/api/records/{record_id}", "record_id"),
            ("InvalidateRecord", "post", "/api/records/{record_id}/invalidate", "record_id"),
            ("CheckFiles", "post", "/api/records/{record_id}/check-files", "record_id"),
        ],
    },
]


def _inject_crud_links(schema_dict: dict) -> dict:
    """Inject OpenAPI links into POST-201 responses for stateful testing.

    Returns a deep-copied schema with links added. Does not mutate the original.
    """
    schema = copy.deepcopy(schema_dict)
    paths = schema.get("paths", {})

    for crud in _CRUD_LINKS:
        post_path = crud["post_path"]
        post_status = crud["post_status"]
        id_field = crud["id_field"]

        path_item = paths.get(post_path)
        if not path_item or "post" not in path_item:
            continue

        response = path_item["post"].get("responses", {}).get(post_status)
        if not response:
            continue

        links = response.setdefault("links", {})
        for link_name, target_method, target_path, param_name in crud["targets"]:
            target_item = paths.get(target_path, {})
            target_op = target_item.get(target_method, {})
            operation_id = target_op.get("operationId")
            if not operation_id:
                continue

            links[link_name] = {
                "operationId": operation_id,
                "parameters": {param_name: f"$response.body#/{id_field}"},
            }

    return schema


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
        ohif_enabled=False,
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
    """Load OpenAPI schema from ASGI app via ASGI transport.

    Lifespan is already replaced with _noop_lifespan in schema_app,
    so from_asgi() is safe — no db_manager/reconcile_config conflicts.
    """
    return schemathesis.openapi.from_asgi("/openapi.json", app=schema_app)


@pytest.fixture(scope="session")
def stateful_api_schema(schema_app):
    """OpenAPI schema enriched with CRUD links for stateful testing.

    Injects OpenAPI links into POST-201 responses so Schemathesis can
    build state-machine transitions (POST → GET → PATCH → DELETE chains).
    Uses from_dict() because link injection requires modifying the schema dict.
    """
    schema_dict = _inject_crud_links(schema_app.openapi())
    loaded = schemathesis.openapi.from_dict(schema_dict)
    loaded.app = schema_app
    return loaded


@pytest.fixture(scope="session")
def stateful_db_engine(test_engine, mock_superuser):
    """Expose engine + superuser for stateful test DB cleanup.

    The mock_superuser dependency ensures the user is created before
    the stateful test starts (it will be re-created on each teardown).
    """
    return test_engine, mock_superuser
