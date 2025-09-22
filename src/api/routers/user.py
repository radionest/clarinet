"""
Async user router for the Clarinet framework with enhanced dependency injection.

This module provides async API endpoints for user management, authentication, and role assignment.
"""

from uuid import UUID

from fastapi import APIRouter, status

from src.api.dependencies import (
    CurrentUserDep,
    PaginationDep,
    UserServiceDep,
)
from src.models import User, UserRead, UserRole

router = APIRouter(tags=["users"])


# Authentication endpoints
@router.get("/me", response_model=UserRead)
async def get_current_user_me(
    current_user: CurrentUserDep,
) -> User:
    """Get current user from authentication."""
    return current_user


@router.get("/me/roles", response_model=list[UserRole])
async def get_my_roles(
    current_user: CurrentUserDep,
    service: UserServiceDep,
) -> list[UserRole]:
    """Get roles for the current user."""
    return await service.get_user_roles(current_user.id)


# Role management endpoints (must come before /{user_id} routes)
@router.get("/roles/{role_name}", response_model=UserRole)
async def get_role_details(
    role_name: str,
    service: UserServiceDep,
) -> UserRole:
    """Get role details by name."""
    return await service.get_role(role_name)


@router.post("/roles", response_model=UserRole, status_code=status.HTTP_201_CREATED)
async def create_role(
    new_role: UserRole,
    service: UserServiceDep,
) -> UserRole:
    """Create a new role."""
    return await service.create_role(name=new_role.name)


# User CRUD endpoints
@router.get("/", response_model=list[UserRead])
async def list_users(
    service: UserServiceDep,
    pagination: PaginationDep,
) -> list[User]:
    """List all users with pagination."""
    return await service.list_users(pagination.skip, pagination.limit)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: UUID,
    service: UserServiceDep,
) -> User:
    """Get user by ID."""
    return await service.get_user(user_id)


@router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    user: User,
    service: UserServiceDep,
) -> User:
    """Create a new user."""
    user_data = user.model_dump()
    return await service.create_user(user_data)


@router.put("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    user_update: User,
    service: UserServiceDep,
) -> User:
    """Update a user's information."""
    update_data = user_update.model_dump(exclude_unset=True)
    return await service.update_user(user_id, update_data)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    service: UserServiceDep,
) -> None:
    """Delete a user by ID."""
    await service.delete_user(user_id)


@router.get("/{user_id}/roles", response_model=list[UserRole])
async def get_user_roles(
    user_id: UUID,
    service: UserServiceDep,
) -> list[UserRole]:
    """Get roles for a specific user."""
    return await service.get_user_roles(user_id)


@router.post("/{user_id}/roles/{role_name}", response_model=UserRead)
async def add_user_role(
    user_id: UUID,
    role_name: str,
    service: UserServiceDep,
) -> User:
    """Assign a role to a user."""
    return await service.assign_role(user_id, role_name)


@router.delete("/{user_id}/roles/{role_name}", response_model=UserRead)
async def remove_user_role(
    user_id: UUID,
    role_name: str,
    service: UserServiceDep,
) -> User:
    """Remove a role from a user."""
    return await service.remove_role(user_id, role_name)


# User activation endpoints
@router.post("/{user_id}/activate", response_model=UserRead)
async def activate_user(
    user_id: UUID,
    service: UserServiceDep,
) -> User:
    """Activate a user account."""
    return await service.activate_user(user_id)


@router.post("/{user_id}/deactivate", response_model=UserRead)
async def deactivate_user(
    user_id: UUID,
    service: UserServiceDep,
) -> User:
    """Deactivate a user account."""
    return await service.deactivate_user(user_id)
