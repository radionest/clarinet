"""Session authentication caching.

The general API keeps no in-process user cache: ``DatabaseStrategy.read_token``
checks the DB on every call, so nothing has to remember to evict anything
(#650, #660). The one cache left is ``current_dicomweb_user`` on /dicom-web.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from clarinet.api.auth_config import DatabaseStrategy
from clarinet.models.auth import AccessToken
from clarinet.models.user import User


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
