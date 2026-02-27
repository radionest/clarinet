"""Tests for utility modules — common, validation, session, admin."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from src.models.auth import AccessToken
from src.models.user import User
from src.utils.auth import get_password_hash

# ===================================================================
# Common utilities
# ===================================================================


class TestCommon:
    """Tests for src.utils.common."""

    def test_timing_decorator(self, capsys):
        from src.utils.common import timing

        @timing
        def add(a: int, b: int) -> int:
            return a + b

        result = add(1, 2)
        assert result == 3
        captured = capsys.readouterr()
        assert "func:'add'" in captured.out
        assert "sec" in captured.out

    def test_copy_object_decorator(self):
        from src.utils.common import copy_object

        class Builder:
            def __init__(self, value: int = 0):
                self.value = value

            @copy_object
            def add(self, n: int):
                self.value += n
                return self

        original = Builder(10)
        result = original.add(5)
        assert result.value == 15
        assert original.value == 10  # Original unchanged


# ===================================================================
# Validation utilities
# ===================================================================


class TestValidation:
    """Tests for src.utils.validation."""

    def test_validate_valid_json(self):
        from src.utils.validation import validate_json_by_schema

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert validate_json_by_schema({"name": "Alice"}, schema) is True

    def test_validate_invalid_json(self):
        from src.exceptions.domain import ValidationError
        from src.utils.validation import validate_json_by_schema

        schema = {"type": "object", "properties": {"age": {"type": "integer"}}, "required": ["age"]}
        with pytest.raises(ValidationError, match="JSON validation failed"):
            validate_json_by_schema({}, schema)

    def test_validate_invalid_schema(self):
        from src.exceptions.domain import ValidationError
        from src.utils.validation import validate_json_by_schema

        bad_schema = {"type": "invalid_type_that_does_not_exist"}
        with pytest.raises(ValidationError, match="Invalid JSON schema"):
            validate_json_by_schema({"key": "value"}, bad_schema)


# ===================================================================
# Session utilities
# ===================================================================


class TestSessionUtils:
    """Tests for src.utils.session."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        """Create a user and some sessions."""
        user = User(
            id=uuid4(),
            email="session_test@test.com",
            hashed_password=get_password_hash("pass"),
            is_active=True,
        )
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        # Create active token
        active_token = AccessToken(
            token=f"active_{uuid4().hex[:20]}",
            user_id=user.id,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            last_accessed=datetime.now(UTC),
        )
        test_session.add(active_token)

        # Create expired token
        expired_token = AccessToken(
            token=f"expired_{uuid4().hex[:20]}",
            user_id=user.id,
            created_at=datetime.now(UTC) - timedelta(days=2),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            last_accessed=datetime.now(UTC) - timedelta(days=1),
        )
        test_session.add(expired_token)
        await test_session.commit()

        return {
            "session": test_session,
            "user": user,
            "active_token": active_token,
            "expired_token": expired_token,
        }

    @pytest.mark.asyncio
    async def test_get_user_sessions(self, env):
        from src.utils.session import get_user_sessions

        sessions = await get_user_sessions(env["session"], env["user"].id, active_only=False)
        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_get_user_sessions_active_only(self, env):
        from src.utils.session import get_user_sessions

        sessions = await get_user_sessions(env["session"], env["user"].id, active_only=True)
        assert len(sessions) == 1
        assert sessions[0].token == env["active_token"].token

    @pytest.mark.asyncio
    async def test_revoke_user_sessions(self, env):
        from src.utils.session import revoke_user_sessions

        count = await revoke_user_sessions(env["session"], env["user"].id)
        assert count == 2

    @pytest.mark.asyncio
    async def test_revoke_except_current(self, env):
        from src.utils.session import revoke_user_sessions

        active_tok = env["active_token"].token
        count = await revoke_user_sessions(env["session"], env["user"].id, except_token=active_tok)
        assert count == 1  # Only the expired one is revoked

    @pytest.mark.asyncio
    async def test_cleanup_expired_sessions(self, env):
        from src.utils.session import cleanup_expired_sessions

        deleted = await cleanup_expired_sessions(env["session"])
        assert deleted >= 1

    @pytest.mark.asyncio
    async def test_validate_session_token(self, env):
        from src.utils.session import validate_session_token

        result = await validate_session_token(env["session"], env["active_token"].token)
        assert result is not None

    @pytest.mark.asyncio
    async def test_validate_session_token_expired(self, env):
        from src.utils.session import validate_session_token

        result = await validate_session_token(env["session"], env["expired_token"].token)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_session_token_ignore_expiry(self, env):
        from src.utils.session import validate_session_token

        result = await validate_session_token(
            env["session"], env["expired_token"].token, check_expiry=False
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_extend_session(self, env):
        from src.utils.session import extend_session

        original_expiry = env["active_token"].expires_at
        extended = await extend_session(
            env["session"], env["active_token"].token, extend_by_hours=48
        )
        assert extended is not None
        assert extended.expires_at > original_expiry

    @pytest.mark.asyncio
    async def test_extend_session_not_found(self, env):
        from src.utils.session import extend_session

        result = await extend_session(env["session"], "nonexistent_token")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_session_stats(self, env):
        from src.utils.session import get_session_stats

        stats = await get_session_stats(env["session"])
        assert "total" in stats
        assert "active" in stats
        assert "expired" in stats
        assert stats["total"] >= 2


# ===================================================================
# Admin utilities
# ===================================================================


class TestAdminUtils:
    """Tests for src.utils.admin."""

    @pytest_asyncio.fixture
    async def env(self, test_session):
        admin = User(
            id=uuid4(),
            email="admin_util@test.com",
            hashed_password=get_password_hash("adminpass"),
            is_active=True,
            is_superuser=True,
        )
        regular = User(
            id=uuid4(),
            email="regular_util@test.com",
            hashed_password=get_password_hash("regularpass"),
            is_active=True,
            is_superuser=False,
        )
        test_session.add_all([admin, regular])
        await test_session.commit()
        return {"session": test_session, "admin": admin, "regular": regular}

    @pytest.mark.asyncio
    async def test_list_admin_users(self, env):
        from src.utils.admin import list_admin_users

        admins = await list_admin_users(env["session"])
        assert any(u.email == "admin_util@test.com" for u in admins)
        assert not any(u.email == "regular_util@test.com" for u in admins)
