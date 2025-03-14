"""
Authentication router for the Clarinet framework.

This module provides API endpoints for user authentication and token management.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status, Cookie
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session

from src.exceptions import UNAUTHORIZED
from src.models import User
from src.settings import settings
from src.utils.database import get_session
from src.api.security import create_access_token, TokenData, Token
from src.api.routers.user import authenticate_user

router = APIRouter(tags=["authentication"])


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Session = Depends(get_session),
) -> Token:
    """Generate access token from username and password."""
    user = authenticate_user(form_data.username, form_data.password, session=session)
    token_data = TokenData(username=user.id)
    token = create_access_token(
        data=token_data, 
        expires_delta=timedelta(minutes=settings.jwt_expire_minutes)
    )
    return token


@router.post("/login")
async def login_with_cookie(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Session = Depends(get_session),
) -> Response:
    """Login and set authentication cookie."""
    token = await login_for_access_token(form_data, session)
    response = Response(status_code=status.HTTP_200_OK)
    response.set_cookie(
        key="clarinet_auth_token", 
        value=token.access_token,
        httponly=True,
        max_age=settings.jwt_expire_minutes * 60,
        secure=not settings.debug,
        samesite="lax"
    )
    return response


@router.post("/logout")
async def logout() -> Response:
    """Logout by clearing the authentication cookie."""
    response = Response(status_code=status.HTTP_200_OK)
    response.delete_cookie(key="clarinet_auth_token")
    return response