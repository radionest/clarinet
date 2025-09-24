"""
Simplified authentication router using fastapi-users.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from src.api.auth_config import (
    DatabaseStrategy,
    UserManager,
    auth_backend,
    current_active_user,
    fastapi_users,
    get_user_db,
)
from src.models.auth import AccessToken
from src.models.user import User, UserCreate, UserRead
from src.settings import settings
from src.utils.database import get_async_session
from src.utils.logger import logger


class SessionInfo(BaseModel):
    """Information about a user session."""

    token_preview: str
    created_at: datetime
    expires_at: datetime
    last_accessed: datetime
    user_agent: str | None
    ip_address: str | None
    is_current: bool = False


class SessionRefreshResponse(BaseModel):
    """Response for session refresh."""

    expires_at: datetime
    extended_by_hours: int


# Use ready-made routers from fastapi-users
router = APIRouter(tags=["auth"])

# Add standard endpoints (login, logout)
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
)

# User registration
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
)


# Additional endpoints
@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(current_active_user)) -> User:
    """Get current user."""
    return user


@router.get("/session/validate", response_model=UserRead)
async def validate_session(
    request: Request,
    session_token: str | None = Cookie(None, alias=settings.cookie_name),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Validate existing session from cookie.

    Used by frontend to restore session on page load.
    """
    if not session_token:
        raise HTTPException(status_code=401, detail="No session cookie found")

    # Use the enhanced strategy to validate token
    strategy = DatabaseStrategy(session, request)
    # Get user manager through dependency injection
    async for user_db in get_user_db(session):
        user_manager = UserManager(user_db)
        user = await strategy.read_token(session_token, user_manager)
        break

    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")

    return user


@router.post("/session/refresh", response_model=SessionRefreshResponse)
async def refresh_session(
    user: User = Depends(current_active_user),
    session_token: str | None = Cookie(None, alias=settings.cookie_name),
    session: AsyncSession = Depends(get_async_session),
) -> SessionRefreshResponse:
    """Explicitly refresh the current session expiration.

    Useful for "keep me logged in" functionality.
    """
    if not session_token:
        raise HTTPException(status_code=401, detail="No session cookie found")

    stmt = select(AccessToken).where(
        AccessToken.token == session_token,  # type:ignore[arg-type]
        AccessToken.user_id == user.id,  # type:ignore[arg-type]
    )
    result = await session.execute(stmt)
    access_token = result.scalar_one_or_none()

    if not access_token:
        raise HTTPException(status_code=401, detail="Session not found")

    # Extend expiration
    new_expiry = datetime.now(UTC) + timedelta(hours=settings.session_expire_hours)

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
    access_token.last_accessed = datetime.now(UTC)

    await session.commit()

    logger.info(f"Session manually refreshed for user {user.id}")

    return SessionRefreshResponse(
        expires_at=new_expiry,
        extended_by_hours=settings.session_expire_hours,
    )


@router.get("/sessions/active", response_model=list[SessionInfo])
async def get_active_sessions(
    user: User = Depends(current_active_user),
    session_token: str | None = Cookie(None, alias=settings.cookie_name),
    session: AsyncSession = Depends(get_async_session),
) -> list[SessionInfo]:
    """Get all active sessions for the current user.

    Allows users to see and manage their sessions.
    """
    stmt = (
        select(AccessToken)
        .where(
            col(AccessToken.user_id) == user.id,
            col(AccessToken.expires_at) > datetime.now(UTC),
        )
        .order_by(col(AccessToken.last_accessed).desc())
    )

    result = await session.execute(stmt)
    sessions = result.scalars().all()

    return [
        SessionInfo(
            token_preview=token.token[:8] + "...",
            created_at=token.created_at,
            expires_at=token.expires_at,
            last_accessed=token.last_accessed,
            user_agent=token.user_agent,
            ip_address=token.ip_address,
            is_current=(token.token == session_token),
        )
        for token in sessions
    ]


@router.delete("/sessions/{token_preview}")
async def revoke_session(
    token_preview: str,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Revoke a specific session.

    Allows users to manage their active sessions for security.
    """
    # Find the session by preview (first 8 chars)
    if not token_preview.endswith("..."):
        raise HTTPException(status_code=400, detail="Invalid token preview format")

    token_start = token_preview[:-3]  # Remove "..."

    # Find matching tokens
    stmt = select(AccessToken).where(
        col(AccessToken.user_id) == user.id,
        col(AccessToken.token).startswith(token_start),
    )
    result = await session.execute(stmt)
    access_tokens = result.scalars().all()

    if not access_tokens:
        raise HTTPException(status_code=404, detail="Session not found")

    if len(access_tokens) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple sessions match this preview. Please use full token.",
        )

    # Delete the session
    await session.delete(access_tokens[0])
    await session.commit()

    logger.info(
        f"User {user.id} revoked session {token_preview}",
        extra={"user_id": str(user.id), "token_preview": token_preview},
    )

    return {"status": "success", "message": "Session revoked successfully"}
