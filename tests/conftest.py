"""Global configuration for integration tests."""

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from clarinet.api.app import app

# Import all models to ensure metadata is populated
from clarinet.models import *  # noqa: F403
from clarinet.models.user import User
from clarinet.settings import Settings
from clarinet.utils.database import get_async_session
from clarinet.utils.logger import logger


@pytest.fixture
def isolated_validator_registry():
    """Snapshot and restore ``_VALIDATOR_REGISTRY`` around a test.

    Use in any test file that exercises ``@record_validator`` — pair with a
    thin file-local ``autouse=True`` fixture if every test in the file needs
    isolation:

        @pytest.fixture(autouse=True)
        def _clean(isolated_validator_registry):
            pass

    Mirrors the registry-isolation pattern already used by
    ``tests/test_schema_hydration.py`` and
    ``tests/test_slicer_context_hydration.py``; centralised here so the
    contract is in one place across unit and integration test files.
    """
    from clarinet.services.record_data_validation import _VALIDATOR_REGISTRY

    saved = dict(_VALIDATOR_REGISTRY)
    yield
    _VALIDATOR_REGISTRY.clear()
    _VALIDATOR_REGISTRY.update(saved)


@pytest.fixture(autouse=True, scope="session")
def _suppress_pynetdicom_logging():
    """Prevent pynetdicom background threads from polluting test output.

    pynetdicom spawns threads for DICOM associations that may outlive
    individual tests. Their logs go through InterceptHandler → loguru,
    causing "I/O operation on closed file" noise when stderr is closed.
    """
    import logging

    pynetdicom_logger = logging.getLogger("pynetdicom")
    pynetdicom_logger.propagate = False
    pynetdicom_logger.addHandler(logging.NullHandler())
    yield


@pytest.fixture(autouse=True, scope="session")
def _disable_toml_export():
    """Prevent API endpoints from writing .toml config files during tests.

    Without this, record type create/update endpoints export .toml files
    to ``./tasks/``, leaving garbage files (especially from schemathesis fuzz).
    """
    app.state.config_mode = "test"
    yield


@pytest.fixture(autouse=True, scope="session")
def _bypass_migration_check():
    """Skip the lifespan fail-fast check on pending alembic migrations.

    Tests create schema via ``SQLModel.metadata.create_all`` in the
    ``test_engine`` fixture, bypassing alembic entirely. The production
    fail-fast check in ``clarinet.api.app.lifespan`` must therefore be
    neutralized session-wide so any ``TestClient`` / ``async with lifespan``
    invocation proceeds. Tests that explicitly verify the fail-fast path
    override this via ``monkeypatch``.
    """
    from unittest.mock import patch

    with patch("clarinet.utils.migrations.verify_migrations_applied", return_value=None):
        yield


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Test settings with in-memory SQLite."""
    return Settings(
        database_url="sqlite+aiosqlite:///test.db",
        jwt_secret_key="test-secret-key-for-testing-only",
        jwt_algorithm="HS256",
        jwt_expire_minutes=30,
        cors_origins=["http://localhost:3000"],
        cors_allow_credentials=True,
        cors_allow_methods=["*"],
        cors_allow_headers=["*"],
        debug=True,
    )


@pytest_asyncio.fixture(scope="session")
async def test_engine(test_settings, worker_id):
    """Create test database engine (one per xdist worker).

    If CLARINET_TEST_DATABASE_URL is set, connects to PostgreSQL.
    With xdist, each worker gets its own database (``<base>_gw0``, etc.)
    to avoid race conditions on enum/table creation.
    Otherwise falls back to SQLite in-memory with StaticPool.
    """
    database_url = os.environ.get("CLARINET_TEST_DATABASE_URL", "")
    worker_db = ""

    if database_url:
        base_url, base_db = database_url.rsplit("/", 1)
        worker_db = f"{base_db}_{worker_id}" if worker_id != "master" else base_db

        # CREATE/DROP DATABASE must run outside a transaction
        admin_engine = create_async_engine(f"{base_url}/postgres", isolation_level="AUTOCOMMIT")
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{worker_db}"'))
            await conn.execute(text(f'CREATE DATABASE "{worker_db}"'))
        await admin_engine.dispose()

        engine = create_async_engine(f"{base_url}/{worker_db}", echo=False, pool_pre_ping=True)
    else:
        database_url = "sqlite+aiosqlite:///:memory:"
        engine = create_async_engine(
            database_url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_fk_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()

    if worker_db:
        admin_engine = create_async_engine(f"{base_url}/postgres", isolation_level="AUTOCOMMIT")
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{worker_db}"'))
        await admin_engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    async_session = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def fresh_session(test_engine) -> AsyncGenerator[AsyncSession]:
    """Create a separate database session (empty identity map).

    Use this instead of test_session when you need to simulate production
    behavior where each request gets a fresh session. This catches lazy-load
    errors (MissingGreenlet) that the shared test_session masks.
    """
    async_session = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


async def create_mock_superuser(session: AsyncSession, email: str = "mock@test.com") -> User:
    """Create a mock superuser detached from the session.

    Expunged after refresh to prevent MissingGreenlet when other
    fixtures call ``session.expire_all()``.

    Args:
        session: Async session to persist the user in.
        email: Email for the mock user (vary per fixture for debugging).

    Returns:
        Detached User instance with all scalar attributes loaded.
    """
    from clarinet.models.user import User
    from clarinet.utils.auth import get_password_hash

    user = User(
        id=uuid4(),
        email=email,
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


def setup_auth_overrides(
    mock_user: User,
    test_session: AsyncSession,
    test_settings: Settings,
) -> None:
    """Set up common dependency overrides for authenticated test clients.

    Args:
        mock_user: Detached superuser returned by ``create_mock_superuser``.
        test_session: Test database session.
        test_settings: Test settings object.
    """
    from clarinet.api.auth_config import current_active_user, current_superuser

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: mock_user
    app.dependency_overrides[current_superuser] = lambda: mock_user

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass


async def create_authenticated_client(
    mock_user: User,
    test_session: AsyncSession,
    test_settings: Settings,
    base_url: str = "http://test",
) -> AsyncGenerator[AsyncClient]:
    """Create an authenticated AsyncClient with auth and session overrides.

    Async generator — use with ``async for`` or as the body of a fixture.

    Args:
        mock_user: Detached superuser returned by ``create_mock_superuser``.
        test_session: Test database session.
        test_settings: Test settings object.
        base_url: Base URL for the test client.

    Yields:
        Configured AsyncClient with cookie handling.
    """
    setup_auth_overrides(mock_user, test_session, test_settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=base_url, cookies={}) as ac:
        original_request = ac.request

        async def request_with_cookies(method, url, **kwargs):
            if ac.cookies:
                headers = kwargs.get("headers") or {}
                cookie_header = "; ".join([f"{k}={v}" for k, v in ac.cookies.items()])
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    kwargs["headers"] = headers
            return await original_request(method, url, **kwargs)

        ac.request = request_with_cookies
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Create test API client with auth bypassed (superuser)."""
    mock_user = await create_mock_superuser(test_session)
    async for ac in create_authenticated_client(mock_user, test_session, test_settings):
        yield ac


