"""Tests for ``clarinet admin reset-password`` (helper: ``reset_user_password``)."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from clarinet.models.auth import AccessToken
from clarinet.models.user import User
from clarinet.utils.admin import reset_user_password
from clarinet.utils.auth import get_password_hash, verify_password
from tests.utils.factories import make_user


async def _seed_user(
    test_session: AsyncSession, *, email: str, is_superuser: bool, password: str
) -> User:
    user = make_user(
        email=email,
        hashed_password=get_password_hash(password),
        is_superuser=is_superuser,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)
    return user


async def _seed_token(test_session: AsyncSession, user: User) -> AccessToken:
    now = datetime.now(UTC)
    token = AccessToken(
        token=f"tok_{uuid4().hex[:20]}",
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        last_accessed=now,
    )
    test_session.add(token)
    await test_session.commit()
    return token


async def _run_reset(username: str, new_password: str, test_session: AsyncSession) -> bool:
    """Invoke ``reset_user_password`` with a session-context mock."""

    @asynccontextmanager
    async def _session_ctx() -> AsyncSession:
        yield test_session

    with patch("clarinet.utils.admin.db_manager") as mock_dbm:
        mock_dbm.get_async_session_context = _session_ctx
        return await reset_user_password(username, new_password)


@pytest.mark.asyncio
async def test_reset_non_superuser_password(test_session: AsyncSession) -> None:
    """A regular (non-superuser) target is reset: True + new hash verifies."""
    user = await _seed_user(
        test_session, email="alice@test.com", is_superuser=False, password="old-pass-1"
    )

    ok = await _run_reset("alice@test.com", "new-pass-2", test_session)

    assert ok is True
    await test_session.refresh(user)
    assert verify_password("new-pass-2", user.hashed_password)


@pytest.mark.asyncio
async def test_reset_invalidates_old_password(test_session: AsyncSession) -> None:
    """The old password no longer verifies after the reset."""
    user = await _seed_user(
        test_session, email="bob@test.com", is_superuser=False, password="old-pass-3"
    )

    await _run_reset("bob@test.com", "new-pass-4", test_session)

    await test_session.refresh(user)
    assert not verify_password("old-pass-3", user.hashed_password)


@pytest.mark.asyncio
async def test_reset_superuser_password_still_works(test_session: AsyncSession) -> None:
    """Superuser targets keep working — the guard removal changes nothing for them."""
    user = await _seed_user(
        test_session, email="root@test.com", is_superuser=True, password="old-pass-5"
    )

    ok = await _run_reset("root@test.com", "new-pass-6", test_session)

    assert ok is True
    await test_session.refresh(user)
    assert verify_password("new-pass-6", user.hashed_password)


@pytest.mark.asyncio
async def test_reset_unknown_email_returns_false(test_session: AsyncSession) -> None:
    """Unknown email → False, nothing written."""
    ok = await _run_reset("nobody@test.com", "whatever-1", test_session)
    assert ok is False


@pytest.mark.asyncio
async def test_reset_revokes_sessions(test_session: AsyncSession) -> None:
    """Reset deletes the target's AccessToken rows in the same flow (#651)."""
    user = await _seed_user(
        test_session, email="carol@test.com", is_superuser=False, password="old-pass-7"
    )
    await _seed_token(test_session, user)
    await _seed_token(test_session, user)

    ok = await _run_reset("carol@test.com", "new-pass-8", test_session)

    assert ok is True
    remaining = (
        (await test_session.execute(select(AccessToken).where(AccessToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert remaining == []
