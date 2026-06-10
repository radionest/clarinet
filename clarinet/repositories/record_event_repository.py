"""Repository for record audit events."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from clarinet.models.record_event import RecordEvent
from clarinet.repositories.base import BaseRepository


class RecordEventRepository(BaseRepository[RecordEvent]):
    """Append-only access to the ``record_event`` audit table.

    ``add()`` only flushes — the event commits together with the mutation
    it describes (repository mutation methods commit, otherwise the request
    session commits on teardown).
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
            .where(RecordEvent.kind == "deleted")
            .order_by(col(RecordEvent.occurred_at).desc(), col(RecordEvent.id).desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
