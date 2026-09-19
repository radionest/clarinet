"""Self-registration is opt-in, and a role-less account reaches no DICOM data.

Before this gate anyone who could reach the server could register an active
account and query the PACS through ``/dicom-web/*``.

The DICOMweb tests log in for real (cookie -> ``DatabaseStrategy.read_token``):
``User.role_names`` fails soft — roles that were never loaded read as ``[]`` —
so only the real auth path proves a role holder is recognised as one and that a
403 comes from a genuinely empty role list, not from an unloaded relationship.
"""

import pytest
from sqlmodel import select

from clarinet.api.app import app
from clarinet.api.dependencies import get_dicomweb_proxy_service
from clarinet.models.user import User, UserRole, UserRolesLink
from clarinet.settings import settings
from tests.conftest import create_authenticated_client, create_mock_superuser
from tests.utils.urls import (
    AUTH_DICOMWEB_ACCESS,
    AUTH_LOGIN,
    AUTH_REGISTER,
    DICOMWEB_STUDIES,
    INFO,
)

NEW_USER = {"email": "newcomer@example.com", "password": "Password123!"}
TEST_USER_LOGIN = {"username": "test@example.com", "password": "testpassword"}


class _EmptyPacs:
    async def search_studies(self, params: dict[str, str]) -> list[dict[str, str]]:
        return []


async def _grant_role(session, user: User, role_name: str) -> None:
    session.add(UserRole(name=role_name))
    await session.commit()
    session.add(UserRolesLink(user_id=user.id, role_name=role_name))
    await session.commit()


async def _login(client) -> None:
    app.dependency_overrides[get_dicomweb_proxy_service] = _EmptyPacs
    assert (await client.post(AUTH_LOGIN, data=TEST_USER_LOGIN)).status_code in (200, 204)


@pytest.mark.asyncio
async def test_registration_is_disabled_by_default(unauthenticated_client, test_session):
    response = await unauthenticated_client.post(AUTH_REGISTER, json=NEW_USER)

    assert response.status_code == 403
    created = await test_session.execute(select(User).where(User.email == NEW_USER["email"]))
    assert created.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_registration_works_when_enabled(unauthenticated_client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "registration_enabled", True)

    response = await unauthenticated_client.post(AUTH_REGISTER, json=NEW_USER)

    assert response.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_info_tells_the_frontend_whether_registration_is_open(
    unauthenticated_client, monkeypatch, enabled
):
    # routers/info.py reads the global settings object, not test_settings.
    monkeypatch.setattr(settings, "registration_enabled", enabled)

    response = await unauthenticated_client.get(INFO)

    assert response.json()["registration_enabled"] is enabled


@pytest.mark.asyncio
async def test_role_less_user_cannot_query_dicomweb(unauthenticated_client, test_user):
    await _login(unauthenticated_client)

    response = await unauthenticated_client.get(DICOMWEB_STUDIES)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_role_holder_can_query_dicomweb(unauthenticated_client, test_user, test_session):
    await _grant_role(test_session, test_user, "doctor")
    await _login(unauthenticated_client)

    response = await unauthenticated_client.get(DICOMWEB_STUDIES)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_superuser_can_query_dicomweb(test_session, test_settings):
    user = await create_mock_superuser(test_session, email="root@test.com")

    async for client in create_authenticated_client(user, test_session, test_settings):
        app.dependency_overrides[get_dicomweb_proxy_service] = _EmptyPacs
        response = await client.get(DICOMWEB_STUDIES)

    assert response.status_code == 200


# --- nginx auth_request target for the external DICOMweb backend ---


@pytest.mark.asyncio
async def test_dicomweb_access_rejects_anonymous(unauthenticated_client):
    assert (await unauthenticated_client.get(AUTH_DICOMWEB_ACCESS)).status_code == 401


@pytest.mark.asyncio
async def test_dicomweb_access_rejects_role_less_user(unauthenticated_client, test_user):
    await _login(unauthenticated_client)

    assert (await unauthenticated_client.get(AUTH_DICOMWEB_ACCESS)).status_code == 403


@pytest.mark.asyncio
async def test_dicomweb_access_admits_role_holder(unauthenticated_client, test_user, test_session):
    await _grant_role(test_session, test_user, "doctor")
    await _login(unauthenticated_client)

    assert (await unauthenticated_client.get(AUTH_DICOMWEB_ACCESS)).status_code == 204
