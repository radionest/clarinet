"""
Security utilities for the Clarinet API.

This module provides utilities for authentication, authorization, password hashing,
and token generation/validation.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
from authlib.jose import JoseError, jwt
from fastapi import Cookie, Depends
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from src.exceptions import UNAUTHORIZED
from src.settings import settings

# Configure OAuth2 bearer token scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


class Token(BaseModel):
    """Schema for authentication token response."""

    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Schema for JWT token payload."""

    username: str
    exp: datetime | None = None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    result: bool = bcrypt.checkpw(
        password=plain_password.encode("utf-8"),
        hashed_password=hashed_password.encode("utf-8"),
    )
    return result


def get_password_hash(password: str) -> str:
    """Generate a password hash."""
    salt = bcrypt.gensalt()
    hashed: bytes = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode()


def create_access_token(data: TokenData, expires_delta: timedelta | None = None) -> Token:
    """Create a new JWT access token."""
    # Set expiration time
    if expires_delta:
        new_expire_time = datetime.now(UTC) + expires_delta
    else:
        new_expire_time = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)

    data.exp = new_expire_time

    # Create token with header and payload
    header = {"alg": settings.jwt_algorithm}
    encoded_jwt = jwt.encode(header, data.model_dump(), settings.jwt_secret_key)

    return Token(access_token=encoded_jwt.decode())


def decode_token(token: Annotated[str, Depends(oauth2_scheme)]) -> TokenData:
    """Decode and validate a JWT token from Authorization header."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key)
        token_data = TokenData(**payload)

        # Validate token data
        if token_data.exp is None or token_data.username is None:
            raise UNAUTHORIZED
        if token_data.exp < datetime.now(UTC):
            raise UNAUTHORIZED

        return token_data
    except JoseError as e:
        raise UNAUTHORIZED from e


def decode_token_cookie(
    clarinet_auth_token: Annotated[str | None, Cookie()] = None,
) -> TokenData:
    """Decode and validate a JWT token from cookie."""
    if not clarinet_auth_token:
        raise UNAUTHORIZED

    try:
        payload = jwt.decode(clarinet_auth_token, settings.jwt_secret_key)
        token_data = TokenData(**payload)

        # Validate token data
        if token_data.exp is None or token_data.username is None:
            raise UNAUTHORIZED
        if token_data.exp < datetime.now(UTC):
            raise UNAUTHORIZED

        return token_data
    except JoseError as e:
        raise UNAUTHORIZED from e
