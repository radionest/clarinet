"""
User router for the Clarinet framework.

This module provides API endpoints for user management, authentication, and role assignment.
"""

from datetime import timedelta
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from src.exceptions import UNAUTHORIZED, CONFLICT, NOT_FOUND, with_context
from src.models import User, UserRead, UserRole
from src.api.security import (
    decode_token,
    decode_token_cookie,
    TokenData,
    verify_password,
    get_password_hash
)
from src.utils.database import get_session

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_current_user_cookie(
    payload: Annotated[TokenData, Depends(decode_token_cookie)],
    session: Session = Depends(get_session),
) -> User:
    """Get current user from cookie authentication."""
    return get_user(user_id=payload.username, session=session)


@router.get("/me/token", response_model=UserRead)
async def get_current_user(
    payload: Annotated[TokenData, Depends(decode_token)],
    session: Session = Depends(get_session),
) -> User:
    """Get current user from token authentication."""
    return get_user(user_id=payload.username, session=session)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: str, session: Session = Depends(get_session)) -> User:
    """Get user by ID."""
    user = session.get(User, user_id)
    if not user:
        raise UNAUTHORIZED
    return user


@router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def add_user(user: User, session: Session = Depends(get_session)) -> User:
    """Create a new user."""
    new_user = User.model_validate(user)
    new_user.password = get_password_hash(user.password)
    
    try:
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
    except Exception:
        session.rollback()
        raise CONFLICT.with_context("User already exists")
        
    return new_user


@router.get("/roles/{role_name}", response_model=UserRole)
async def get_role_details(role_name: str, session: Session = Depends(get_session)) -> UserRole:
    """Get role details by name."""
    role = session.get(UserRole, role_name)
    if not role:
        raise NOT_FOUND.with_context(f"Role '{role_name}' not found")
    return role


@router.post("/roles", response_model=UserRole, status_code=status.HTTP_201_CREATED)
async def create_role(new_role: UserRole, session: Session = Depends(get_session)) -> UserRole:
    """Create a new role."""
    try:
        session.add(new_role)
        session.commit()
        session.refresh(new_role)
    except Exception:
        session.rollback()
        raise CONFLICT.with_context(f"Role '{new_role.name}' already exists")
    
    return new_role


@router.post("/{user_id}/roles/{role_name}", response_model=UserRead)
async def add_user_role(
    user_id: str,
    role_name: str,
    session: Session = Depends(get_session),
) -> User:
    """Assign a role to a user."""
    user = session.get(User, user_id)
    if not user:
        raise NOT_FOUND.with_context(f"User '{user_id}' not found")
        
    role = session.get(UserRole, role_name)
    if not role:
        raise NOT_FOUND.with_context(f"Role '{role_name}' not found")
    
    if role in user.roles:
        raise CONFLICT.with_context(f"User '{user_id}' already has role '{role_name}'")
    
    user.roles.append(role)
    session.commit()
    session.refresh(user)
    return user


@router.get("/me/roles", response_model=List[UserRole])
async def get_my_roles(user: User = Depends(get_current_user)) -> List[UserRole]:
    """Get roles for the current user."""
    return user.roles


@router.get("/{user_id}/roles", response_model=List[UserRole])
async def get_user_roles(user: User = Depends(get_user)) -> List[UserRole]:
    """Get roles for a specific user."""
    return user.roles


def authenticate_user(
    username: str, password: str, session: Session = Depends(get_session)
) -> User:
    """Authenticate a user with username and password."""
    user = get_user(username, session=session)
    if not verify_password(password, user.password):
        raise UNAUTHORIZED
    return user