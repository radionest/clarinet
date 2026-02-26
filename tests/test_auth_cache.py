"""Unit tests for DatabaseStrategy in-memory session cache.

Tests cover:
- Cache hit returns user without DB query
- Cache miss on TTL expiry
- Cache eviction when max size reached
- Cache invalidation on logout (destroy_token)
- Cache invalidation on invalid/expired token
- Cache disabled when TTL = 0
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cachetools import TTLCache

from src.api.auth_config import DatabaseStrategy
from src.models.auth import AccessToken
from src.models.user import User


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the class-level cache before and after each test."""
    DatabaseStrategy._user_cache.clear()
    yield
    DatabaseStrategy._user_cache.clear()


def _make_user(*, active: bool = True) -> User:
    """Create a mock User object."""
    user = MagicMock(spec=User)
    user.id = uuid4()
    user.email = "test@example.com"
    user.is_active = active
    return user


def _make_token(user: User, *, expired: bool = False) -> AccessToken:
    """Create a mock AccessToken object."""
    token = MagicMock(spec=AccessToken)
    token.user_id = user.id
    token.ip_address = None
    token.last_accessed = datetime.now(UTC)
    token.created_at = datetime.now(UTC) - timedelta(hours=1)
    if expired:
        token.expires_at = datetime.now(UTC) - timedelta(hours=1)
    else:
        token.expires_at = datetime.now(UTC) + timedelta(hours=24)
    return token


def _make_strategy(*, token_obj: AccessToken | None = None, user: User | None = None):
    """Create a DatabaseStrategy with mocked session."""
    session = AsyncMock()
    session.expunge = MagicMock()  # expunge is sync, not async

    # Mock session.execute to return token query, then user query
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

    strategy = DatabaseStrategy(session=session, request=None)
    return strategy


