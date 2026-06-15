"""Record audit event model.

Append-only journal of record mutations: who (``actor_id``, NULL = system /
worker / RecordFlow), when (``occurred_at``), what (``kind`` + status
transition + payload). Events are written by :class:`RecordService` right
next to the mutation itself; rows survive deletion of the record and the
actor (``ondelete=SET NULL``), and ``deleted`` events carry a snapshot of
the removed record in ``old_value``.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Column, Field, Relationship, SQLModel

from clarinet.types import PortableJSON

from .user import User

# DB column stays a plain string (additive downstream migrations);
# API payloads and service writes are constrained to these values.
type RecordEventKind = Literal[
    "created",
    "status_changed",
    "data_submitted",
    "data_updated",
    "assigned",
    "unassigned",
    "failed",
    "invalidated",
    "context_info_updated",
    "files_cleared",
    "deleted",
]


class RecordEventBase(SQLModel):
    """Shared fields for record audit events."""

    record_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("record.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Denormalized record id WITHOUT a FK: survives record deletion, so the
    # full history of a deleted record stays correlatable (record_id goes
    # NULL via SET NULL, record_key keeps the original value).
    record_key: int | None = Field(default=None)
    kind: str = Field(max_length=32)
    actor_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    from_status: str | None = Field(default=None, max_length=20)
    to_status: str | None = Field(default=None, max_length=20)
    old_value: dict[str, Any] | None = Field(
        default=None, sa_column=Column(PortableJSON, nullable=True)
    )
    new_value: dict[str, Any] | None = Field(
        default=None, sa_column=Column(PortableJSON, nullable=True)
    )
    reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    # DB clock, not app clock — multi-instance deployments must not produce
    # audit timestamps that contradict insertion order.
    occurred_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class RecordEvent(RecordEventBase, table=True):
    """Append-only audit event for a record mutation."""

    __tablename__ = "record_event"
    __table_args__ = (
        Index("ix_record_event_record_id_occurred_at", "record_id", "occurred_at"),
        Index("ix_record_event_kind", "kind"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # Read-only nav to the acting user (single FK: actor_id), never
    # back-populated — User has no events backref. The repo's read methods
    # eager-load it with selectinload so ``actor_name`` can be serialized.
    actor: User | None = Relationship()

    @property
    def actor_name(self) -> str | None:
        """Email of the acting user; None for system actions or when unloaded.

        Reads from ``__dict__`` to avoid triggering a lazy load outside an
        async context (mirrors ``User.role_names``). Populated only when the
        caller eager-loaded ``actor``.
        """
        actor = self.__dict__.get("actor")
        return actor.email if actor is not None else None


class RecordEventRead(RecordEventBase):
    """API response schema for record audit events."""

    id: int
    # Email of the acting user, resolved from ``actor_id`` via the eager-loaded
    # ``actor`` relationship; None for system actions (actor_id NULL).
    actor_name: str | None = None


class RecordEventFind(SQLModel):
    """Search filters for the record audit feed (all optional).

    ``patient_id`` matches events whose record currently belongs to that
    patient (JOIN via ``record``); events of already-deleted records
    (``record_id`` NULL) are excluded. Results are newest first.
    """

    kind: str | None = None
    actor_id: UUID | None = None
    patient_id: str | None = None
    since: datetime | None = None
    skip: int = 0
    limit: int = 100
