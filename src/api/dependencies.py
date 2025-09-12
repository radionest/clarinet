"""
Common dependencies for Clarinet API endpoints.

This module provides reusable dependency functions for FastAPI endpoints
including authentication, parameter validation, and other shared functionality.
"""

from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.security import TokenData, decode_token, decode_token_cookie
from ..exceptions import UNAUTHORIZED, ClarinetError
from ..models import User
from ..settings import settings
from ..utils.database import get_async_session


async def get_client_ip(request: Request) -> str:
    """
    Get the client's IP address.

    Args:
        request: FastAPI request object

    Returns:
        Client IP address as string
    """
    if request.client is None:
        raise ClarinetError("Cant get client IP, because request.client is None!")
    client_host = request.client.host
    return client_host


async def get_application_url(request: Request) -> str:
    """
    Get the base URL of the application.

    Args:
        request: FastAPI request object

    Returns:
        Base URL string including protocol, host, and port
    """
    host = request.url.scheme + "://" + request.url.netloc
    root_path = settings.root_url if settings.root_url != "/" else ""
    return f"{host}{root_path}"


async def common_parameters(
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int | None = Query(None, ge=1, description="Maximum number of items to return"),
) -> dict[str, int | None]:
    """
    Get common query parameters for pagination.

    Args:
        skip: Number of items to skip
        limit: Maximum number of items to return

    Returns:
        Dictionary with skip and limit parameters
    """
    return {"skip": skip, "limit": limit}


async def get_current_user_async(
    payload: Annotated[TokenData, Depends(decode_token)],
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """
    Get the current authenticated user from token using async session.

    Args:
        payload: Token data from JWT
        session: Async database session

    Returns:
        Authenticated user

    Raises:
        HTTPException: If user is not found or inactive
    """
    user = await session.get(User, payload.username)
    if not user or not user.isactive:
        raise UNAUTHORIZED
    return user


async def get_current_user_cookie_async(
    payload: Annotated[TokenData, Depends(decode_token_cookie)],
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """
    Get the current authenticated user from cookie using async session.

    Args:
        payload: Token data from JWT cookie
        session: Async database session

    Returns:
        Authenticated user

    Raises:
        HTTPException: If user is not found or inactive
    """
    user = await session.get(User, payload.username)
    if not user or not user.isactive:
        raise UNAUTHORIZED
    return user