@pytest_asyncio.fixture
async def unauthenticated_client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Create test API client WITHOUT auth overrides (real cookie-based auth)."""

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        original_request = ac.request

        async def request_with_cookies(method, url, **kwargs):
            if ac.cookies:
                headers = kwargs.get("headers") or {}
                cookie_header = "; ".join([f"{k}={v}" for k, v in ac.cookies.items()])
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    kwargs["headers"] = headers
            return await original_request(method, url, **kwargs)

        ac.request = request_with_cookies
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def fresh_client(fresh_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Test client using a fresh session (catches lazy-load errors).

    Unlike the regular ``client`` fixture which shares test_session with fixtures,
    this client uses a separate session with an empty identity map, simulating
    production where each request gets its own session.
    """

    async def override_get_session():
        yield fresh_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        original_request = ac.request

        async def request_with_cookies(method, url, **kwargs):
            if ac.cookies:
                headers = kwargs.get("headers") or {}
                cookie_header = "; ".join([f"{k}={v}" for k, v in ac.cookies.items()])
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    kwargs["headers"] = headers
            return await original_request(method, url, **kwargs)

        ac.request = request_with_cookies
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def test_client(test_session, test_settings) -> TestClient:
    """Synchronous test client for simple tests."""

    def override_get_session():
        yield test_session

    def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(test_session):
    """Create test user."""
    from clarinet.models.user import User
    from clarinet.utils.auth import get_password_hash

    user = User(
        id=uuid4(),  # UUID as ID
        email="test@example.com",
        hashed_password=get_password_hash("testpassword"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(test_session):
    """Create test administrator."""
    from clarinet.models.user import User, UserRole
    from clarinet.utils.auth import get_password_hash

    admin = User(
        id=uuid4(),  # UUID as ID
        email="admin@example.com",
        hashed_password=get_password_hash("adminpassword"),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_session.add(admin)
    await test_session.commit()
    await test_session.refresh(admin)

    # Create admin role if it doesn't exist
    admin_role = await test_session.get(UserRole, "admin")
    if not admin_role:
        admin_role = UserRole(name="admin")
        test_session.add(admin_role)
        await test_session.commit()

    # Link user with role through UserRolesLink
    from clarinet.models.user import UserRolesLink

    admin_link = UserRolesLink(user_id=admin.id, role_name="admin")
    test_session.add(admin_link)
    await test_session.commit()

    return admin


@pytest_asyncio.fixture
async def auth_headers(client, test_user):
    """Get headers with authorization cookies."""
    # Login with new fastapi-users API
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "test@example.com",  # fastapi-users uses email as username
            "password": "testpassword",
        },
    )
    assert response.status_code in [200, 204]  # fastapi-users may return 204

    # For cookie-based auth headers are not needed, cookies are stored in the client
    # But return empty dict for compatibility
    return {}


@pytest_asyncio.fixture
async def admin_headers(client, admin_user):
    """Get headers with administrator cookies."""
    # Login with new fastapi-users API
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "admin@example.com",  # fastapi-users uses email as username
            "password": "adminpassword",
        },
    )
    assert response.status_code in [200, 204]  # fastapi-users may return 204

    # For cookie-based auth headers are not needed, cookies are stored in the client
    # But return empty dict for compatibility
    return {}


