"""Failed-auth throttling: login lockout and ``X-Internal-Token`` guessing.

Counters are in-memory in ``clarinet.api.auth_config``; the autouse
``_reset_auth_throttle`` fixture in ``conftest.py`` clears them between tests.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from cachetools import TTLCache
from fastapi import HTTPException
from fastapi_users.exceptions import UserNotExists
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from clarinet.api import auth_config
from clarinet.api.app import app
from clarinet.api.auth_config import UserManager
from clarinet.models.user import User
from tests.utils.urls import AUTH_LOGIN, AUTH_ME

GOOD = {"username": "test@example.com", "password": "testpassword"}
BAD = {"username": "test@example.com", "password": "wrong-password"}
REMOTE_IP = "203.0.113.7"
SERVICE_TOKEN = "tok-123"


def _client_from(ip: str) -> AsyncClient:
    """Client whose requests arrive from ``ip`` (the fixture client is loopback)."""
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 50000)), base_url="http://test")


@pytest_asyncio.fixture
async def service_admin(test_session, test_settings, monkeypatch):
    """Admin row the service token resolves to, plus a known token value."""
    monkeypatch.setattr(test_settings, "internal_service_token", SecretStr(SERVICE_TOKEN))
    admin = User(
        id=uuid4(),
        email=test_settings.admin_email,
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_session.add(admin)
    await test_session.commit()
    return admin


@pytest.mark.asyncio
async def test_account_locked_after_default_max_failures(unauthenticated_client, test_user):
    for _ in range(5):  # default login_max_failures_per_account
        response = await unauthenticated_client.post(AUTH_LOGIN, data=BAD)
        assert response.status_code == 400

    response = await unauthenticated_client.post(AUTH_LOGIN, data=GOOD)

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_successful_login_resets_account_counter(
    unauthenticated_client, test_user, test_settings, monkeypatch
):
    monkeypatch.setattr(test_settings, "login_max_failures_per_account", 3)
    for _ in range(2):
        await unauthenticated_client.post(AUTH_LOGIN, data=BAD)
    assert (await unauthenticated_client.post(AUTH_LOGIN, data=GOOD)).status_code in (200, 204)

    for _ in range(2):
        response = await unauthenticated_client.post(AUTH_LOGIN, data=BAD)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_account_counter_ignores_email_case(
    unauthenticated_client, test_user, test_settings, monkeypatch
):
    monkeypatch.setattr(test_settings, "login_max_failures_per_account", 2)
    for _ in range(2):
        await unauthenticated_client.post(AUTH_LOGIN, data={**BAD, "username": "TEST@Example.com"})

    response = await unauthenticated_client.post(AUTH_LOGIN, data=GOOD)

    assert response.status_code == 429


@pytest.mark.asyncio
async def test_ip_locked_across_accounts(
    unauthenticated_client, test_user, test_settings, monkeypatch
):
    monkeypatch.setattr(test_settings, "login_max_failures_per_ip", 3)
    for i in range(3):
        await unauthenticated_client.post(
            AUTH_LOGIN, data={"username": f"nobody{i}@example.com", "password": "x"}
        )

    response = await unauthenticated_client.post(AUTH_LOGIN, data=GOOD)

    assert response.status_code == 429


@pytest.mark.asyncio
async def test_lockout_minutes_zero_disables_throttling(
    unauthenticated_client, test_user, test_settings, monkeypatch
):
    monkeypatch.setattr(test_settings, "login_lockout_minutes", 0)
    for _ in range(6):
        await unauthenticated_client.post(AUTH_LOGIN, data=BAD)

    response = await unauthenticated_client.post(AUTH_LOGIN, data=GOOD)

    assert response.status_code in (200, 204)


@pytest.mark.asyncio
async def test_concurrent_burst_cannot_exceed_account_limit():
    """Attempts are reserved before the first await, so a parallel burst can't
    slip past the check while earlier guesses are still being verified."""

    class _SlowUnknownUserDb:
        async def get_by_email(self, email: str) -> None:
            await asyncio.sleep(0)  # yield, like a real DB round-trip
            raise UserNotExists()

    async def attempt() -> int:
        manager = UserManager(user_db=_SlowUnknownUserDb(), client_ip=None)
        try:
            await manager.authenticate(SimpleNamespace(username="a@example.com", password="x"))
        except HTTPException as e:
            return e.status_code
        return 400

    results = await asyncio.gather(*(attempt() for _ in range(8)))

    assert results.count(400) == 5  # default login_max_failures_per_account
    assert results.count(429) == 3


def test_failures_do_not_extend_the_lockout_window(monkeypatch):
    """Fixed window anchored at the first failure: a trickle of typos behind a
    shared NAT address must not keep the per-IP counter alive forever."""
    now = [0.0]
    monkeypatch.setattr(
        auth_config, "_auth_failures", TTLCache(maxsize=10, ttl=60, timer=lambda: now[0])
    )
    auth_config._record_auth_failure("ip:10.0.0.1")
    now[0] = 50.0
    auth_config._record_auth_failure("ip:10.0.0.1")
    assert auth_config._is_throttled("ip:10.0.0.1", 2)

    now[0] = 61.0  # past the first failure's window, within the second's

    assert not auth_config._is_throttled("ip:10.0.0.1", 2)


def test_successful_login_does_not_open_the_ip_window(monkeypatch):
    """A forgiven attempt must leave no entry behind: a zero-count leftover would
    anchor the window at the *success*, so a later lock could expire in seconds."""
    now = [0.0]
    monkeypatch.setattr(
        auth_config, "_auth_failures", TTLCache(maxsize=10, ttl=60, timer=lambda: now[0])
    )
    auth_config._record_auth_failure("ip:10.0.0.1")  # attempt reserved...
    auth_config._forgive_auth_failure("ip:10.0.0.1")  # ...and taken back on success

    now[0] = 50.0
    auth_config._record_auth_failure("ip:10.0.0.1")
    auth_config._record_auth_failure("ip:10.0.0.1")
    now[0] = 61.0  # 61s after the success, only 11s after the first real failure

    assert auth_config._is_throttled("ip:10.0.0.1", 2)


@pytest.mark.asyncio
async def test_invalid_service_token_locks_remote_ip(
    unauthenticated_client, service_admin, test_settings, monkeypatch
):
    monkeypatch.setattr(test_settings, "login_max_failures_per_ip", 3)
    async with _client_from(REMOTE_IP) as remote:
        valid = {"X-Internal-Token": SERVICE_TOKEN}
        assert (await remote.get(AUTH_ME, headers=valid)).status_code == 200
        for _ in range(3):
            await remote.get(AUTH_ME, headers={"X-Internal-Token": "guess"})

        response = await remote.get(AUTH_ME, headers=valid)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_loopback_service_token_is_never_locked(
    unauthenticated_client, service_admin, test_settings, monkeypatch
):
    # In-process RecordFlow and co-located workers share 127.0.0.1: one stale
    # worker must not lock the valid token out.
    monkeypatch.setattr(test_settings, "login_max_failures_per_ip", 3)
    for _ in range(3):
        await unauthenticated_client.get(AUTH_ME, headers={"X-Internal-Token": "guess"})

    response = await unauthenticated_client.get(
        AUTH_ME, headers={"X-Internal-Token": SERVICE_TOKEN}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_non_ascii_service_token_is_rejected_not_crashed(
    unauthenticated_client, service_admin
):
    # hmac.compare_digest raises TypeError on a non-ASCII str; uncaught, that is an
    # unauthenticated 500 whose traceback renders the real token as a frame local.
    response = await unauthenticated_client.get(AUTH_ME, headers={"X-Internal-Token": b"\xff\xfe"})

    assert response.status_code == 401
