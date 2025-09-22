"""
User-related models for the Clarinet framework.

This module provides models for users, roles, and authentication.
"""

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi_users_db_sqlmodel import SQLModelBaseUserDB
from pydantic import EmailStr
from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, Relationship, SQLModel

from .base import BaseModel

if TYPE_CHECKING:
    from .task import Task, TaskDesign


class UserRolesLink(BaseModel, table=True):
    """Link table for many-to-many relationship between users and roles."""

    user_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("user.id"),
            primary_key=True,
        ),
    )
    role_name: str = Field(foreign_key="userrole.name", primary_key=True)


class User(SQLModelBaseUserDB, SQLModel, table=True):
    """
    User model for fastapi-users with UUID as primary key.

    Inherits from SQLModelBaseUserDB which provides:
    - id: UUID (primary key)
    - email: EmailStr (unique, indexed)
    - hashed_password: str
    - is_active: bool (default=True)
    - is_superuser: bool (default=False)
    - is_verified: bool (default=False)
    """

    __tablename__ = "user"

    # Override id field to ensure proper UUID handling
    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4),
    )

    # Relationships with existing models
    roles: list["UserRole"] = Relationship(back_populates="users", link_model=UserRolesLink)
    tasks: list["Task"] = Relationship(back_populates="user")


class UserRead(SQLModel):
    """Pydantic model for reading user data without sensitive fields."""

    id: UUID
    email: str
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False


class UserCreate(SQLModel):
    """Pydantic model for creating a new user."""

    email: EmailStr
    password: str
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False


class UserUpdate(SQLModel):
    """Pydantic model for updating user data."""

    password: str | None = None
    email: EmailStr | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None
    is_verified: bool | None = None


class UserRole(BaseModel, table=True):
    """Model representing a role that can be assigned to users."""

    name: str = Field(primary_key=True)
    users: list[User] = Relationship(back_populates="roles", link_model=UserRolesLink)
    allowed_task_designs: list["TaskDesign"] = Relationship(back_populates="constraint_role")
