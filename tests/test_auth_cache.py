"""Session authentication caching.

The general API keeps no in-process user cache: ``DatabaseStrategy.read_token``
checks the DB on every call, so nothing has to remember to evict anything
(#650, #660). The one cache left is ``current_dicomweb_user`` on /dicom-web.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

from clarinet.api import dependencies as deps
from clarinet.api.auth_config import DatabaseStrategy
from clarinet.models.auth import AccessToken
from clarinet.models.user import User
from tests.utils.factories import make_user


def _make_user(*, active: bool = True) -> User:
    """Create a mock User object."""
    user = MagicMock(spec=User)
    user.id = uuid4()
    user.email = "test@example.com"
    user.is_active = active
    return user


def _make_token(user: User) -> AccessToken:
    """A mock AccessToken accessed just now, so no activity write is due."""
    token = MagicMock(spec=AccessToken)
    token.user_id = user.id
    token.ip_address = None
    token.last_accessed = datetime.now(UTC)
    token.created_at = datetime.now(UTC) - timedelta(hours=1)
    token.expires_at = datetime.now(UTC) + timedelta(hours=24)
    return token


def _make_strategy(
    *, token_obj: AccessToken | None = None, user: User | None = None
) -> DatabaseStrategy:
    """DatabaseStrategy whose session answers the token query, then the user query."""
    session = AsyncMock()
    session.expunge = MagicMock()  # expunge is sync, not async
    if token_obj is not None:
        token_result = MagicMock()
        token_result.scalar_one_or_none.return_value = token_obj
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(side_effect=[token_result, user_result])
    else:
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
    return DatabaseStrategy(session=session, request=None)


@pytest.fixture
def auth_settings():
    with patch("clarinet.api.auth_config.settings") as mock_settings:
        mock_settings.session_cache_ttl_seconds = (
            30  # a configured TTL must not make read_token cache
        )
        mock_settings.session_ip_check = False
        mock_settings.session_idle_timeout_minutes = 0
        mock_settings.session_sliding_refresh = False
        yield mock_settings


class TestReadTokenHitsTheDatabase:
    @pytest.mark.asyncio
    async def test_every_call_queries_the_db(self, auth_settings):
        """A second call with the same token is not served from memory."""
        user = _make_user()
        for _ in range(2):
            strategy = _make_strategy(token_obj=_make_token(user), user=user)
            assert await strategy.read_token("tok-repeat", AsyncMock()) is user
            assert strategy.session.execute.call_count == 2  # token + user

    @pytest.mark.asyncio
    async def test_unknown_token_returns_none(self, auth_settings):
        strategy = _make_strategy(token_obj=None)
        assert await strategy.read_token("tok-unknown", AsyncMock()) is None

    @pytest.mark.asyncio
    async def test_inactive_user_returns_none(self, auth_settings):
        user = _make_user(active=False)
        strategy = _make_strategy(token_obj=_make_token(user), user=user)
        assert await strategy.read_token("tok-inactive", AsyncMock()) is None

    @pytest.mark.asyncio
    async def test_none_token_returns_none_without_db(self):
        strategy = _make_strategy()
        assert await strategy.read_token(None, AsyncMock()) is None
        assert strategy.session.execute.call_count == 0


def _dicomweb_request(*, internal_token: str | None = None) -> MagicMock:
    request = MagicMock(spec=Request)
    request.cookies = {deps.settings.cookie_name: "tok-dw"}
    request.headers = {"X-Internal-Token": internal_token} if internal_token else {}
    request.client = SimpleNamespace(host="10.0.0.1")
    request.url = SimpleNamespace(path="/dicom-web/studies")
    return request


class TestDicomWebUserCache:
    """current_dicomweb_user: TTL-only reuse of a session cookie's verdict."""

    @pytest.fixture(autouse=True)
    def service_user(self):
        with patch(
            "clarinet.api.auth_config._get_service_user", AsyncMock(return_value=None)
        ) as service:
            yield service

    @pytest.mark.asyncio
    async def test_cookie_verdict_is_reused_within_ttl(self):
        user = make_user(is_superuser=True)
        with patch.object(DatabaseStrategy, "read_token", AsyncMock(return_value=user)) as read:
            for _ in range(2):
                assert await deps.current_dicomweb_user(_dicomweb_request(), MagicMock()) is user
        assert read.await_count == 1

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_reuse(self, monkeypatch):
        monkeypatch.setattr(deps.settings, "session_cache_ttl_seconds", 0)
        user = make_user(is_superuser=True)
        with patch.object(DatabaseStrategy, "read_token", AsyncMock(return_value=user)) as read:
            for _ in range(2):
                await deps.current_dicomweb_user(_dicomweb_request(), MagicMock())
        assert read.await_count == 2
        assert "tok-dw" not in deps._dicomweb_user_cache

    @pytest.mark.asyncio
    async def test_service_token_user_is_never_cached_under_the_cookie(self, service_user):
        admin = make_user(is_superuser=True)
        cookie_owner = make_user(roles=[])
        service_user.return_value = admin
        with patch.object(DatabaseStrategy, "read_token", AsyncMock(return_value=cookie_owner)):
            request = _dicomweb_request(internal_token="tok-service")
            assert await deps.current_dicomweb_user(request, MagicMock()) is admin
            assert "tok-dw" not in deps._dicomweb_user_cache

            service_user.return_value = None
            with pytest.raises(HTTPException) as denied:
                await deps.current_dicomweb_user(_dicomweb_request(), MagicMock())
        assert denied.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_is_401_and_not_cached(self):
        with (
            patch.object(DatabaseStrategy, "read_token", AsyncMock(return_value=None)),
            pytest.raises(HTTPException) as denied,
        ):
            await deps.current_dicomweb_user(_dicomweb_request(), MagicMock())
        assert denied.value.status_code == 401
        assert "tok-dw" not in deps._dicomweb_user_cache

    @pytest.mark.asyncio
    async def test_role_less_user_is_403_and_not_cached(self):
        role_less = make_user(roles=[])
        with (
            patch.object(DatabaseStrategy, "read_token", AsyncMock(return_value=role_less)),
            pytest.raises(HTTPException) as denied,
        ):
            await deps.current_dicomweb_user(_dicomweb_request(), MagicMock())
        assert denied.value.status_code == 403
        assert "tok-dw" not in deps._dicomweb_user_cache
