"""Repository for User-specific database operations."""

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from clarinet.exceptions.domain import UserNotFoundError
from clarinet.models import Record, User, UserRole
from clarinet.repositories.base import BaseRepository
from clarinet.services.events.models import EntityEvent
from clarinet.utils.session import revoke_user_sessions


class UserRepository(BaseRepository[User]):
    """Repository for User model operations."""

    def __init__(self, session: AsyncSession):
        """Initialize user repository with session."""
        super().__init__(session, User)
        self._role_repo = BaseRepository(session, UserRole)

    async def clear_owned_records(self, user_id: UUID) -> list[EntityEvent]:
        """Null ``Record.user_id`` for every record owned by *user_id*; leaves status untouched.

        Returns one record ``updated`` event per cleared record for the caller
        to publish after its commit — the Core UPDATE is invisible to the SSE
        capture, which the ORM nullify it replaces was not.

        Run before ``session.delete(user)`` in ``UserService.delete_user``:
        without this, SQLAlchemy's own FK-nullify-on-parent-delete cascade
        would set ``Record.user_id`` on each owned record through the mapped
        attribute during flush, tripping the direct-lifecycle-write guard in
        ``models/record.py`` (only ``RecordRepository.write_transition`` may
        write ``status``/``user_id`` on a saved record). A Core UPDATE with
        ``synchronize_session=False`` bypasses that attribute entirely — the
        same technique ``write_transition`` itself uses.
        """
        # ponytail: this only pre-empts SQLAlchemy's FK-nullify cascade while
        # ``User.records`` stays unloaded on the deleted user — any other
        # delete path (e.g. ``clarinet/utils/fastapi_users_db.py``'s delete)
        # would still load/touch the relationship and hit the direct-write
        # guard. Durable fix: ``ForeignKey("user.id", ondelete="SET NULL")`` +
        # ``passive_deletes=True`` on the relationship (needs a migration).
        owned = await self.session.execute(
            select(Record.id, Record.record_type_name).where(col(Record.user_id) == user_id)
        )
        await self.session.execute(
            update(Record)
            .where(col(Record.user_id) == user_id)
            .values(user_id=None)
            .execution_options(synchronize_session=False)
        )
        return [
            EntityEvent(entity="record", action="updated", id=str(rid), record_type_name=rtn)
            for rid, rtn in owned.all()
        ]

    async def get_with_roles(self, user_id: UUID) -> User:
        """Get user with roles loaded.

        Args:
            user_id: User ID

        Returns:
            User with roles loaded

        Raises:
            UserNotFoundError: If user doesn't exist
        """
        user = await self.get_optional(user_id)
        if user is None:
            raise UserNotFoundError(user_id)
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
        """Get all users with their roles loaded in a single query.

        Uses ``selectinload`` so a paginated list of N users issues 2 SQL
        statements (users + a single batched roles fetch) instead of 1 + N
        sequential round-trips on a shared ``AsyncSession``.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of users with roles
        """
        stmt = (
            select(User)
            .options(selectinload(User.roles))  # type: ignore[arg-type]
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_with_roles(self) -> list[User]:
        """All active users with roles eagerly loaded, unbounded.

        Like ``get_all_with_roles`` but filtered to active users and without
        pagination — the admin workload table must list every active user.
        """
        stmt = (
            select(User).where(col(User.is_active).is_(True)).options(selectinload(User.roles))  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_role(self, role_name: str) -> list[User]:
        """Find all users with a specific role.

        Args:
            role_name: Name of the role

        Returns:
            List of users with the specified role
        """
        from clarinet.models.user import UserRolesLink

        statement = select(User).join(UserRolesLink).where(UserRolesLink.role_name == role_name)
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update_password(self, user: User, hashed_password: str) -> User:
        """Set a new password hash and revoke all of the user's sessions (#651).

        One commit covers both: ``revoke_user_sessions`` commits the pending
        hash together with its DELETE, so a failed revoke leaves the old
        password in place rather than a new password with the old sessions
        still valid.

        Args:
            user: User to update
            hashed_password: New hashed password

        Returns:
            Updated user
        """
        user.hashed_password = hashed_password
        await revoke_user_sessions(self.session, user.id)
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

    async def get_role(self, name: str) -> UserRole:
        """Get role by name or raise EntityNotFoundError.

        Args:
            name: Role name

        Returns:
            Role object

        Raises:
            EntityNotFoundError: If role doesn't exist
        """
        return await self._role_repo.get(name)

    async def get_all_roles(self, skip: int = 0, limit: int = 100) -> list[UserRole]:
        """Get all available roles.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of roles
        """
        return list(await self._role_repo.get_all(skip=skip, limit=limit))

    async def role_exists(self, name: str) -> bool:
        """Check if role exists.

        Args:
            name: Role name

        Returns:
            True if role exists
        """
        return await self._role_repo.exists(name=name)

    async def create_role(self, role: UserRole) -> UserRole:
        """Create new role.

        Args:
            role: Role to create

        Returns:
            Created role
        """
        return await self._role_repo.create(role)