class TestCacheHit:
    """Test that cached tokens are returned without DB query."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self):
        """Second read_token call should return cached user, no DB query."""
        user = _make_user()
        token_obj = _make_token(user)
        token_str = "test-token-abc"

        with patch("src.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 30
            mock_settings.session_ip_check = False
            mock_settings.session_idle_timeout_minutes = 0
            mock_settings.session_sliding_refresh = False

            # First call — goes to DB, populates cache
            strategy = _make_strategy(token_obj=token_obj, user=user)
            result = await strategy.read_token(token_str, AsyncMock())
            assert result is user
            assert strategy.session.execute.call_count == 2  # token query + user query

            # Second call — new strategy (same class cache), should hit cache
            strategy2 = _make_strategy()  # DB returns None (shouldn't be called)
            result2 = await strategy2.read_token(token_str, AsyncMock())
            assert result2 is user
            assert strategy2.session.execute.call_count == 0  # no DB calls


class TestCacheTTLExpiry:
    """Test that expired cache entries are not returned."""

    @pytest.mark.asyncio
    async def test_expired_cache_entry_triggers_db_query(self):
        """After TTL expires, read_token should query DB again."""
        user = _make_user()
        token_str = "test-token-ttl"

        # Create a short-TTL cache so entries expire quickly
        DatabaseStrategy._user_cache = TTLCache(maxsize=1000, ttl=0.01)
        DatabaseStrategy._user_cache[token_str] = user
        await asyncio.sleep(0.02)  # let entry expire

        with patch("src.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 30

            # Should NOT return cached user — TTL expired
            token_obj = _make_token(user)
            strategy = _make_strategy(token_obj=token_obj, user=user)
            mock_settings.session_ip_check = False
            mock_settings.session_idle_timeout_minutes = 0
            mock_settings.session_sliding_refresh = False

            result = await strategy.read_token(token_str, AsyncMock())
            assert result is user
            # DB was queried (2 calls: token + user)
            assert strategy.session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_fresh_cache_entry_does_not_trigger_db(self):
        """Within TTL, read_token should return cached user without DB."""
        user = _make_user()
        token_str = "test-token-fresh"

        # Insert a fresh cache entry
        DatabaseStrategy._user_cache[token_str] = user

        with patch("src.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 30

            strategy = _make_strategy()
            result = await strategy.read_token(token_str, AsyncMock())
            assert result is user
            assert strategy.session.execute.call_count == 0


class TestCacheEviction:
    """Test LRU eviction when cache is full."""

    @pytest.mark.asyncio
    async def test_oldest_entry_evicted_when_full(self):
        """When cache reaches max size, oldest entry is evicted."""
        # Replace cache with a small-capacity one
        DatabaseStrategy._user_cache = TTLCache(maxsize=3, ttl=60)

        user = _make_user()

        # Fill cache with 3 entries
        DatabaseStrategy._user_cache["token-old"] = user
        DatabaseStrategy._user_cache["token-mid"] = user
        DatabaseStrategy._user_cache["token-new"] = user

        assert len(DatabaseStrategy._user_cache) == 3

        # Add a 4th entry via read_token — should evict "token-old" (LRU)
        new_user = _make_user()
        token_obj = _make_token(new_user)

        with patch("src.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 60
            mock_settings.session_ip_check = False
            mock_settings.session_idle_timeout_minutes = 0
            mock_settings.session_sliding_refresh = False

            strategy = _make_strategy(token_obj=token_obj, user=new_user)
            await strategy.read_token("token-4th", AsyncMock())

        assert "token-old" not in DatabaseStrategy._user_cache
        assert "token-mid" in DatabaseStrategy._user_cache
        assert "token-new" in DatabaseStrategy._user_cache
        assert "token-4th" in DatabaseStrategy._user_cache
        assert len(DatabaseStrategy._user_cache) == 3


class TestCacheInvalidation:
    """Test cache invalidation on logout and invalid tokens."""

    @pytest.mark.asyncio
    async def test_destroy_token_removes_from_cache(self):
        """Logout (destroy_token) should remove cached entry."""
        user = _make_user()
        token_str = "token-to-destroy"

        DatabaseStrategy._user_cache[token_str] = user
        assert token_str in DatabaseStrategy._user_cache

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        session.execute = AsyncMock(return_value=result)

        strategy = DatabaseStrategy(session=session, request=None)
        await strategy.destroy_token(token_str, user)

        assert token_str not in DatabaseStrategy._user_cache

    @pytest.mark.asyncio
    async def test_invalid_token_removed_from_cache(self):
        """If token is not found in DB, it should be removed from cache."""
        user = _make_user()
        token_str = "token-invalid"

        with patch("src.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 1

            # Create a short-TTL cache so the entry expires, forcing DB lookup
            DatabaseStrategy._user_cache = TTLCache(maxsize=1000, ttl=0.01)
            DatabaseStrategy._user_cache[token_str] = user
            await asyncio.sleep(0.02)  # let entry expire

            mock_settings.session_ip_check = False

            # DB returns no token
            strategy = _make_strategy(token_obj=None)
            result = await strategy.read_token(token_str, AsyncMock())

            assert result is None
            assert token_str not in DatabaseStrategy._user_cache

    @pytest.mark.asyncio
    async def test_inactive_user_removed_from_cache(self):
        """If user is inactive, cache entry should be removed."""
        user = _make_user(active=False)
        token_str = "token-inactive-user"

        # Create a short-TTL cache so entry expires, forcing DB lookup
        DatabaseStrategy._user_cache = TTLCache(maxsize=1000, ttl=0.01)
        DatabaseStrategy._user_cache[token_str] = _make_user()
        await asyncio.sleep(0.02)  # let entry expire

        token_obj = _make_token(user)

        with patch("src.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 30
            mock_settings.session_ip_check = False
            mock_settings.session_idle_timeout_minutes = 0
            mock_settings.session_sliding_refresh = False

            strategy = _make_strategy(token_obj=token_obj, user=user)
            result = await strategy.read_token(token_str, AsyncMock())

            assert result is None
            assert token_str not in DatabaseStrategy._user_cache


class TestCacheDisabled:
    """Test behavior when caching is disabled (TTL = 0)."""

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_cache(self):
        """With TTL=0, cache should never be used."""
        user = _make_user()
        token_obj = _make_token(user)
        token_str = "token-no-cache"

        with patch("src.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 0
            mock_settings.session_ip_check = False
            mock_settings.session_idle_timeout_minutes = 0
            mock_settings.session_sliding_refresh = False

            # First call
            strategy = _make_strategy(token_obj=token_obj, user=user)
            await strategy.read_token(token_str, AsyncMock())

            # Cache should remain empty
            assert token_str not in DatabaseStrategy._user_cache

            # Second call should still query DB
            token_obj2 = _make_token(user)
            strategy2 = _make_strategy(token_obj=token_obj2, user=user)
            result = await strategy2.read_token(token_str, AsyncMock())
            assert result is user
            assert strategy2.session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_none_token_returns_none(self):
        """read_token with None token should return None immediately."""
        strategy = _make_strategy()
        result = await strategy.read_token(None, AsyncMock())
        assert result is None
        assert strategy.session.execute.call_count == 0

    @pytest.mark.asyncio
    async def test_empty_token_returns_none(self):
        """read_token with empty string should query DB and find nothing."""
        with patch("src.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 0

            strategy = _make_strategy(token_obj=None)
            result = await strategy.read_token("", AsyncMock())
            assert result is None
