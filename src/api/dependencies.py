"""
Common dependencies for Clarinet API endpoints.

This module provides reusable dependency functions for FastAPI endpoints
including authentication, parameter validation, and other shared functionality.
"""

from typing import Annotated, Dict, Optional

from fastapi import Cookie, Depends, HTTPException, Query, Request, status
from sqlmodel import Session

from ..exceptions import UNAUTHORIZED, ClarinetError
from ..models import User
from ..utils.database import get_session
from ..utils.logger import logger
from ..settings import settings
from ..api.security import decode_token, decode_token_cookie, TokenData


async def get_current_user(
    payload: Annotated[TokenData, Depends(decode_token)],
    session: Session = Depends(get_session),
) -> User:
    """
    Get the current authenticated user from token.
    
    Args:
        payload: Token data from JWT
        session: Database session
        
    Returns:
        Authenticated user
        
    Raises:
        HTTPException: If user is not found or inactive
    """
    user = session.get(User, payload.username)
    if not user or not user.isactive:
        raise UNAUTHORIZED
    return user


async def get_current_user_cookie(
    payload: Annotated[TokenData, Depends(decode_token_cookie)],
    session: Session = Depends(get_session),
) -> User:
    """
    Get the current authenticated user from cookie.
    
    Args:
        payload: Token data from JWT cookie
        session: Database session
        
    Returns:
        Authenticated user
        
    Raises:
        HTTPException: If user is not found or inactive
    """
    user = session.get(User, payload.username)
    if not user or not user.isactive:
        raise UNAUTHORIZED
    return user


async def get_client_ip(request: Request) -> str:
    """
    Get the client's IP address.
    
    Args:
        request: FastAPI request object
        
    Returns:
        Client IP address as string
    """
    if request.client is None:
        raise ClarinetError('Cant get client IP, because request.client is None!')
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
    limit: Optional[int] = Query(
        None, ge=1, description="Maximum number of items to return"
    ),
) -> Dict[str, Optional[int]]:
    """
    Common query parameters for pagination.
    
    Args:
        skip: Number of items to skip
        limit: Maximum number of items to return
        
    Returns:
        Dictionary with skip and limit parameters
    """
    return {"skip": skip, "limit": limit}