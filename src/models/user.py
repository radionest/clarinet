"""
User-related models for the Clarinet framework.

This module provides models for users, roles, and authentication.
"""

import uuid
from datetime import datetime, UTC
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from sqlmodel import SQLModel, Field, Relationship

from .base import BaseModel

if TYPE_CHECKING:
    from .task import Task, TaskType


class UserRolesLink(BaseModel, table=True):
    """Link table for many-to-many relationship between users and roles."""

    user_id: str = Field(foreign_key="user.id", primary_key=True)
    role_name: str = Field(foreign_key="userrole.name", primary_key=True)


class UserBase(BaseModel):
    """Base model for user data."""
    
    id: str = Field(primary_key=True)
    isactive: bool = Field(default=True)


class User(UserBase, table=True):
    """Model representing a user in the system."""

    password: str
    roles: List["UserRole"] = Relationship(
        back_populates="users", link_model=UserRolesLink
    )
    tasks: List["Task"] = Relationship(back_populates="user")


class UserRead(UserBase):
    """Pydantic model for reading user data without sensitive fields."""
    pass


class UserRole(BaseModel, table=True):
    """Model representing a role that can be assigned to users."""

    name: str = Field(primary_key=True)
    users: List[User] = Relationship(back_populates="roles", link_model=UserRolesLink)
    allowed_tasks: List["TaskType"] = Relationship(back_populates="constraint_role")


class HTTPSession(SQLModel, table=True):
    """Model for tracking HTTP sessions."""

    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: str = Field(foreign_key="user.id")
    start_time: datetime = Field(default_factory=lambda: datetime.now(UTC))