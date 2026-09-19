"""Self-registration is opt-in, and a role-less account reaches no DICOM data.

Before this gate anyone who could reach the server could register an active
account and query the PACS through ``/dicom-web/*``.
"""

import pytest
from sqlmodel import select

from clarinet.api.app import app
from clarinet.api.dependencies import get_dicomweb_proxy_service
from clarinet.models.user import User
from tests.conftest import (
    create_authenticated_client,
    create_mock_superuser,
    create_mock_user_with_role,
)
from tests.utils.urls import AUTH_REGISTER, DICOMWEB_STUDIES, INFO

NEW_USER = {"email": "newcomer@example.com", "password": "Password123!"}


class _EmptyPacs:
    async def search_studies(self, params: dict[str, str]) -> list[dict[str, str]]:
        return []


async def _search_studies_as(user, test_session, test_settings) -> int:
    async for client in create_authenticated_client(user, test_session, test_settings):
        app.dependency_overrides[get_dicomweb_proxy_service] = _EmptyPacs
        response = await client.get(DICOMWEB_STUDIES)
    return response.status_code


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
async def test_info_tells_the_frontend_whether_registration_is_open(unauthenticated_client):
    response = await unauthenticated_client.get(INFO)

    assert response.json()["registration_enabled"] is False


@pytest.mark.asyncio
async def test_role_less_user_cannot_query_dicomweb(test_session, test_settings):
    user = await create_mock_superuser(test_session, email="noroles@test.com")
    user.is_superuser = False

    assert await _search_studies_as(user, test_session, test_settings) == 403


@pytest.mark.asyncio
async def test_role_holder_can_query_dicomweb(test_session, test_settings):
    user = await create_mock_user_with_role(test_session, "doctor")

    assert await _search_studies_as(user, test_session, test_settings) == 200


@pytest.mark.asyncio
async def test_superuser_can_query_dicomweb(test_session, test_settings):
    user = await create_mock_superuser(test_session, email="root@test.com")

    assert await _search_studies_as(user, test_session, test_settings) == 200
