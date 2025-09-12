"""Глобальная конфигурация для интеграционных тестов."""

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
    """Создание event loop для всей тестовой сессии."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Тестовые настройки с SQLite в памяти."""
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
    """Создание тестового движка БД."""
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
    """Создание тестовой сессии БД."""
    async_session = sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient, None]:
    """Создание тестового клиента API."""

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session

    # Переопределяем настройки если есть такая зависимость
    try:
        from src.settings import get_settings
        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass
    
    # Also override settings object directly in security module
    import src.api.security
    src.api.security.settings = test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def test_client(test_session, test_settings) -> TestClient:
    """Синхронный тестовый клиент для простых тестов."""

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
    """Создание тестового пользователя."""
    from src.api.security import get_password_hash
    from src.models.user import User

    user = User(
        id="test@example.com",  # Using email as ID
        password=get_password_hash("testpassword"),
        isactive=True,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_user(test_session):
    """Создание тестового администратора."""
    from src.api.security import get_password_hash
    from src.models.user import User, UserRole

    admin = User(
        id="admin@example.com",  # Using email as ID
        password=get_password_hash("adminpassword"),
        isactive=True,
    )
    test_session.add(admin)
    await test_session.commit()
    await test_session.refresh(admin)

    # Создаем роль администратора если она не существует
    admin_role = await test_session.get(UserRole, "admin")
    if not admin_role:
        admin_role = UserRole(name="admin")
        test_session.add(admin_role)
        await test_session.commit()
    
    # Связываем пользователя с ролью через UserRolesLink
    from src.models.user import UserRolesLink
    admin_link = UserRolesLink(user_id=admin.id, role_name="admin")
    test_session.add(admin_link)
    await test_session.commit()

    return admin


@pytest_asyncio.fixture
async def auth_headers(client, test_user):
    """Получение заголовков с токеном авторизации."""
    response = await client.post(
        "/auth/login",
        data={
            "username": "test@example.com",
            "password": "testpassword",
        }
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_headers(client, admin_user):
    """Получение заголовков с токеном администратора."""
    response = await client.post(
        "/auth/login",
        data={
            "username": "admin@example.com",
            "password": "adminpassword",
        }
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def test_patient(test_session):
    """Создание тестового пациента."""
    from src.models.patient import Patient
    
    patient = Patient(
        id="TEST_PAT001",
        name="Test Patient",
        anon_name="ANON_001"
    )
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def test_study(test_session, test_patient):
    """Создание тестового исследования."""
    from datetime import UTC, datetime
    from src.models.study import Study
    
    study = Study(
        patient_id=test_patient.id,
        study_uid="1.2.3.4.5.6.7.8.9",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_STUDY_001"
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)
    return study


@pytest_asyncio.fixture(autouse=True)
async def clear_database(test_session):
    """Очистка БД после каждого теста."""
    yield
    # Очистка происходит автоматически через rollback в test_session
