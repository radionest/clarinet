"""
Async authentication router for the Clarinet framework.

This module provides async authentication endpoints for the Clarinet API.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_async
from src.api.routers.user import authenticate_user_async
from src.api.security import TokenData
from src.api.security import create_access_token as create_token
from src.exceptions import UNAUTHORIZED
from src.models import User
from src.settings import settings
from src.types import AuthResponse, MessageResponse
from src.utils.database import get_async_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: AsyncSession = Depends(get_async_session),
) -> AuthResponse:
    """Login endpoint that returns an access token."""
    user = await authenticate_user_async(form_data.username, form_data.password, session)

    if not user:
        raise UNAUTHORIZED

    token_data = TokenData(username=user.id)
    token = create_token(
        data=token_data, expires_delta=timedelta(minutes=settings.jwt_expire_minutes)
    )

    return {"access_token": token.access_token, "token_type": token.token_type}


@router.post("/login/cookie")
async def login_cookie(
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: AsyncSession = Depends(get_async_session),
) -> MessageResponse:
    """Login endpoint that sets an HTTP-only cookie."""
    user = await authenticate_user_async(form_data.username, form_data.password, session)

    if not user:
        raise UNAUTHORIZED

    token_data = TokenData(username=user.id)
    token = create_token(
        data=token_data, expires_delta=timedelta(minutes=settings.jwt_expire_minutes)
    )

    # Set cookie with token
    response.set_cookie(
        key="access_token",
        value=token.access_token,
        httponly=True,
        secure=not settings.debug,
        samesite="strict",
        max_age=settings.jwt_expire_minutes * 60,
    )

    return {"message": "Login successful", "username": user.id}


@router.post("/logout")
async def logout(response: Response) -> MessageResponse:
    """Logout endpoint that clears the authentication cookie."""
    response.delete_cookie(key="access_token")
    return {"message": "Logout successful"}


@router.post("/refresh")
async def refresh_token(
    user: User = Depends(get_current_user_async),
) -> AuthResponse:
    """Refresh the access token."""
    token_data = TokenData(username=user.id)
    token = create_token(
        data=token_data, expires_delta=timedelta(minutes=settings.jwt_expire_minutes)
    )

    return {"access_token": token.access_token, "token_type": token.token_type}
