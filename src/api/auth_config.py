"""
Fastapi-users configuration for session-based authentication.
Following KISS principle - minimal configuration.
"""

from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

from fastapi import Depends, Request, Response
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    Strategy,
)
from fastapi_users_db_sqlmodel import SQLModelUserDatabaseAsync
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.auth import AccessToken
from src.models.user import User
from src.settings import settings
from src.utils.database import get_async_session
from src.utils.logger import logger


# Minimal UserManager
class UserManager(BaseUserManager[User, UUID]):
    """Minimal user manager - only necessary methods."""

    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    async def on_after_register(
        self,
        user: User,
        request: Request | None = None,
    ) -> None:
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


# No longer need custom SQLModelUserDatabase - use fastapi_users_db_sqlmodel instead


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLModelUserDatabaseAsync, None]:
    """Get user database."""
    yield SQLModelUserDatabaseAsync(session, User)


async def get_user_manager(
    user_db: SQLModelUserDatabaseAsync = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    """Get user manager."""
    yield UserManager(user_db)


# Cookie transport configuration (KISS - only cookies, no tokens)
cookie_transport = CookieTransport(
    cookie_name=settings.cookie_name,
    cookie_max_age=settings.session_expire_seconds,
    cookie_httponly=True,  # Protection from XSS
    cookie_secure=not settings.debug,  # HTTPS in production
    cookie_samesite="lax",  # Protection from CSRF
)


# Database session storage strategy
class DatabaseStrategy(Strategy[User, UUID]):
    """Simple database strategy for session storage."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize strategy with database session."""
        self.session = session

    async def write_token(self, user: User) -> str:
        """Create and store session token."""
        token = str(uuid4())

        # Save to DB through AccessToken
        access_token = AccessToken(
            token=token,
            user_id=user.id,
        )
        self.session.add(access_token)
        await self.session.commit()

        return token

    async def read_token(
        self, token: str | None, user_manager: BaseUserManager[User, UUID]
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
fastapi_users = FastAPIUsers[User, UUID](
    get_user_manager,
    [auth_backend],
)

# Export ready dependencies
current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
optional_current_user = fastapi_users.current_user(optional=True)
