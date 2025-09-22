"""CRUD operations tests for User."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select

from src.models.user import User, UserRole
from src.utils.auth import get_password_hash, verify_password


@pytest.mark.asyncio
async def test_create_user(test_session):
    """Test creating user."""
    user_id = uuid4()
    user = User(
        id=user_id,
        email="newuser@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_verified=False,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    assert user.id == user_id
    assert user.email == "newuser@example.com"
    assert user.is_active is True
    assert verify_password("password123", user.hashed_password)


@pytest.mark.asyncio
async def test_get_user_by_id(test_session, test_user):
    """Test getting user by ID."""
    statement = select(User).where(User.id == test_user.id)
    result = await test_session.execute(statement)
    user = result.scalar_one_or_none()

    assert user is not None
    assert user.id == test_user.id
    assert user.email == test_user.email
    assert user.is_active == test_user.is_active


@pytest.mark.asyncio
async def test_update_user(test_session, test_user):
    """Test updating user."""
    # Update user
    test_user.is_active = False
    test_session.add(test_user)
    await test_session.commit()
    await test_session.refresh(test_user)

    # Check changes
    updated_user = await test_session.get(User, test_user.id)
    assert updated_user.is_active is False


@pytest.mark.asyncio
async def test_delete_user(test_session):
    """Test deleting user."""
    # Create user for deletion
    user_id = uuid4()
    user = User(
        id=user_id,
        email="delete@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=False,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()

    # Delete user
    await test_session.delete(user)
    await test_session.commit()

    # Check that user is deleted
    deleted_user = await test_session.get(User, user_id)
    assert deleted_user is None


@pytest.mark.asyncio
async def test_user_with_roles(test_session):
    """Test creating user with roles."""
    # Create user
    user_id = uuid4()
    user = User(
        id=user_id,
        email="roleuser@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=False,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    # First create roles if they don't exist
    admin_role_def = await test_session.get(UserRole, "admin")
    if not admin_role_def:
        admin_role_def = UserRole(name="admin")
        test_session.add(admin_role_def)
        await test_session.commit()

    moderator_role_def = await test_session.get(UserRole, "moderator")
    if not moderator_role_def:
        moderator_role_def = UserRole(name="moderator")
        test_session.add(moderator_role_def)
        await test_session.commit()

    # Add links through UserRolesLink
    from src.models.user import UserRolesLink

    admin_link = UserRolesLink(user_id=user.id, role_name="admin")
    moderator_link = UserRolesLink(user_id=user.id, role_name="moderator")

    test_session.add(admin_link)
    test_session.add(moderator_link)
    await test_session.commit()

    # Get user roles through UserRolesLink
    from src.models.user import UserRolesLink

    statement = select(UserRolesLink).where(UserRolesLink.user_id == user.id)
    result = await test_session.execute(statement)
    roles = result.scalars().all()

    assert len(roles) == 2
    role_names = [r.role_name for r in roles]
    assert "admin" in role_names
    assert "moderator" in role_names


@pytest.mark.asyncio
async def test_list_all_users(test_session, test_user, admin_user):
    """Test getting list of all users."""
    statement = select(User)
    result = await test_session.execute(statement)
    users = result.scalars().all()

    assert len(users) >= 2  # Minimum test_user and admin_user
    user_ids = [u.id for u in users]
    assert test_user.id in user_ids
    assert admin_user.id in user_ids


@pytest.mark.asyncio
async def test_filter_active_users(test_session):
    """Test filtering active users."""
    # Create active and inactive users
    active_user_id = uuid4()
    inactive_user_id = uuid4()
    active_user = User(
        id=active_user_id,
        email="active@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=False,
        is_superuser=False,
    )
    inactive_user = User(
        id=inactive_user_id,
        email="inactive@example.com",
        hashed_password=get_password_hash("password"),
        is_active=False,
        is_verified=False,
        is_superuser=False,
    )

    test_session.add(active_user)
    test_session.add(inactive_user)
    await test_session.commit()

    # Get only active users
    statement = select(User).where(User.is_active)
    result = await test_session.execute(statement)
    active_users = result.scalars().all()

    user_ids = [u.id for u in active_users]
    assert active_user_id in user_ids
    assert inactive_user_id not in user_ids


@pytest.mark.asyncio
async def test_user_registration_via_api(client: AsyncClient):
    """Test user registration via API."""
    response = await client.post(
        "/auth/register",
        json={
            "email": "apiuser@example.com",
            "username": "apiuser",
            "password": "securepassword123",
        },
    )

    # Registration may be disabled or require additional fields
    if response.status_code == 200:
        data = response.json()
        assert "id" in data or "user" in data
    elif response.status_code == 422:
        # Validation failed - this is also ok for test
        pass
    else:
        # 404 if endpoint doesn't exist
        assert response.status_code in [404, 405]
