"""Service layer for user business logic."""

from uuid import UUID, uuid4

from clarinet.api.auth_config import DatabaseStrategy
from clarinet.exceptions.domain import (
    InvalidCredentialsError,
    RoleAlreadyExistsError,
    UserAlreadyExistsError,
    UserAlreadyHasRoleError,
)
from clarinet.models import User, UserCreate, UserRole, UserUpdate
from clarinet.repositories.user_repository import UserRepository
from clarinet.utils.auth import get_password_hash, verify_password


class UserService:
    """Service for user-related business logic."""

    def __init__(self, user_repo: UserRepository):
        """Initialize user service with repository.

        Args:
            user_repo: User repository instance
        """
        self.user_repo = user_repo

    async def get_user(self, user_id: UUID) -> User:
        """Get user by ID with roles eagerly loaded.

        Roles are loaded so that ``UserRead.role_names`` is populated correctly
        when this user is serialised — the computed field reads ``__dict__`` to
        avoid lazy-load failures, so the data must be present at fetch time.

        Args:
            user_id: User ID

        Returns:
            User object with roles loaded

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        return await self.user_repo.get_with_roles(user_id)

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

    async def create_user(self, data: UserCreate) -> User:
        """Create new user with hashed password.

        Returns the user with `roles` eagerly loaded (empty for new accounts)
        so `UserRead.role_names` serialises consistently.

        Raises:
            UserAlreadyExistsError: If user with this email already exists
        """
        if await self.user_repo.find_by_email(data.email) is not None:
            raise UserAlreadyExistsError(data.email)

        user = User(
            id=uuid4(),
            email=data.email,
            hashed_password=get_password_hash(data.password),
            is_active=data.is_active if data.is_active is not None else True,
            is_superuser=data.is_superuser if data.is_superuser is not None else False,
            is_verified=data.is_verified if data.is_verified is not None else False,
        )
        created = await self.user_repo.create(user)
        return await self.user_repo.get_with_roles(created.id)

    async def update_user(self, user_id: UUID, data: UserUpdate) -> User:
        """Update user information.

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        user = await self.user_repo.get(user_id)

        update_fields = data.model_dump(exclude_unset=True, exclude={"password"})

        if data.password is not None:
            update_fields["hashed_password"] = get_password_hash(data.password)

        await self.user_repo.update(user, update_fields)
        return await self.user_repo.get_with_roles(user_id)

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
        """List all users with pagination, roles eagerly loaded.

        See ``get_user`` for why eager loading is required.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of users with roles loaded
        """
        return await self.user_repo.get_all_with_roles(skip=skip, limit=limit)

    async def list_roles(self) -> list[UserRole]:
        """List all available roles.

        Returns:
            List of all roles
        """
        return await self.user_repo.get_all_roles()

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

        Invalidates the auth-flow user cache so the new role takes effect on
        the next request instead of waiting up to ``session_cache_ttl_seconds``.

        Args:
            user_id: User ID
            role_name: Role name to assign

        Returns:
            Updated user with new role and `roles` eagerly loaded

        Raises:
            EntityNotFoundError: If user or role doesn't exist
            UserAlreadyHasRoleError: If user already has the role
        """
        user = await self.user_repo.get(user_id)
        role = await self.user_repo.get_role(role_name)

        # Check if already has role
        if await self.user_repo.has_role(user, role_name):
            raise UserAlreadyHasRoleError(user_id, role_name)

        await self.user_repo.add_role(user, role)
        DatabaseStrategy.invalidate_user_cache(user_id)
        return await self.user_repo.get_with_roles(user_id)

    async def remove_role(self, user_id: UUID, role_name: str) -> User:
        """Remove role from user.

        Invalidates the auth-flow user cache so the demotion takes effect
        immediately instead of after the TTL expires.

        Args:
            user_id: User ID
            role_name: Role name to remove

        Returns:
            Updated user without the role and `roles` eagerly loaded

        Raises:
            EntityNotFoundError: If user or role doesn't exist
        """
        user = await self.user_repo.get(user_id)
        role = await self.user_repo.get_role(role_name)

        await self.user_repo.remove_role(user, role)
        DatabaseStrategy.invalidate_user_cache(user_id)
        return await self.user_repo.get_with_roles(user_id)

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
        if await self.user_repo.role_exists(name):
            raise RoleAlreadyExistsError(name)

        role = UserRole(name=name)
        return await self.user_repo.create_role(role)

    async def get_role(self, name: str) -> UserRole:
        """Get role by name.

        Args:
            name: Role name

        Returns:
            Role object

        Raises:
            EntityNotFoundError: If role doesn't exist
        """
        return await self.user_repo.get_role(name)

    async def activate_user(self, user_id: UUID) -> User:
        """Activate user account.

        Args:
            user_id: User ID

        Returns:
            Activated user with `roles` eagerly loaded

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        user = await self.user_repo.get(user_id)
        await self.user_repo.activate(user)
        return await self.user_repo.get_with_roles(user_id)

    async def deactivate_user(self, user_id: UUID) -> User:
        """Deactivate user account.

        Invalidates the auth-flow user cache so the deactivation takes effect
        immediately — otherwise the next request within the TTL would still
        accept the now-disabled session.

        Args:
            user_id: User ID

        Returns:
            Deactivated user with `roles` eagerly loaded

        Raises:
            EntityNotFoundError: If user doesn't exist
        """
        user = await self.user_repo.get(user_id)
        await self.user_repo.deactivate(user)
        DatabaseStrategy.invalidate_user_cache(user_id)
        return await self.user_repo.get_with_roles(user_id)
