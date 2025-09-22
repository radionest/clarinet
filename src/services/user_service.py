"""Service layer for user business logic."""

from typing import Any
from uuid import UUID

from src.exceptions.domain import (
    InvalidCredentialsError,
    RoleAlreadyExistsError,
    UserAlreadyExistsError,
    UserAlreadyHasRoleError,
)
from src.models import User, UserRole
from src.repositories.user_repository import UserRepository, UserRoleRepository
from src.utils.auth import get_password_hash, verify_password


class UserService:
    """Service for user-related business logic."""

    def __init__(self, user_repo: UserRepository, role_repo: UserRoleRepository):
        """Initialize user service with repositories.

        Args:
            user_repo: User repository instance
            role_repo: User role repository instance
        """
        self.user_repo = user_repo
        self.role_repo = role_repo

    async def get_user(self, user_id: UUID) -> User:
        """Get user by ID.

        Args:
            user_id: User ID

        Returns:
            User object

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        return await self.user_repo.get(user_id)

    async def get_user_with_roles(self, user_id: UUID) -> User:
        """Get user with roles loaded.

        Args:
            user_id: User ID

        Returns:
            User with roles

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        return await self.user_repo.get_with_roles(user_id)

    async def create_user(self, user_data: dict[str, Any]) -> User:
        """Create new user with hashed password.

        Args:
            user_data: User data dictionary

        Returns:
            Created user

        Raises:
            UserAlreadyExistsError: If user already exists
        """
        # Check if user exists
        if await self.user_repo.exists(id=user_data["id"]):
            raise UserAlreadyExistsError(user_data["id"])

        # Hash password if provided
        if "password" in user_data:
            user_data["hashed_password"] = get_password_hash(user_data.pop("password"))
        elif "hashed_password" in user_data:
            user_data["hashed_password"] = get_password_hash(user_data["hashed_password"])

        # Create user
        user = User(**user_data)
        return await self.user_repo.create(user)

    async def update_user(self, user_id: UUID, update_data: dict[str, Any]) -> User:
        """Update user information.

        Args:
            user_id: User ID
            update_data: Dictionary with fields to update

        Returns:
            Updated user

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        user = await self.user_repo.get(user_id)

        # Hash password if being updated
        if "password" in update_data:
            update_data["hashed_password"] = get_password_hash(update_data.pop("password"))
        elif "hashed_password" in update_data:
            update_data["hashed_password"] = get_password_hash(update_data["hashed_password"])

        return await self.user_repo.update(user, update_data)

    async def delete_user(self, user_id: UUID) -> None:
        """Delete user.

        Args:
            user_id: User ID to delete

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        user = await self.user_repo.get(user_id)
        await self.user_repo.delete(user)

    async def authenticate(self, username: str, password: str) -> User:
        """Authenticate user with username and password.

        Args:
            username: Username
            password: Plain text password

        Returns:
            Authenticated user

        Raises:
            InvalidCredentialsError: If authentication fails
        """
        # Try to get user by username (which is the ID in this case)
        user: User | None = None
        try:
            user = await self.user_repo.get(username)
        except Exception:
            # If not found by ID, try by username field
            user = await self.user_repo.find_by_username(username)

        if user is None or not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError()

        return user

    async def list_users(self, skip: int = 0, limit: int = 100) -> list[User]:
        """List all users with pagination.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of users
        """
        users = await self.user_repo.get_all(skip=skip, limit=limit)
        return list(users)

    async def get_user_roles(self, user_id: UUID) -> list[UserRole]:
        """Get roles for a user.

        Args:
            user_id: User ID

        Returns:
            List of user roles

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        user = await self.user_repo.get_with_roles(user_id)
        return user.roles

    async def assign_role(self, user_id: UUID, role_name: str) -> User:
        """Assign role to user.

        Args:
            user_id: User ID
            role_name: Role name to assign

        Returns:
            Updated user with new role

        Raises:
            EntityNotFoundError: If user or role doesn't exist
            UserAlreadyHasRoleError: If user already has the role
        """
        user = await self.user_repo.get(user_id)
        role = await self.role_repo.get(role_name)

        # Check if already has role
        if await self.user_repo.has_role(user, role_name):
            raise UserAlreadyHasRoleError(user_id, role_name)

        return await self.user_repo.add_role(user, role)

    async def remove_role(self, user_id: UUID, role_name: str) -> User:
        """Remove role from user.

        Args:
            user_id: User ID
            role_name: Role name to remove

        Returns:
            Updated user without the role

        Raises:
            EntityNotFoundError: If user or role doesn't exist
        """
        user = await self.user_repo.get(user_id)
        role = await self.role_repo.get(role_name)

        return await self.user_repo.remove_role(user, role)

    async def create_role(self, name: str) -> UserRole:
        """Create new role.

        Args:
            name: Role name string

        Returns:
            Created role

        Raises:
            RoleAlreadyExistsError: If role already exists
        """
        # Check if role exists
        if await self.role_repo.exists(name=name):
            raise RoleAlreadyExistsError(name)

        role = UserRole(name=name)
        return await self.role_repo.create(role)

    async def get_role(self, name: str) -> UserRole:
        """Get role by name.

        Args:
            name: Role name

        Returns:
            Role object

        Raises:
            EntityNotFoundError: If role doesn't exist
        """
        return await self.role_repo.get(name)

    async def activate_user(self, user_id: UUID) -> User:
        """Activate user account.

        Args:
            user_id: User ID

        Returns:
            Activated user

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        user = await self.user_repo.get(user_id)
        return await self.user_repo.activate(user)

    async def deactivate_user(self, user_id: UUID) -> User:
        """Deactivate user account.

        Args:
            user_id: User ID

        Returns:
            Deactivated user

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        user = await self.user_repo.get(user_id)
        return await self.user_repo.deactivate(user)
