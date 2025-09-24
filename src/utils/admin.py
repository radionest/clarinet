"""
Administrator user management utilities.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.models.user import User
from src.utils.auth import get_password_hash
from src.utils.db_manager import db_manager
from src.utils.logger import logger


async def reset_admin_password(username: str, new_password: str) -> bool:
    """
    Reset the password for an admin user.

    Args:
        username: The admin username (email)
        new_password: The new password to set

    Returns:
        True if password was reset, False otherwise
    """
    async with db_manager.get_async_session_context() as session:
        result = await session.execute(select(User).where(User.email == username))
        user = result.scalar_one_or_none()

        if not user:
            logger.error(f"User '{username}' not found")
            return False

        if not user.is_superuser:
            logger.error(f"User '{username}' is not a superuser")
            return False

        user.hashed_password = get_password_hash(new_password)
        await session.commit()

        logger.info(f"Password reset for user '{username}'")
        return True


async def list_admin_users(session: AsyncSession) -> list[User]:
    """
    List all users with superuser privileges.

    Args:
        session: Database session

    Returns:
        List of admin users
    """
    result = await session.execute(select(User).where(User.is_superuser == True))  # noqa: E712
    return list(result.scalars().all())


async def ensure_admin_exists() -> None:
    """
    Ensure at least one admin user exists in the system.

    Raises:
        RuntimeError: If no admin users exist and creation fails
    """
    async with db_manager.get_async_session_context() as session:
        admins = await list_admin_users(session)

        if not admins:
            logger.warning("No admin users found in system!")
            from src.utils.bootstrap import create_admin_user

            admin = await create_admin_user()
            if not admin:
                raise RuntimeError(
                    "No admin users exist and automatic creation failed. "
                    "System requires at least one admin user."
                )
