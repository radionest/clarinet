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
from sqlalchemy import delete
from sqlmodel import col

from clarinet.api import auth_config
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


def _aware(value: datetime) -> datetime:
    """SQLite hands timestamps back naive; compare them as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _session_row(session, user_id, *, idle_for: timedelta) -> AccessToken:
    now = datetime.now(UTC)
    row = AccessToken(
        token=f"tok-{uuid4().hex}",
        user_id=user_id,
        expires_at=now + timedelta(hours=24),
        last_accessed=now - idle_for,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _read(session, token: str) -> User | None:
    return await DatabaseStrategy(session, None).read_token(token, None)  # type: ignore[arg-type]


class TestReadTokenActivityWrites:
    """read_token records activity at most once per interval, as a Core UPDATE (#665)."""

    @pytest.fixture(autouse=True)
    def _session_settings(self, monkeypatch):
        monkeypatch.setattr(auth_config.settings, "session_idle_timeout_minutes", 60)
        monkeypatch.setattr(auth_config.settings, "session_sliding_refresh", False)
        monkeypatch.setattr(auth_config.settings, "session_ip_check", False)

    @pytest.mark.asyncio
    async def test_recent_activity_is_not_rewritten(self, test_session, test_user):
        row = await _session_row(test_session, test_user.id, idle_for=timedelta(seconds=10))
        before = row.last_accessed

        assert await _read(test_session, row.token) is not None

        await test_session.refresh(row)
        assert row.last_accessed == before

    @pytest.mark.asyncio
    async def test_stale_activity_is_refreshed(self, test_session, test_user):
        row = await _session_row(test_session, test_user.id, idle_for=timedelta(minutes=5))
        before = _aware(row.last_accessed)

        assert await _read(test_session, row.token) is not None

        await test_session.refresh(row)
        assert _aware(row.last_accessed) > before

    @pytest.mark.asyncio
    async def test_one_minute_idle_timeout_still_sees_activity(
        self, test_session, test_user, monkeypatch
    ):
        """A flat 60 s throttle would log out a user who is active every 40 s."""
        monkeypatch.setattr(auth_config.settings, "session_idle_timeout_minutes", 1)
        row = await _session_row(test_session, test_user.id, idle_for=timedelta(seconds=40))
        before = _aware(row.last_accessed)

        assert await _read(test_session, row.token) is not None

        await test_session.refresh(row)
        assert _aware(row.last_accessed) > before

    @pytest.mark.asyncio
    async def test_session_deleted_mid_request_does_not_raise(
        self, test_session, test_user, monkeypatch
    ):
        """#665: another transaction deletes the row between read_token's SELECT
        and its activity write — the write must not raise StaleDataError (a 500)."""
        row = await _session_row(test_session, test_user.id, idle_for=timedelta(minutes=5))
        token = row.token
        execute = test_session.execute
        calls = 0

        async def execute_then_revoke(statement, *args, **kwargs):
            nonlocal calls
            calls += 1
            result = await execute(statement, *args, **kwargs)
            if calls == 1:  # the token SELECT is done; a concurrent logout lands now
                await execute(
                    delete(AccessToken)
                    .where(col(AccessToken.token) == token)
                    .execution_options(synchronize_session=False)
                )
            return result

        monkeypatch.setattr(test_session, "execute", execute_then_revoke)

        assert await _read(test_session, token) is not None
        assert await _read(test_session, token) is None
