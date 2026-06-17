"""Presence (online/offline) SSE events + the admin online-users snapshot.

Sessions are not ORM-captured, so the lifecycle points (login / logout /
revoke / cleanup) emit ``PresenceEvent`` explicitly. "Online" = a session that
would still authenticate now (not expired, within the idle timeout), which is
also what ``get_online_user_ids`` returns for the initial snapshot.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from clarinet.api.auth_config import DatabaseStrategy
from clarinet.models.auth import AccessToken
from clarinet.services.events.bus import set_event_bus
from clarinet.services.events.capture import register_capture_listeners
from clarinet.services.events.models import PresenceEvent
from clarinet.services.session_cleanup import SessionCleanupService
from clarinet.settings import settings
from clarinet.utils.session import get_online_user_ids
from tests.conftest import create_authenticated_client, create_mock_superuser
from tests.utils.factories import make_user
from tests.utils.urls import ADMIN_ONLINE_USERS


class RecordingBus:
    """Stand-in for EventBus that records published events instead of fanning out."""

    def __init__(self) -> None:
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)

    def publish_threadsafe(self, event) -> None:
        self.events.append(event)


@pytest.fixture
def sse_bus():
    register_capture_listeners()
    bus = RecordingBus()
    set_event_bus(bus)  # type: ignore[arg-type]
    yield bus
    set_event_bus(None)


def _presence(bus, online=None) -> list[PresenceEvent]:
    out = [e for e in bus.events if isinstance(e, PresenceEvent)]
    if online is not None:
        out = [e for e in out if e.online is online]
    return out


@pytest_asyncio.fixture
async def user(test_session):
    u = make_user(email="presence@test.com")
    test_session.add(u)
    await test_session.commit()
    return u


def _token(user_id, *, fresh=True, expired=False, idle=False) -> AccessToken:
    now = datetime.now(UTC)
    idle_minutes = settings.session_idle_timeout_minutes
    return AccessToken(
        token=f"tok_{user_id}_{int(expired)}{int(idle)}{int(fresh)}",
        user_id=user_id,
        expires_at=(now - timedelta(minutes=1)) if expired else (now + timedelta(hours=1)),
        last_accessed=(now - timedelta(minutes=idle_minutes + 5)) if idle else now,
    )


@pytest.mark.asyncio
async def test_write_token_emits_online(test_session, sse_bus, user):
    sse_bus.events.clear()
    await DatabaseStrategy(test_session).write_token(user)
    online = _presence(sse_bus, online=True)
    assert len(online) == 1
    assert online[0].user_id == user.id


@pytest.mark.asyncio
async def test_destroy_token_emits_offline_when_last(test_session, sse_bus, user):
    strategy = DatabaseStrategy(test_session)
    token = await strategy.write_token(user)
    sse_bus.events.clear()
    await strategy.destroy_token(token, user)
    offline = _presence(sse_bus, online=False)
    assert len(offline) == 1
    assert offline[0].user_id == user.id


@pytest.mark.asyncio
async def test_destroy_token_keeps_online_with_other_session(test_session, sse_bus, user):
    strategy = DatabaseStrategy(test_session)
    token = await strategy.write_token(user)
    await strategy.write_token(user)  # a second live session remains
    sse_bus.events.clear()
    await strategy.destroy_token(token, user)
    assert _presence(sse_bus, online=False) == []  # still online via the other session


@pytest.mark.asyncio
async def test_get_online_user_ids_excludes_idle_and_expired(test_session, user):
    idle_user = make_user(email="idle@test.com")
    expired_user = make_user(email="expired@test.com")
    test_session.add_all([idle_user, expired_user])
    await test_session.commit()
    test_session.add_all(
        [
            _token(user.id),
            _token(idle_user.id, idle=True),
            _token(expired_user.id, expired=True),
        ]
    )
    await test_session.commit()

    ids = await get_online_user_ids(test_session, settings.session_idle_timeout_minutes)
    assert user.id in ids
    assert idle_user.id not in ids
    assert expired_user.id not in ids


@pytest.mark.asyncio
async def test_online_users_endpoint_returns_ids(client, test_session, user):
    test_session.add(_token(user.id))
    await test_session.commit()
    resp = await client.get(ADMIN_ONLINE_USERS)
    assert resp.status_code == 200
    assert str(user.id) in resp.json()["user_ids"]


@pytest.mark.asyncio
async def test_online_users_endpoint_forbidden_for_non_admin(test_session, test_settings):
    non_admin = await create_mock_superuser(test_session, email="non_admin@test.com")
    non_admin.is_superuser = False
    async for ac in create_authenticated_client(non_admin, test_session, test_settings):
        resp = await ac.get(ADMIN_ONLINE_USERS)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cleanup_evicts_idle_and_emits_offline(sse_bus, monkeypatch):
    # Dedicated in-memory engine: _perform_cleanup opens its own session, and
    # sharing the conftest test_session's single StaticPool connection across
    # two sessions corrupts the greenlet context. An isolated engine avoids it.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    u = make_user(email="cleanup@test.com")
    idle_token = _token(u.id, idle=True)  # not expired, but idle past the timeout
    async with maker() as seed:
        seed.add(u)
        await seed.commit()
        seed.add(idle_token)
        await seed.commit()
    sse_bus.events.clear()

    async def fake_get_session():
        async with maker() as s:
            yield s

    monkeypatch.setattr("clarinet.services.session_cleanup.get_async_session", fake_get_session)
    await SessionCleanupService()._perform_cleanup()

    # The idle session is evicted and its owner (now without a valid session) goes offline.
    assert any(e.user_id == u.id for e in _presence(sse_bus, online=False))
    async with maker() as check:
        assert await check.get(AccessToken, idle_token.token) is None
    await engine.dispose()
