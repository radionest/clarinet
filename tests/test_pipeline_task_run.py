"""Unit tests for PipelineTaskRunRepository."""

from datetime import UTC, datetime, timedelta

import pytest

from clarinet.models.pipeline_task_run import (
    PipelineTaskRunCreate,
    PipelineTaskRunFind,
    PipelineTaskRunUpdate,
)
from clarinet.repositories.pipeline_task_run_repository import PipelineTaskRunRepository


def _make_create(
    task_id: str = "abc-123",
    task_name: str = "test_task",
    record_id: int | None = None,
) -> PipelineTaskRunCreate:
    return PipelineTaskRunCreate(
        id=task_id,
        task_name=task_name,
        queue="clarinet.default",
        record_id=record_id,
        started_at=datetime.now(UTC),
    )


class TestPipelineTaskRunRepository:
    @pytest.mark.asyncio
    async def test_upsert_start_creates_run(self, test_session):
        repo = PipelineTaskRunRepository(test_session)
        run = await repo.upsert_start(_make_create("tid-1"))
        assert run.id == "tid-1"
        assert run.status == "running"
        assert run.finished_at is None
        assert run.created_at is not None

    @pytest.mark.asyncio
    async def test_upsert_start_idempotent(self, test_session):
        repo = PipelineTaskRunRepository(test_session)
        first = await repo.upsert_start(_make_create("tid-dup"))
        second = await repo.upsert_start(_make_create("tid-dup", task_name="different_name"))
        assert first.id == second.id
        assert second.task_name == "test_task"  # original preserved

    @pytest.mark.asyncio
    async def test_finish_updates_status(self, test_session):
        repo = PipelineTaskRunRepository(test_session)
        await repo.upsert_start(_make_create("tid-2"))
        update = PipelineTaskRunUpdate(
            status="succeeded",
            finished_at=datetime.now(UTC),
            execution_time=1.5,
            result={"score": 0.9},
        )
        run = await repo.finish("tid-2", update)
        assert run is not None
        assert run.status == "succeeded"
        assert run.execution_time == pytest.approx(1.5)
        assert run.result == {"score": 0.9}
        assert run.finished_at is not None

    @pytest.mark.asyncio
    async def test_finish_unknown_id_returns_none(self, test_session):
        repo = PipelineTaskRunRepository(test_session)
        result = await repo.finish(
            "no-such-id",
            PipelineTaskRunUpdate(status="failed", finished_at=datetime.now(UTC)),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_find_filters_by_status(self, test_session):
        repo = PipelineTaskRunRepository(test_session)
        await repo.upsert_start(_make_create("tid-3"))
        await repo.finish(
            "tid-3",
            PipelineTaskRunUpdate(status="succeeded", finished_at=datetime.now(UTC)),
        )
        await repo.upsert_start(_make_create("tid-4"))
        results = await repo.find(PipelineTaskRunFind(status="running"))
        ids = {r.id for r in results}
        assert "tid-4" in ids
        assert "tid-3" not in ids

    @pytest.mark.asyncio
    async def test_find_filters_by_task_name(self, test_session):
        repo = PipelineTaskRunRepository(test_session)
        await repo.upsert_start(_make_create("tid-5", task_name="convert"))
        await repo.upsert_start(_make_create("tid-6", task_name="segment"))
        results = await repo.find(PipelineTaskRunFind(task_name="segment"))
        assert {r.id for r in results} == {"tid-6"}

    @pytest.mark.asyncio
    async def test_find_filters_by_since(self, test_session):
        repo = PipelineTaskRunRepository(test_session)
        await repo.upsert_start(_make_create("tid-recent"))
        results = await repo.find(PipelineTaskRunFind(since=datetime.now(UTC) - timedelta(hours=1)))
        assert any(r.id == "tid-recent" for r in results)

    @pytest.mark.asyncio
    async def test_find_by_record_empty(self, test_session):
        repo = PipelineTaskRunRepository(test_session)
        await repo.upsert_start(_make_create("tid-norec"))
        results = await repo.find_by_record(999_999)
        assert len(results) == 0
