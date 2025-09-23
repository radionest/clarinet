"""
Session storage model for cookie authentication with lifecycle management.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Column, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, SQLModel


class AccessToken(SQLModel, table=True):
    """
    Enhanced session token with lifecycle management.
    """

    __tablename__ = "access_token"
    __table_args__ = (
        Index("ix_access_token_expires_at", "expires_at"),
        Index("ix_access_token_user_id", "user_id"),
    )

    token: str = Field(primary_key=True, index=True)
    user_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = Field(index=True)  # When session expires
    last_accessed: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Optional metadata fields
    user_agent: str | None = Field(default=None, max_length=512)
    ip_address: str | None = Field(default=None, max_length=45)  # IPv6 support

    @property
    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.now(UTC) >= self.expires_at

    @property
    def is_active(self) -> bool:
        """Check if session is currently active (accessed recently)."""
        idle_timeout = timedelta(hours=1)  # Configurable
        return datetime.now(UTC) - self.last_accessed < idle_timeout

    def refresh_expiration(self, extend_by: timedelta) -> None:
        """Extend session expiration for active users."""
        self.last_accessed = datetime.now(UTC)
        new_expiry = datetime.now(UTC) + extend_by
        if new_expiry > self.expires_at:
            self.expires_at = new_expiry
