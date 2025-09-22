"""
Session storage model for cookie authentication.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, SQLModel


class AccessToken(SQLModel, table=True):
    """
    Database-backed session token.
    Minimal fields according to KISS.
    """

    token: str = Field(primary_key=True, index=True)
    user_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("user.id"),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # fastapi-users manages expires_at automatically
