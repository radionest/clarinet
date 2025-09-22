"""Repository for User-specific database operations."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.models import User, UserRole
from src.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User model operations."""

    def __init__(self, session: AsyncSession):
        """Initialize user repository with session."""
        super().__init__(session, User)

    async def get_with_roles(self, user_id: UUID) -> User:
        """Get user with roles loaded.

        Args:
            user_id: User ID

        Returns:
            User with roles loaded

        Raises:
            NOT_FOUND: If user doesn't exist
        """
        user = await self.get(user_id)
        await self.session.refresh(user, ["roles"])
        return user

    async def find_by_username(self, username: str) -> User | None:
        """Find user by username.

        Args:
            username: Username to search for

        Returns:
            User if found, None otherwise
        """
        return await self.get_by(username=username)

    async def find_by_email(self, email: str) -> User | None:
        """Find user by email.

        Args:
            email: Email to search for

        Returns:
            User if found, None otherwise
        """
        return await self.get_by(email=email)

    async def add_role(self, user: User, role: UserRole) -> User:
        """Add role to user.

        Args:
            user: User to add role to
            role: Role to add

        Returns:
            Updated user with new role
        """
        await self.session.refresh(user, ["roles"])
        if role not in user.roles:
            user.roles.append(role)
            await self.session.commit()
            await self.session.refresh(user)
        return user

    async def remove_role(self, user: User, role: UserRole) -> User:
        """Remove role from user.

        Args:
            user: User to remove role from
            role: Role to remove

        Returns:
            Updated user without the role
        """
        await self.session.refresh(user, ["roles"])
        if role in user.roles:
            user.roles.remove(role)
            await self.session.commit()
            await self.session.refresh(user)
        return user

    async def has_role(self, user: User, role_name: str) -> bool:
        """Check if user has a specific role.

        Args:
            user: User to check
            role_name: Name of the role to check

        Returns:
            True if user has the role
        """
        await self.session.refresh(user, ["roles"])
        return any(role.name == role_name for role in user.roles)

    async def get_all_with_roles(self, skip: int = 0, limit: int = 100) -> list[User]:
        """Get all users with their roles loaded.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of users with roles
        """
        users = await self.get_all(skip=skip, limit=limit)
        for user in users:
            await self.session.refresh(user, ["roles"])
        return list(users)

    async def find_by_role(self, role_name: str) -> list[User]:
        """Find all users with a specific role.

        Args:
            role_name: Name of the role

        Returns:
            List of users with the specified role
        """
        from src.models.user import UserRolesLink

        statement = select(User).join(UserRolesLink).where(UserRolesLink.role_name == role_name)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update_password(self, user: User, hashed_password: str) -> User:
        """Update user's password.

        Args:
            user: User to update
            hashed_password: New hashed password

        Returns:
            Updated user
        """
        user.hashed_password = hashed_password
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def activate(self, user: User) -> User:
        """Activate user account.

        Args:
            user: User to activate

        Returns:
            Activated user
        """
        user.is_active = True
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def deactivate(self, user: User) -> User:
        """Deactivate user account.

        Args:
            user: User to deactivate

        Returns:
            Deactivated user
        """
        user.is_active = False
        await self.session.commit()
        await self.session.refresh(user)
        return user


class UserRoleRepository(BaseRepository[UserRole]):
    """Repository for UserRole model operations."""

    def __init__(self, session: AsyncSession):
        """Initialize user role repository with session."""
        super().__init__(session, UserRole)

    async def find_by_name(self, name: str) -> UserRole | None:
        """Find role by name.

        Args:
            name: Role name

        Returns:
            Role if found, None otherwise
        """
        return await self.get_by(name=name)

    async def get_users_for_role(self, role: UserRole) -> list[User]:
        """Get all users with a specific role.

        Args:
            role: Role to search for

        Returns:
            List of users with the role
        """
        await self.session.refresh(role, ["users"])
        return list(role.users)
