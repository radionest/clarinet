"""Unit tests for PipelineTaskRunRepository and audit client serialization."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

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

    @pytest.mark.asyncio
    async def test_finish_ignores_late_retrying_after_terminal(self, test_session):
        """A stale 'retrying' PATCH must not downgrade a terminal status."""
        repo = PipelineTaskRunRepository(test_session)
        await repo.upsert_start(_make_create("tid-late"))
        await repo.finish(
            "tid-late",
            PipelineTaskRunUpdate(
                status="succeeded", finished_at=datetime.now(UTC), execution_time=1.0
            ),
        )
        run = await repo.finish(
            "tid-late",
            PipelineTaskRunUpdate(status="retrying", finished_at=datetime.now(UTC)),
        )
        assert run is not None
        assert run.status == "succeeded"
        assert run.execution_time == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_finish_ignores_explicit_nulls(self, test_session):
        """JSON nulls (e.g. retry_count) must not be applied to NOT NULL columns."""
        repo = PipelineTaskRunRepository(test_session)
        await repo.upsert_start(_make_create("tid-nulls"))
        update = PipelineTaskRunUpdate.model_validate(
            {
                "status": "succeeded",
                "finished_at": datetime.now(UTC),
                "retry_count": None,
                "error_type": None,
                "result": None,
            }
        )
        run = await repo.finish("tid-nulls", update)
        assert run is not None
        assert run.status == "succeeded"
        assert run.retry_count == 0

    @pytest.mark.asyncio
    async def test_upsert_start_returns_existing_on_insert_race(self, test_session):
        """Losing a concurrent same-id insert race falls back to the winner's row."""
        repo = PipelineTaskRunRepository(test_session)
        await repo.upsert_start(_make_create("tid-race"))
        await test_session.commit()

        real_get = repo.get_optional
        calls = {"n": 0}

        async def miss_once(id):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # simulate the pre-insert check missing the row
            return await real_get(id)

        with patch.object(repo, "get_optional", side_effect=miss_once):
            run = await repo.upsert_start(_make_create("tid-race", task_name="loser"))
        assert run.id == "tid-race"
        assert run.task_name == "test_task"  # winner's row preserved


class TestAuditClientSerialization:
    """The audit client must not send JSON nulls for unset optional fields."""

    def _client(self):
        from clarinet.client import ClarinetClient

        return ClarinetClient(base_url="http://test/api", auto_login=False)

    @pytest.mark.asyncio
    async def test_finish_pipeline_run_omits_none_fields(self):
        client = self._client()
        with patch.object(client, "_request", new_callable=AsyncMock) as request_mock:
            await client.finish_pipeline_run(
                task_id="tid-ser",
                status="succeeded",
                finished_at=datetime.now(UTC),
                execution_time=1.5,
            )
        sent = request_mock.call_args.kwargs["json"]
        assert sent["status"] == "succeeded"
        assert "retry_count" not in sent
        assert "error_type" not in sent
        assert "result" not in sent

    @pytest.mark.asyncio
    async def test_create_pipeline_run_omits_none_fields(self):
        client = self._client()
        with patch.object(client, "_request", new_callable=AsyncMock) as request_mock:
            await client.create_pipeline_run(
                task_id="tid-ser2",
                task_name="test_task",
                queue="clarinet.default",
                started_at=datetime.now(UTC),
            )
        sent = request_mock.call_args.kwargs["json"]
        assert sent["id"] == "tid-ser2"
        assert "record_id" not in sent
        assert "pipeline_id" not in sent
