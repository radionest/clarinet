"""Repository for record audit events."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from clarinet.models.record import Record
from clarinet.models.record_event import RecordEvent, RecordEventFind
from clarinet.repositories.base import BaseRepository


class RecordEventRepository(BaseRepository[RecordEvent]):
    """Append-only access to the ``record_event`` audit table.

    ``add()`` only flushes; the event is committed by the next commit on
    the shared session — usually the request-teardown commit. For most
    mutations the event therefore lands in the transaction *after* the
    mutation's own commit: a process crash in that window loses the event
    but never the mutation (accepted trade-off; only the cascade-delete
    path flushes events inside the mutation's transaction).
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, RecordEvent)

    async def add(self, event: RecordEvent) -> RecordEvent:
        """Append an event (flush only, no commit)."""
        return await self.create(event)

    async def list_for_record(
        self, record_id: int, skip: int = 0, limit: int = 200
    ) -> Sequence[RecordEvent]:
        """Events for *record_id*, oldest first (timeline order)."""
        stmt = (
            select(RecordEvent)
            .options(
                selectinload(RecordEvent.actor),  # type: ignore[arg-type]
                selectinload(RecordEvent.record),  # type: ignore[arg-type]
            )
            .where(RecordEvent.record_id == record_id)
            .order_by(col(RecordEvent.occurred_at).asc(), col(RecordEvent.id).asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_deleted(self, skip: int = 0, limit: int = 100) -> Sequence[RecordEvent]:
        """``deleted`` events (their ``record_id`` is NULL), newest first."""
        stmt = (
            select(RecordEvent)
            .options(
                selectinload(RecordEvent.actor),  # type: ignore[arg-type]
                selectinload(RecordEvent.record),  # type: ignore[arg-type]
            )
            .where(RecordEvent.kind == "deleted")
            .order_by(col(RecordEvent.occurred_at).desc(), col(RecordEvent.id).desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def find(self, criteria: RecordEventFind) -> Sequence[RecordEvent]:
        """Return events matching *criteria*, newest first.

        ``patient_id`` is resolved through the record table; events of
        already-deleted records (NULL ``record_id``) never match it.
        """
        stmt = select(RecordEvent).options(
            selectinload(RecordEvent.actor),  # type: ignore[arg-type]
            selectinload(RecordEvent.record),  # type: ignore[arg-type]
        )
        if criteria.kind is not None:
            stmt = stmt.where(RecordEvent.kind == criteria.kind)
        if criteria.actor_id is not None:
            stmt = stmt.where(RecordEvent.actor_id == criteria.actor_id)
        if criteria.patient_id is not None:
            stmt = stmt.where(
                col(RecordEvent.record_id).in_(
                    select(Record.id).where(Record.patient_id == criteria.patient_id)
                )
            )
        if criteria.since is not None:
            stmt = stmt.where(col(RecordEvent.occurred_at) >= criteria.since)
        stmt = (
            stmt.order_by(col(RecordEvent.occurred_at).desc(), col(RecordEvent.id).desc())
            .offset(criteria.skip)
            .limit(criteria.limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