@pytest_asyncio.fixture
async def test_patient(test_session):
    """Create test patient."""
    from tests.utils.factories import make_patient

    patient = make_patient("TEST_PAT001", "Test Patient", anon_name="ANON_001")
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def test_study(test_session, test_patient):
    """Create test study."""
    from datetime import UTC, datetime

    from clarinet.models.study import Study

    study = Study(
        patient_id=test_patient.id,
        study_uid="1.2.3.4.5.6.7.8.9",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_STUDY_001",
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)
    return study


@pytest_asyncio.fixture
async def test_series(test_session, test_study):
    """Create test series."""
    from clarinet.models.study import Series

    series = Series(
        study_uid=test_study.study_uid,
        series_uid="1.2.3.4.5.6.7.8.9.1",
        series_number=1,
        series_description="Test Series",
    )
    test_session.add(series)
    await test_session.commit()
    await test_session.refresh(series)
    return series


@pytest_asyncio.fixture
async def test_record_type(test_session):
    """Create test record type."""
    from clarinet.models.record import RecordType

    record_type = RecordType(
        name="test-record-type",
        description="Test record type",
        label="Test Type",
        level="SERIES",
    )
    test_session.add(record_type)
    await test_session.commit()
    await test_session.refresh(record_type)
    return record_type


@pytest_asyncio.fixture(autouse=True)
async def clear_database(test_session, test_engine, request):
    """Clear all table data after each test for isolation."""
    yield
    test_name = request.node.nodeid
    try:
        await test_session.rollback()
    except Exception as exc:
        logger.warning(f"[clear_database] rollback failed for {test_name}: {exc}")
    try:
        if test_engine.dialect.name == "postgresql":
            # Use a fresh connection — test_session may be in a broken state
            # after FK violations from background tasks (entity flow).
            async with test_engine.begin() as conn:
                tables = ", ".join(f'"{t.name}"' for t in SQLModel.metadata.sorted_tables)
                await conn.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
        else:
            for table in reversed(SQLModel.metadata.sorted_tables):
                await test_session.execute(table.delete())
            await test_session.commit()
    except Exception as exc:
        logger.error(f"[clear_database] cleanup failed for {test_name}: {exc}")


@pytest_asyncio.fixture
async def clarinet_client(test_session, test_settings):
    """Create ClarinetClient for testing with real API."""
    from unittest.mock import patch

    from clarinet.client import ClarinetClient

    # Override database session dependency
    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    # Override settings in auth_config
    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    # Create transport for AsyncClient
    transport = ASGITransport(app=app)

    # Create real AsyncClient with /api base path and enable cookie jar
    real_client = AsyncClient(transport=transport, base_url="http://test/api", cookies={})

    # Patch the client to properly handle cookies (same as in conftest.py for tests)
    original_request = real_client.request

    async def request_with_cookies(method, url, **kwargs):
        # Always include cookies in headers
        if real_client.cookies:
            headers = kwargs.get("headers") or {}
            cookie_header = "; ".join([f"{k}={v}" for k, v in real_client.cookies.items()])
            if cookie_header:
                headers["Cookie"] = cookie_header
                kwargs["headers"] = headers
        return await original_request(method, url, **kwargs)

    real_client.request = request_with_cookies

    # Patch httpx.AsyncClient to return our test client
    with patch("clarinet.client.httpx.AsyncClient", return_value=real_client):
        # Create ClarinetClient with auto_login=False to avoid login on init
        client = ClarinetClient(
            base_url="http://test/api", username="test@example.com", auto_login=False
        )
        # Replace the client's httpx client with our test client
        client.client = real_client

        yield client

        # Cleanup
        await client.close()

    app.dependency_overrides.clear()


def create_disk_series(
    cache_dir: Path, study_uid: str, series_uid: str, cached_at: float, file_size: int = 1024
) -> Path:
    """Create a fake cached series on disk with a .cached_at marker and a dummy file."""
    series_dir = cache_dir / study_uid / series_uid
    series_dir.mkdir(parents=True, exist_ok=True)

    marker = series_dir / ".cached_at"
    marker.write_text(str(cached_at))

    dummy = series_dir / "1.2.3.dcm"
    dummy.write_bytes(b"\x00" * file_size)

    return series_dir
