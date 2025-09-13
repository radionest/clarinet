"""Global configuration for integration tests."""

import asyncio
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from src.api.app import app

# Import all models to ensure metadata is populated
from src.models import *  # noqa: F403
from src.settings import Settings
from src.utils.database import get_async_session


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create event loop for the entire test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


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


@pytest_asyncio.fixture
async def test_engine(test_settings):
    """Create test database engine."""
    # Use aiosqlite for async testing
    database_url = "sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(
        database_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session."""
    async_session = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient, None]:
    """Create test API client."""

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session

    # Override settings if such dependency exists
    try:
        from src.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    # Also override settings object directly in security module
    # Update auth_config settings if needed
    try:
        import src.api.auth_config

        src.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    # Use cookies=True to enable cookie jar
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        # Patch the client to properly handle cookies
        original_request = ac.request

        async def request_with_cookies(method, url, **kwargs):
            # Always include cookies in headers
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
        from src.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(test_session):
    """Create test user."""
    from src.models.user import User
    from src.utils.auth import get_password_hash

    user = User(
        id="test_user",  # Username as ID
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
    from src.models.user import User, UserRole
    from src.utils.auth import get_password_hash

    admin = User(
        id="admin_user",  # Username as ID
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
    from src.models.user import UserRolesLink

    admin_link = UserRolesLink(user_id=admin.id, role_name="admin")
    test_session.add(admin_link)
    await test_session.commit()

    return admin


@pytest_asyncio.fixture
async def auth_headers(client, test_user):
    """Get headers with authorization cookies."""
    # Login with new fastapi-users API
    response = await client.post(
        "/auth/login",
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
        "/auth/login",
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
    from src.models.patient import Patient

    patient = Patient(id="TEST_PAT001", name="Test Patient", anon_name="ANON_001")
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def test_study(test_session, test_patient):
    """Create test study."""
    from datetime import UTC, datetime

    from src.models.study import Study

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


@pytest_asyncio.fixture(autouse=True)
async def clear_database(test_session):
    """Clear database after each test."""
    yield
    # Cleanup occurs automatically through rollback in test_session
