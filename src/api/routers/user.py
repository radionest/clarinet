"""
Async user router for the Clarinet framework.

This module provides async API endpoints for user management, authentication, and role assignment.
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.api.dependencies import get_current_user_async, get_current_user_cookie_async
from src.api.security import get_password_hash, verify_password
from src.exceptions import CONFLICT, NOT_FOUND, UNAUTHORIZED
from src.models import User, UserRead, UserRole
from src.utils.async_crud import (
    add_item_async,
    exists_async,
    get_item_async,
)
from src.utils.database import get_async_session

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_current_user_cookie(
    user: Annotated[User, Depends(get_current_user_cookie_async)],
) -> User:
    """Get current user from cookie authentication."""
    return user


@router.get("/me/token", response_model=UserRead)
async def get_current_user(
    user: Annotated[User, Depends(get_current_user_async)],
) -> User:
    """Get current user from token authentication."""
    return user


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: str, session: AsyncSession = Depends(get_async_session)) -> User:
    """Get user by ID."""
    user = await session.get(User, user_id)
    if not user:
        raise UNAUTHORIZED
    return user


@router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def add_user(user: User, session: AsyncSession = Depends(get_async_session)) -> User:
    """Create a new user."""
    # Check if user already exists
    if await exists_async(User, session, id=user.id):
        raise CONFLICT.with_context(f"User {user.id} already exists")

    new_user = User.model_validate(user)
    new_user.password = get_password_hash(user.password)

    return await add_item_async(new_user, session)


@router.get("/roles/{role_name}", response_model=UserRole)
async def get_role_details(
    role_name: str, session: AsyncSession = Depends(get_async_session)
) -> UserRole:
    """Get role details by name."""
    role = await session.get(UserRole, role_name)
    if not role:
        raise NOT_FOUND.with_context(f"Role '{role_name}' not found")
    return role


@router.post("/roles", response_model=UserRole, status_code=status.HTTP_201_CREATED)
async def create_role(
    new_role: UserRole, session: AsyncSession = Depends(get_async_session)
) -> UserRole:
    """Create a new role."""
    # Check if role already exists
    if await exists_async(UserRole, session, name=new_role.name):
        raise CONFLICT.with_context(f"Role '{new_role.name}' already exists")

    return await add_item_async(new_role, session)


@router.post("/{user_id}/roles/{role_name}", response_model=UserRead)
async def add_user_role(
    user_id: str,
    role_name: str,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Assign a role to a user."""
    user = await get_item_async(User, user_id, session)
    role = await get_item_async(UserRole, role_name, session)

    # Load roles relationship
    await session.refresh(user, ["roles"])

    if role in user.roles:
        raise CONFLICT.with_context(f"User '{user_id}' already has role '{role_name}'")

    user.roles.append(role)
    await session.commit()
    await session.refresh(user)
    return user


@router.get("/me/roles", response_model=list[UserRole])
async def get_my_roles(
    user: User = Depends(get_current_user_async), session: AsyncSession = Depends(get_async_session)
) -> list[UserRole]:
    """Get roles for the current user."""
    # Refresh user with roles loaded
    await session.refresh(user, ["roles"])
    return user.roles


@router.get("/{user_id}/roles", response_model=list[UserRole])
async def get_user_roles(
    user_id: str, session: AsyncSession = Depends(get_async_session)
) -> list[UserRole]:
    """Get roles for a specific user."""
    user = await get_item_async(User, user_id, session)
    # Refresh user with roles loaded
    await session.refresh(user, ["roles"])
    return user.roles


async def authenticate_user_async(username: str, password: str, session: AsyncSession) -> User:
    """Authenticate a user with username and password asynchronously."""
    try:
        user = await get_item_async(User, username, session)
    except HTTPException as e:
        raise UNAUTHORIZED from e

    if not verify_password(password, user.password):
        raise UNAUTHORIZED
    return user


@router.get("/", response_model=list[UserRead])
async def list_users(
    session: AsyncSession = Depends(get_async_session), skip: int = 0, limit: int = 100
) -> Sequence[User]:
    """List all users with pagination."""
    statement = select(User).offset(skip).limit(limit)
    result = await session.execute(statement)
    return result.scalars().all()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, session: AsyncSession = Depends(get_async_session)) -> None:
    """Delete a user by ID."""
    from src.utils.async_crud import delete_item_async

    user = await get_item_async(User, user_id, session)
    await delete_item_async(user, session)


@router.put("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: str, user_update: User, session: AsyncSession = Depends(get_async_session)
) -> User:
    """Update a user's information."""
    from src.utils.async_crud import update_item_async

    user = await get_item_async(User, user_id, session)

    update_data = user_update.model_dump(exclude_unset=True)

    # Hash password if it's being updated
    if "password" in update_data:
        update_data["password"] = get_password_hash(update_data["password"])

    return await update_item_async(user, update_data, session)
