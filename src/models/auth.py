"""
Session storage model for cookie authentication.
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class AccessToken(SQLModel, table=True):
    """
    Database-backed session token.
    Minimal fields according to KISS.
    """

    token: str = Field(primary_key=True, index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # fastapi-users manages expires_at automatically
