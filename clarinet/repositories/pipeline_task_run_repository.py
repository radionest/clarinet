"""Repository for pipeline task run audit records."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from clarinet.models.pipeline_task_run import (
    PipelineTaskRun,
    PipelineTaskRunCreate,
    PipelineTaskRunFind,
    PipelineTaskRunUpdate,
)
from clarinet.repositories.base import BaseRepository


class PipelineTaskRunRepository(BaseRepository[PipelineTaskRun]):
    """CRUD for the append-mostly ``pipeline_task_run`` audit table."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PipelineTaskRun)

    async def upsert_start(self, data: PipelineTaskRunCreate) -> PipelineTaskRun:
        """Insert a run row for task start; idempotent on duplicate task_id.

        Re-delivered messages (worker crash before ack) call this twice —
        the original row wins so the first ``started_at`` is preserved.
        """
        existing = await self.get_optional(data.id)
        if existing is not None:
            return existing
        return await self.create(PipelineTaskRun(**data.model_dump()))

    async def finish(self, task_id: str, data: PipelineTaskRunUpdate) -> PipelineTaskRun | None:
        """Apply terminal-status fields; None when the start row never arrived."""
        run = await self.get_optional(task_id)
        if run is None:
            return None
        return await self.update(run, data.model_dump(exclude_unset=True), exclude_unset=False)

    async def find(self, criteria: PipelineTaskRunFind) -> Sequence[PipelineTaskRun]:
        """Return runs matching *criteria*, newest first."""
        stmt = select(PipelineTaskRun)
        if criteria.status is not None:
            stmt = stmt.where(PipelineTaskRun.status == criteria.status)
        if criteria.task_name is not None:
            stmt = stmt.where(PipelineTaskRun.task_name == criteria.task_name)
        if criteria.record_id is not None:
            stmt = stmt.where(PipelineTaskRun.record_id == criteria.record_id)
        if criteria.since is not None:
            stmt = stmt.where(col(PipelineTaskRun.created_at) >= criteria.since)
        stmt = (
            stmt.order_by(col(PipelineTaskRun.created_at).desc())
            .offset(criteria.skip)
            .limit(criteria.limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def find_by_record(
        self, record_id: int, skip: int = 0, limit: int = 100
    ) -> Sequence[PipelineTaskRun]:
        """Return runs linked to *record_id*, newest first."""
        stmt = (
            select(PipelineTaskRun)
            .where(PipelineTaskRun.record_id == record_id)
            .order_by(col(PipelineTaskRun.started_at).desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
