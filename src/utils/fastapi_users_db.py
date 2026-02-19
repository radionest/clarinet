"""
Pydantic v2 compatible replacements for fastapi-users-db-sqlmodel.

The upstream package (fastapi-users-db-sqlmodel 0.3.0) is unmaintained and
incompatible with Pydantic v2 + SQLModel >= 0.0.25.  This module provides
drop-in replacements for SQLModelBaseUserDB and SQLModelUserDatabaseAsync.
"""

import uuid
from typing import TYPE_CHECKING, Any, Generic, cast

from fastapi_users.db.base import BaseUserDatabase
from fastapi_users.models import ID, OAP, UP
from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import Field, SQLModel, func, select


class SQLModelBaseUserDB(SQLModel):
    """Base user model for SQLModel with Pydantic v2 compatibility."""

    __tablename__ = "user"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, nullable=False)
    if TYPE_CHECKING:  # pragma: no cover
        email: str
    else:
        email: EmailStr = Field(
            sa_column_kwargs={"unique": True, "index": True}, nullable=False
        )
    hashed_password: str

    is_active: bool = Field(True, nullable=False)
    is_superuser: bool = Field(False, nullable=False)
    is_verified: bool = Field(False, nullable=False)

    model_config = {"from_attributes": True}


class SQLModelUserDatabaseAsync(Generic[UP, ID], BaseUserDatabase[UP, ID]):
    """
    Async database adapter for SQLModel (Pydantic v2 compatible).

    :param session: SQLAlchemy async session.
    :param user_model: SQLModel user model class.
    :param oauth_account_model: Optional OAuth account model.
    """

    session: AsyncSession
    user_model: type[UP]
    oauth_account_model: type[SQLModelBaseOAuthAccount] | None

    def __init__(
        self,
        session: AsyncSession,
        user_model: type[UP],
        oauth_account_model: type[SQLModelBaseOAuthAccount] | None = None,
    ) -> None:
        self.session = session
        self.user_model = user_model
        self.oauth_account_model = oauth_account_model

    async def get(self, id: ID) -> UP | None:
        return await self.session.get(self.user_model, id)

    async def get_by_email(self, email: str) -> UP | None:
        statement = select(self.user_model).where(
            func.lower(self.user_model.email) == func.lower(email)  # type: ignore[attr-defined]
        )
        results = await self.session.execute(statement)
        obj = results.first()
        if obj is None:
            return None
        return cast(UP, obj[0])

    async def get_by_oauth_account(self, oauth: str, account_id: str) -> UP | None:
        if self.oauth_account_model is None:
            raise NotImplementedError()
        statement = (
            select(self.oauth_account_model)
            .where(self.oauth_account_model.oauth_name == oauth)
            .where(self.oauth_account_model.account_id == account_id)
            .options(selectinload(self.oauth_account_model.user))  # type: ignore[attr-defined]
        )
        results = await self.session.execute(statement)
        oauth_account = results.first()
        if oauth_account:
            user = oauth_account[0].user  # type: ignore[attr-defined]
            return cast(UP, user)
        return None

    async def create(self, create_dict: dict[str, Any]) -> UP:
        user = self.user_model(**create_dict)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update(self, user: UP, update_dict: dict[str, Any]) -> UP:
        for key, value in update_dict.items():
            setattr(user, key, value)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def delete(self, user: UP) -> None:
        await self.session.delete(user)
        await self.session.commit()

    async def add_oauth_account(self, user: UP, create_dict: dict[str, Any]) -> UP:
        if self.oauth_account_model is None:
            raise NotImplementedError()
        oauth_account = self.oauth_account_model(**create_dict)
        user.oauth_accounts.append(oauth_account)  # type: ignore[attr-defined]
        self.session.add(user)
        await self.session.commit()
        return user

    async def update_oauth_account(
        self, user: UP, oauth_account: OAP, update_dict: dict[str, Any]
    ) -> UP:
        if self.oauth_account_model is None:
            raise NotImplementedError()
        for key, value in update_dict.items():
            setattr(oauth_account, key, value)
        self.session.add(oauth_account)
        await self.session.commit()
        return user


class SQLModelBaseOAuthAccount(SQLModel):
    """Base OAuth account model with Pydantic v2 compatibility."""

    __tablename__ = "oauthaccount"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    oauth_name: str = Field(index=True, nullable=False)
    access_token: str = Field(nullable=False)
    expires_at: int | None = Field(nullable=True)
    refresh_token: str | None = Field(nullable=True)
    account_id: str = Field(index=True, nullable=False)
    account_email: str = Field(nullable=False)

    model_config = {"from_attributes": True}
