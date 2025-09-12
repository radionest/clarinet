"""
Fastapi-users configuration for session-based authentication.
Following KISS principle - minimal configuration.
"""

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, Request, Response
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    Strategy,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.auth import AccessToken
from src.models.user import User
from src.settings import settings
from src.utils.database import get_async_session
from src.utils.logger import logger


# Minimal UserManager
class UserManager(BaseUserManager[User, str]):
    """Minimal user manager - only necessary methods."""

    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    async def get_by_email(self, user_email: str) -> User:
        """Get user by email for authentication."""
        user = await self.user_db.get_by_email(user_email)
        if user is None:
            raise Exception("User not found")
        # Type narrowing for mypy
        if not isinstance(user, User):
            raise Exception("Invalid user type")
        return user

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        """Called after successful user registration."""
        del request  # Unused but required by interface
        logger.info(f"User {user.id} has registered.")

    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
        response: Response | None = None,
    ) -> None:
        """Called after successful login."""
        del request, response  # Unused but required by interface
        logger.info(f"User {user.id} logged in.")


class SQLModelUserDatabase:
    """Custom user database adapter for SQLModel with string ID."""

    def __init__(self, session: AsyncSession, user_model: type[User]) -> None:
        self.session = session
        self.user_model = user_model

    async def get(self, id: str) -> User | None:
        """Get user by ID."""
        statement = select(self.user_model).where(self.user_model.id == id)  # type: ignore[arg-type]
        results = await self.session.execute(statement)
        return results.scalar_one_or_none()  # type: ignore[return-value]

    async def get_by_email(self, email: str) -> User | None:
        """Get user by email."""
        statement = select(self.user_model).where(self.user_model.email == email)  # type: ignore[arg-type]
        results = await self.session.execute(statement)
        return results.scalar_one_or_none()  # type: ignore[return-value]

    async def create(self, user_dict: dict) -> User:
        """Create user."""
        user = self.user_model(**user_dict)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update(self, user: User, update_dict: dict) -> User:
        """Update user."""
        for key, value in update_dict.items():
            setattr(user, key, value)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def delete(self, user: User) -> None:
        """Delete user."""
        await self.session.delete(user)
        await self.session.commit()


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLModelUserDatabase, None]:
    """Get user database."""
    yield SQLModelUserDatabase(session, User)


async def get_user_manager(
    user_db: SQLModelUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    """Get user manager."""
    yield UserManager(user_db)  # type: ignore[arg-type]


# Cookie transport configuration (KISS - only cookies, no tokens)
cookie_transport = CookieTransport(
    cookie_name=settings.cookie_name,
    cookie_max_age=settings.session_expire_seconds,
    cookie_httponly=True,  # Protection from XSS
    cookie_secure=not settings.debug,  # HTTPS in production
    cookie_samesite="lax",  # Protection from CSRF
)


# Database session storage strategy
class DatabaseStrategy(Strategy[User, str]):
    """Simple database strategy for session storage."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize strategy with database session."""
        self.session = session

    async def write_token(self, user: User) -> str:
        """Create and store session token."""
        token = str(uuid.uuid4())

        # Save to DB through AccessToken
        access_token = AccessToken(
            token=token,
            user_id=user.id,
        )
        self.session.add(access_token)
        await self.session.commit()

        return token

    async def read_token(
        self, token: str | None, user_manager: BaseUserManager[User, str]
    ) -> User | None:  # type: ignore[override]
        """Validate and read session token."""
        del user_manager  # Unused but required by interface
        if not token:
            return None

        # Find token in DB
        statement = select(AccessToken).where(AccessToken.token == token)  # type: ignore[arg-type]
        results = await self.session.execute(statement)
        access_token = results.scalar_one_or_none()

        if not access_token:
            return None

        # Get user
        user_statement = select(User).where(User.id == access_token.user_id)  # type: ignore[arg-type]
        user_results = await self.session.execute(user_statement)
        return user_results.scalar_one_or_none()  # type: ignore[return-value]

    async def destroy_token(self, token: str, user: User) -> None:
        """Remove session token on logout."""
        del user  # Unused but required by interface
        statement = delete(AccessToken).where(AccessToken.token == token)  # type: ignore[arg-type]
        await self.session.execute(statement)
        await self.session.commit()


def get_database_strategy(
    session: AsyncSession = Depends(get_async_session),
) -> DatabaseStrategy:
    """Get database strategy."""
    return DatabaseStrategy(session)


# Create authentication backend
auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_database_strategy,
)

# Create FastAPIUsers instance
fastapi_users = FastAPIUsers[User, str](
    get_user_manager,
    [auth_backend],
)

# Export ready dependencies
current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
optional_current_user = fastapi_users.current_user(optional=True)
