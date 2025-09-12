"""
User-related models for the Clarinet framework.

This module provides models for users, roles, and authentication.
"""

from typing import TYPE_CHECKING

from pydantic import EmailStr
from sqlmodel import Field, Relationship, SQLModel

from .base import BaseModel

if TYPE_CHECKING:
    from .task import Task, TaskDesign


class UserRolesLink(BaseModel, table=True):
    """Link table for many-to-many relationship between users and roles."""

    user_id: str = Field(foreign_key="user.id", primary_key=True)
    role_name: str = Field(foreign_key="userrole.name", primary_key=True)


class User(SQLModel, table=True):
    """
    Minimal user model for fastapi-users with string ID.

    Custom implementation to use string ID instead of UUID.
    """

    __tablename__ = "user"

    # Use string ID (username) instead of UUID
    id: str = Field(primary_key=True)

    # FastAPI-Users required fields
    email: EmailStr = Field(sa_column_kwargs={"unique": True, "index": True}, nullable=False)
    hashed_password: str = Field(nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    is_superuser: bool = Field(default=False, nullable=False)
    is_verified: bool = Field(default=False, nullable=False)

    # Relationships with existing models
    roles: list["UserRole"] = Relationship(back_populates="users", link_model=UserRolesLink)
    tasks: list["Task"] = Relationship(back_populates="user")


class UserRead(SQLModel):
    """Pydantic model for reading user data without sensitive fields."""

    id: str
    email: str
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False


class UserRole(BaseModel, table=True):
    """Model representing a role that can be assigned to users."""

    name: str = Field(primary_key=True)
    users: list[User] = Relationship(back_populates="roles", link_model=UserRolesLink)
    allowed_task_designs: list["TaskDesign"] = Relationship(back_populates="constraint_role")
