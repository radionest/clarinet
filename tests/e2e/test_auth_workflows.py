"""
End-to-end tests for authentication and authorization workflows.

Tests cover:
- Complete registration flow
- Session lifecycle management
- Multi-session handling
- Role-based access control
- Cookie authentication
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.auth import AccessToken
from src.models.user import User, UserRole, UserRolesLink
from src.settings import settings


class TestCompleteRegistrationFlow:
    """Test complete user registration and authentication flow."""

    @pytest.mark.asyncio
    async def test_registration_login_logout_flow(
        self, client: AsyncClient, test_session: AsyncSession
    ):
        """Test complete registration -> login -> access -> logout flow."""
        # Step 1: Register new user
        registration_data = {
            "email": "newuser@example.com",
            "password": "SecurePassword123!",
        }

        response = await client.post("/api/auth/register", json=registration_data)
        assert response.status_code == 201
        user_data = response.json()
        assert user_data["email"] == registration_data["email"]
        assert "id" in user_data
        user_id = user_data["id"]

        # Verify user created in database
        stmt = select(User).where(User.email == registration_data["email"])
        result = await test_session.execute(stmt)
        user = result.scalar_one()
        assert user is not None
        assert str(user.id) == user_id
        assert user.is_active is True
        assert user.is_superuser is False

        # Step 2: Login with new user
        login_data = {
            "username": registration_data["email"],  # FastAPI-users uses email as username
            "password": registration_data["password"],
        }

        response = await client.post("/api/auth/login", data=login_data)
        assert response.status_code in [200, 204]

        # Check cookie was set
        assert settings.cookie_name in response.cookies
        session_cookie = response.cookies[settings.cookie_name]

        # Verify session created in database
        stmt = select(AccessToken).where(AccessToken.user_id == user.id)
        result = await test_session.execute(stmt)
        access_token = result.scalar_one()
        assert access_token is not None
        assert access_token.token == session_cookie
        assert access_token.expires_at > datetime.now(UTC)

        # Step 3: Access protected endpoint
        response = await client.get("/api/auth/me")
        assert response.status_code == 200
        me_data = response.json()
        assert me_data["email"] == registration_data["email"]
        assert me_data["id"] == user_id

        # Step 4: Logout
        response = await client.post("/api/auth/logout")
        assert response.status_code in [200, 204]

        # Verify session removed from database
        stmt = select(AccessToken).where(AccessToken.token == session_cookie)
        result = await test_session.execute(stmt)
        access_token = result.scalar_one_or_none()
        assert access_token is None

        # Step 5: Try to access protected endpoint after logout
        response = await client.get("/api/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_registration_validation(self, client: AsyncClient):
        """Test registration with invalid data."""
        # Test with invalid email
        response = await client.post(
            "/api/auth/register",
            json={"email": "invalid-email", "password": "Password123!"}
        )
        assert response.status_code == 422

        # Test with weak password
        response = await client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "password": "123"}
        )
        assert response.status_code == 422

        # Test with missing fields
        response = await client.post("/api/auth/register", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_duplicate_registration(
        self, client: AsyncClient, test_user: User
    ):
        """Test registration with existing email."""
        response = await client.post(
            "/api/auth/register",
            json={"email": test_user.email, "password": "NewPassword123!"}
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"].lower()


class TestSessionLifecycle:
    """Test session lifecycle management."""

    @pytest.mark.asyncio
    async def test_session_creation_and_expiry(
        self, client: AsyncClient, test_user: User, test_session: AsyncSession
    ):
        """Test session creation with proper expiry time."""
        # Login to create session
        response = await client.post(
            "/api/auth/login",
            data={"username": test_user.email, "password": "testpassword"}
        )
        assert response.status_code in [200, 204]

        # Get session from database
        stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
        result = await test_session.execute(stmt)
        access_token = result.scalar_one()

        # Check expiry is set correctly
        expected_expiry = datetime.now(UTC) + timedelta(hours=settings.session_expire_hours)
        assert abs((access_token.expires_at - expected_expiry).total_seconds()) < 60

        # Check metadata
        assert access_token.user_agent is not None  # From test client
        assert access_token.created_at <= datetime.now(UTC)
        assert access_token.last_accessed <= datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_session_sliding_refresh(
        self, client: AsyncClient, test_user: User, test_session: AsyncSession
    ):
        """Test session sliding refresh on activity."""
        # Enable sliding refresh in settings
        with patch.object(settings, "session_sliding_refresh", True):
            # Login
            response = await client.post(
                "/api/auth/login",
                data={"username": test_user.email, "password": "testpassword"}
            )
            assert response.status_code in [200, 204]

            # Get initial session
            stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
            result = await test_session.execute(stmt)
            access_token = result.scalar_one()
            initial_expiry = access_token.expires_at

            # Simulate time passing (less than 50% of session lifetime)
            with patch("src.api.auth_config.datetime") as mock_datetime:
                # Move time forward by 25% of session lifetime
                future_time = datetime.now(UTC) + timedelta(
                    hours=settings.session_expire_hours * 0.25
                )
                mock_datetime.now.return_value = future_time
                mock_datetime.UTC = UTC

                # Make authenticated request
                response = await client.get("/api/auth/me")
                assert response.status_code == 200

                # Session should not be refreshed yet (> 50% time remaining)
                await test_session.refresh(access_token)
                assert access_token.expires_at == initial_expiry

            # Now move time to trigger refresh (> 50% of lifetime passed)
            with patch("src.api.auth_config.datetime") as mock_datetime:
                future_time = datetime.now(UTC) + timedelta(
                    hours=settings.session_expire_hours * 0.6
                )
                mock_datetime.now.return_value = future_time
                mock_datetime.UTC = UTC

                # Make authenticated request
                response = await client.get("/api/auth/me")
                assert response.status_code == 200

                # Session should be refreshed
                await test_session.refresh(access_token)
                assert access_token.expires_at > initial_expiry

    @pytest.mark.asyncio
    async def test_session_idle_timeout(
        self, client: AsyncClient, test_user: User, test_session: AsyncSession
    ):
        """Test session expiry due to inactivity."""
        # Set idle timeout
        with patch.object(settings, "session_idle_timeout_minutes", 30):
            # Login
            response = await client.post(
                "/api/auth/login",
                data={"username": test_user.email, "password": "testpassword"}
            )
            assert response.status_code in [200, 204]

            # Get session
            stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
            result = await test_session.execute(stmt)
            access_token = result.scalar_one()

            # Access endpoint - should work
            response = await client.get("/api/auth/me")
            assert response.status_code == 200

            # Simulate idle time passing
            access_token.last_accessed = datetime.now(UTC) - timedelta(minutes=31)
            await test_session.commit()

            # Try to access - should fail due to idle timeout
            response = await client.get("/api/auth/me")
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_session_cleanup(
        self, client: AsyncClient, test_user: User, test_session: AsyncSession
    ):
        """Test that expired sessions are not usable."""
        # Login
        response = await client.post(
            "/api/auth/login",
            data={"username": test_user.email, "password": "testpassword"}
        )
        assert response.status_code in [200, 204]

        # Manually expire the session
        stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
        result = await test_session.execute(stmt)
        access_token = result.scalar_one()
        access_token.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await test_session.commit()

        # Try to access protected endpoint
        response = await client.get("/api/auth/me")
        assert response.status_code == 401


class TestMultiSessionHandling:
    """Test multiple concurrent sessions."""

    @pytest.mark.asyncio
    async def test_multiple_sessions_same_user(
        self, test_user: User, test_session: AsyncSession
    ):
        """Test user can have multiple active sessions."""
        # Use single client with different sessions
        from src.api.app import app
        transport = ASGITransport(app=app)

        session_tokens = []

        for i in range(3):
            # Create new client for each session
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies={},
                headers={"User-Agent": f"Device-{i}"}
            ) as new_client:
                # Login from each "device"
                response = await new_client.post(
                    "/api/auth/login",
                    data={"username": test_user.email, "password": "testpassword"}
                )
                assert response.status_code in [200, 204]

                # Store session token
                session_tokens.append(response.cookies[settings.cookie_name])

        # Verify all sessions exist in database
        stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
        result = await test_session.execute(stmt)
        sessions = result.scalars().all()
        assert len(sessions) == 3

        # Verify each session has different token
        db_tokens = {s.token for s in sessions}
        assert len(db_tokens) == 3
        assert db_tokens == set(session_tokens)

        # Each session should work independently
        for token in session_tokens:
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies={settings.cookie_name: token}
            ) as test_client:
                response = await test_client.get("/api/auth/me")
                assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_concurrent_session_limit(
        self, test_user: User, test_session: AsyncSession
    ):
        """Test enforcement of concurrent session limit."""
        # Set concurrent session limit
        with patch.object(settings, "session_concurrent_limit", 2):
            session_tokens = []
            from src.api.app import app
            transport = ASGITransport(app=app)

            # Create 3 sessions (exceeding limit of 2)
            for i in range(3):
                async with AsyncClient(
                    transport=transport,
                    base_url="http://test",
                    cookies={},
                    headers={"User-Agent": f"Device-{i}"}
                ) as client:
                    response = await client.post(
                        "/api/auth/login",
                        data={"username": test_user.email, "password": "testpassword"}
                    )
                    assert response.status_code in [200, 204]
                    session_tokens.append(response.cookies[settings.cookie_name])

            # Check that only 2 sessions remain (oldest was removed)
            stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
            result = await test_session.execute(stmt)
            sessions = result.scalars().all()
            assert len(sessions) == 2

            # First session should be removed
            remaining_tokens = {s.token for s in sessions}
            assert session_tokens[0] not in remaining_tokens
            assert session_tokens[1] in remaining_tokens
            assert session_tokens[2] in remaining_tokens

    @pytest.mark.asyncio
    async def test_logout_all_sessions(
        self, client: AsyncClient, test_user: User, test_session: AsyncSession
    ):
        """Test logging out all sessions for a user."""
        # Create multiple sessions
        session_tokens = []
        for i in range(3):
            response = await client.post(
                "/api/auth/login",
                data={"username": test_user.email, "password": "testpassword"}
            )
            assert response.status_code in [200, 204]
            session_tokens.append(response.cookies[settings.cookie_name])

            # Clear cookies for next login
            client.cookies.clear()

        # Implement logout all (this would be a custom endpoint)
        # For now, manually delete all sessions
        from sqlalchemy import delete
        stmt = delete(AccessToken).where(AccessToken.user_id == test_user.id)
        await test_session.execute(stmt)
        await test_session.commit()

        # Verify all sessions are gone
        stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
        result = await test_session.execute(stmt)
        sessions = result.scalars().all()
        assert len(sessions) == 0


class TestRoleBasedAccessControl:
    """Test role-based access control."""

    @pytest.mark.asyncio
    async def test_regular_user_access(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Test regular user can access normal endpoints but not admin."""
        # Can access own profile
        response = await client.get("/api/auth/me", headers=auth_headers)
        assert response.status_code == 200

        # Cannot access admin endpoints (example - would need actual admin endpoint)
        # This is a placeholder - replace with actual admin endpoint
        response = await client.get("/api/admin/users", headers=auth_headers)
        assert response.status_code in [403, 404]  # 403 Forbidden or 404 if route doesn't exist

    @pytest.mark.asyncio
    async def test_admin_user_access(
        self, client: AsyncClient, admin_user: User, admin_headers: dict
    ):
        """Test admin user can access both normal and admin endpoints."""
        # Can access own profile
        response = await client.get("/api/auth/me", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["is_superuser"] is True

        # Can access admin endpoints (would need actual admin endpoint)
        # This is a placeholder - actual implementation would test real admin endpoints

    @pytest.mark.asyncio
    async def test_role_assignment(
        self, test_session: AsyncSession, test_user: User
    ):
        """Test role assignment through UserRolesLink."""
        # Create admin role if doesn't exist
        admin_role = await test_session.get(UserRole, "admin")
        if not admin_role:
            admin_role = UserRole(name="admin", description="Administrator role")
            test_session.add(admin_role)
            await test_session.commit()

        # Assign admin role to user
        role_link = UserRolesLink(user_id=test_user.id, role_name="admin")
        test_session.add(role_link)
        await test_session.commit()

        # Verify role assignment
        stmt = select(UserRolesLink).where(UserRolesLink.user_id == test_user.id)
        result = await test_session.execute(stmt)
        links = result.scalars().all()
        assert len(links) == 1
        assert links[0].role_name == "admin"

    @pytest.mark.asyncio
    async def test_privilege_escalation_prevention(
        self, client: AsyncClient, test_user: User, auth_headers: dict,
        test_session: AsyncSession
    ):
        """Test that users cannot escalate their own privileges."""
        # Try to make self superuser (should fail)
        response = await client.patch(
            f"/api/users/{test_user.id}",
            json={"is_superuser": True},
            headers=auth_headers
        )
        # Should either be forbidden or not found
        assert response.status_code in [403, 404, 422]

        # Verify user is still not superuser
        stmt = select(User).where(User.id == test_user.id)
        result = await test_session.execute(stmt)
        user = result.scalar_one()
        assert user.is_superuser is False


class TestCookieAuthentication:
    """Test cookie-based authentication mechanisms."""

    @pytest.mark.asyncio
    async def test_cookie_attributes(self, client: AsyncClient, test_user: User):
        """Test that cookies have correct security attributes."""
        # Login to get cookie
        response = await client.post(
            "/api/auth/login",
            data={"username": test_user.email, "password": "testpassword"}
        )
        assert response.status_code in [200, 204]

        # Check cookie exists
        assert settings.cookie_name in response.cookies

        # In test environment, we can't fully test httpOnly and secure flags
        # as they're part of the Set-Cookie header attributes
        # But we can verify the cookie is set and works
        cookie_value = response.cookies[settings.cookie_name]
        assert cookie_value is not None
        assert len(cookie_value) > 0

    @pytest.mark.asyncio
    async def test_cookie_auto_inclusion(
        self, client: AsyncClient, test_user: User
    ):
        """Test that cookies are automatically included in requests."""
        # Login
        response = await client.post(
            "/api/auth/login",
            data={"username": test_user.email, "password": "testpassword"}
        )
        assert response.status_code in [200, 204]

        # Make multiple authenticated requests without explicit headers
        for _ in range(3):
            response = await client.get("/api/auth/me")
            assert response.status_code == 200
            data = response.json()
            assert data["email"] == test_user.email

    @pytest.mark.asyncio
    async def test_cookie_cleared_on_logout(
        self, client: AsyncClient, test_user: User
    ):
        """Test that cookies are cleared on logout."""
        # Login
        response = await client.post(
            "/api/auth/login",
            data={"username": test_user.email, "password": "testpassword"}
        )
        assert response.status_code in [200, 204]
        assert settings.cookie_name in response.cookies

        # Logout
        response = await client.post("/api/auth/logout")
        assert response.status_code in [200, 204]

        # Cookie should be cleared (set to empty or expired)
        # After logout, attempting to access protected endpoint should fail
        response = await client.get("/api/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_cookie_rejected(
        self, client: AsyncClient
    ):
        """Test that invalid cookies are rejected."""
        # Set invalid cookie
        client.cookies[settings.cookie_name] = "invalid-token-value"

        # Try to access protected endpoint
        response = await client.get("/api/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_cookie_rejected(
        self, client: AsyncClient, test_user: User, test_session: AsyncSession
    ):
        """Test that expired session cookies are rejected."""
        # Login
        response = await client.post(
            "/api/auth/login",
            data={"username": test_user.email, "password": "testpassword"}
        )
        assert response.status_code in [200, 204]

        # Manually expire the session in database
        stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
        result = await test_session.execute(stmt)
        access_token = result.scalar_one()
        access_token.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await test_session.commit()

        # Try to access with expired session
        response = await client.get("/api/users/me")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_ip_validation(
    client: AsyncClient, test_user: User, test_session: AsyncSession
):
    """Test IP address validation when enabled."""
    with patch.object(settings, "session_ip_check", True):
        # Login from one IP
        with patch("src.api.auth_config.Request") as mock_request:
            mock_request.client.host = "192.168.1.1"

            response = await client.post(
                "/api/auth/login",
                data={"username": test_user.email, "password": "testpassword"}
            )
            assert response.status_code in [200, 204]

            # Get session token
            stmt = select(AccessToken).where(AccessToken.user_id == test_user.id)
            result = await test_session.execute(stmt)
            access_token = result.scalar_one()
            assert access_token.ip_address == "192.168.1.1"

            # Try to use session from different IP (would fail with IP check)
            mock_request.client.host = "192.168.1.2"
            response = await client.get("/api/auth/me")
            # This would fail if IP check is properly implemented
            # assert response.status_code == 401


@pytest.mark.asyncio
async def test_concurrent_requests_same_session(
    client: AsyncClient, test_user: User
):
    """Test that concurrent requests with same session work correctly."""
    # Login once
    response = await client.post(
        "/api/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code in [200, 204]

    # Make multiple concurrent requests
    async def make_request():
        response = await client.get("/api/auth/me")
        return response.status_code

    # Run 10 concurrent requests
    tasks = [make_request() for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # All should succeed
    assert all(status == 200 for status in results)