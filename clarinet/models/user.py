"""
User-related models for the Clarinet framework.

This module provides models for users, roles, and authentication.
"""

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi_users import schemas
from pydantic import Field as PydanticField
from pydantic import computed_field, field_serializer
from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, Relationship, SQLModel

from clarinet.utils.fastapi_users_db import SQLModelBaseUserDB

from .base import BaseModel

if TYPE_CHECKING:
    from .record import Record
    from .record_type import RecordType


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
    records: list["Record"] = Relationship(back_populates="user")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def role_names(self) -> list[str]:
        # Read from __dict__ to avoid triggering a lazy-load on `roles` outside
        # the auth flow (where it is eagerly loaded via selectinload).
        # If `roles` was never materialised, log a warning so regressions
        # surface instead of silently returning [].
        if "roles" not in self.__dict__:
            from clarinet.utils.logger import logger

            logger.warning(
                f"User.role_names accessed without eager-loaded roles "
                f"(user_id={self.id}); returning empty list",
            )
            return []
        return [r.name for r in (self.__dict__["roles"] or [])]


class UserRead(schemas.BaseUser[UUID]):
    """Pydantic model for reading user data without sensitive fields."""

    role_names: list[str] = PydanticField(default_factory=list)

    @field_serializer("email")
    @classmethod
    def serialize_email_ascii(cls, v: str) -> str:
        """Encode IDN Unicode domains back to ASCII punycode for RFC 5321 compliance."""
        try:
            local, domain = v.rsplit("@", 1)
            return f"{local}@{domain.encode('idna').decode('ascii')}"
        except UnicodeError:
            return v


class UserCreate(schemas.BaseUserCreate, extra="forbid"):  # type: ignore[call-arg]
    """Pydantic model for creating a new user with password validation."""

    password: str = Field(min_length=8, max_length=18)


class UserUpdate(schemas.BaseUserUpdate):
    """Pydantic model for updating user data."""

    pass


class UserRoleCreate(SQLModel):
    """Schema for creating a new role."""

    name: str = Field(min_length=1, max_length=50)


class UserRole(BaseModel, table=True):
    """Model representing a role that can be assigned to users."""

    name: str = Field(primary_key=True, min_length=1, max_length=50)
    users: list[User] = Relationship(back_populates="roles", link_model=UserRolesLink)
    allowed_record_types: list["RecordType"] = Relationship(back_populates="constraint_role")
