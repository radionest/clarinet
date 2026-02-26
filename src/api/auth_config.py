"""
Fastapi-users configuration for session-based authentication.
Following KISS principle - minimal configuration.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID, uuid4

from cachetools import TTLCache
from fastapi import Depends, Request, Response
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    Strategy,
)
from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from src.models.auth import AccessToken
from src.models.user import User
from src.settings import settings
from src.utils.database import get_async_session
from src.utils.fastapi_users_db import SQLModelUserDatabaseAsync
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
) -> AsyncGenerator[SQLModelUserDatabaseAsync]:
    """Get user database."""
    yield SQLModelUserDatabaseAsync(session, User)


async def get_user_manager(
    user_db: SQLModelUserDatabaseAsync = Depends(get_user_db),
) -> AsyncGenerator[UserManager]:
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


# Enhanced database session storage strategy with lifecycle management
class DatabaseStrategy(Strategy[User, UUID]):
    """Enhanced database strategy with session lifecycle management."""

    _user_cache: ClassVar[TTLCache] = TTLCache(
        maxsize=1000,
        ttl=max(settings.session_cache_ttl_seconds, 1),
    )

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        """Initialize strategy with database session and optional request."""
        self.session = session
        self.request = request

    async def write_token(self, user: User) -> str:
        """Create new session with lifecycle management."""
        # Check concurrent session limit
        if settings.session_concurrent_limit > 0:
            await self._enforce_session_limit(user.id)

        token = str(uuid4())
        expires_at = datetime.now(UTC) + timedelta(hours=settings.session_expire_hours)

        # Extract request metadata
        user_agent = None
        ip_address = None
        if self.request:
            user_agent = self.request.headers.get("User-Agent", "")[:512]
            if self.request.client:
                ip_address = self.request.client.host

        access_token = AccessToken(
            token=token,
            user_id=user.id,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )

        self.session.add(access_token)
        await self.session.commit()

        logger.info(
            f"Session created for user {user.id}",
            extra={
                "user_id": str(user.id),
                "expires_at": expires_at.isoformat(),
                "ip_address": ip_address,
            },
        )

        return token

    async def read_token(
        self, token: str | None, user_manager: BaseUserManager[User, UUID]
    ) -> User | None:  # type: ignore[override]
        """Validate token with comprehensive checks and in-memory caching."""
        del user_manager  # Unused but required by interface
        if not token:
            return None

        # Check in-memory cache for recent validations
        ttl = settings.session_cache_ttl_seconds
        if ttl > 0 and token in self._user_cache:
            return self._user_cache[token]  # type: ignore[no-any-return]

        # Query token with expiration check
        stmt = select(AccessToken).where(
            AccessToken.token == token,  # type: ignore[arg-type]
            AccessToken.expires_at > datetime.now(UTC),  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        access_token = result.scalar_one_or_none()

        if not access_token:
            logger.debug(f"Token {token[:8]}... not found or expired")
            self._user_cache.pop(token, None)
            return None

        # Optional IP validation
        if settings.session_ip_check and self.request:
            request_ip = self.request.client.host if self.request.client else None
            if access_token.ip_address and request_ip != access_token.ip_address:
                logger.warning(
                    f"IP mismatch for token {token[:8]}...: "
                    f"{request_ip} != {access_token.ip_address}"
                )
                return None

        # Check idle timeout
        if settings.session_idle_timeout_minutes > 0:
            # Ensure last_accessed is timezone-aware
            last_accessed = access_token.last_accessed
            if last_accessed.tzinfo is None:
                last_accessed = last_accessed.replace(tzinfo=UTC)
            idle_duration = datetime.now(UTC) - last_accessed
            max_idle = timedelta(minutes=settings.session_idle_timeout_minutes)
            if idle_duration > max_idle:
                logger.info(f"Session {token[:8]}... expired due to inactivity")
                self._user_cache.pop(token, None)
                return None

        # Update last accessed and optionally refresh
        access_token.last_accessed = datetime.now(UTC)

        if settings.session_sliding_refresh:
            # Ensure expires_at is timezone-aware
            expires_at = access_token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            time_left = expires_at - datetime.now(UTC)
            total_duration = timedelta(hours=settings.session_expire_hours)

            # Refresh if less than 50% time remaining
            if time_left < total_duration / 2:
                new_expiry = datetime.now(UTC) + total_duration

                # Check absolute timeout
                if settings.session_absolute_timeout_days > 0:
                    max_age = timedelta(days=settings.session_absolute_timeout_days)
                    # Ensure created_at is timezone-aware
                    created_at = access_token.created_at
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=UTC)
                    absolute_limit = created_at + max_age
                    new_expiry = min(new_expiry, absolute_limit)

                access_token.expires_at = new_expiry
                logger.debug(f"Extended session {token[:8]}... to {new_expiry.isoformat()}")

        await self.session.commit()

        # Get user
        user_stmt = select(User).where(User.id == access_token.user_id)  # type: ignore[arg-type]
        user_result = await self.session.execute(user_stmt)
        user = user_result.scalar_one_or_none()

        if not user or not user.is_active:
            logger.warning(f"User {access_token.user_id} not found or inactive")
            self._user_cache.pop(token, None)
            return None

        # Cache the validated user (detach from SQLAlchemy session first)
        if ttl > 0:
            self.session.expunge(user)
            self._user_cache[token] = user

        return user  # type: ignore[return-value]

    async def destroy_token(self, token: str, user: User) -> None:
        """Remove session token on logout."""
        self._user_cache.pop(token, None)
        stmt = delete(AccessToken).where(AccessToken.token == token)  # type: ignore[arg-type]
        result: CursorResult[Any] = await self.session.execute(stmt)  # type: ignore[assignment]
        await self.session.commit()

        if result.rowcount > 0:
            logger.info(
                f"Session destroyed for user {user.id}",
                extra={"user_id": str(user.id), "token_preview": token[:8] + "..."},
            )

    async def _enforce_session_limit(self, user_id: UUID) -> None:
        """Enforce maximum concurrent sessions per user."""
        # Count active sessions
        count_stmt = (
            select(func.count())
            .select_from(AccessToken)
            .where(
                AccessToken.user_id == user_id,  # type: ignore[arg-type]
                AccessToken.expires_at > datetime.now(UTC),  # type: ignore[arg-type]
            )
        )
        result = await self.session.execute(count_stmt)
        session_count = result.scalar() or 0

        if session_count >= settings.session_concurrent_limit:
            # Remove oldest sessions
            excess = session_count - settings.session_concurrent_limit + 1

            # Get oldest sessions
            oldest_stmt = (
                select(col(AccessToken.token))
                .where(
                    AccessToken.user_id == user_id,  # type: ignore[arg-type]
                    AccessToken.expires_at > datetime.now(UTC),  # type: ignore[arg-type]
                )
                .order_by(col(AccessToken.created_at))
                .limit(excess)
            )

            result = await self.session.execute(oldest_stmt)
            old_tokens = [row[0] for row in result]

            # Delete them
            if old_tokens:
                delete_stmt = delete(AccessToken).where(
                    AccessToken.token.in_(old_tokens)  # type: ignore[attr-defined]
                )
                await self.session.execute(delete_stmt)
                logger.info(
                    f"Removed {len(old_tokens)} old sessions for user {user_id} "
                    f"(limit: {settings.session_concurrent_limit})"
                )


def get_database_strategy(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> DatabaseStrategy:
    """Get database strategy with request context."""
    return DatabaseStrategy(session, request)


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
