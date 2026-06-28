"""Unit tests for DicomWebProxyService preload worker — multi-study progress aggregation."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from clarinet.services.dicomweb.service import DicomWebProxyService


def _make_series_result(series_uid: str) -> MagicMock:
    result = MagicMock()
    result.series_instance_uid = series_uid
    return result


def _make_cached_entry(num_instances: int) -> MagicMock:
    entry = MagicMock()
    entry.instances = {f"sop_{i}": MagicMock() for i in range(num_instances)}
    return entry


@pytest.fixture
def progress_store() -> dict[str, dict[str, Any]]:
    return {}


@pytest.fixture
def mock_filler(progress_store: dict[str, dict[str, Any]]) -> MagicMock:
    """CacheFiller mock with a dict-backed preload progress store."""
    filler = MagicMock()
    filler.get_preload_progress.side_effect = progress_store.get
    filler.set_preload_progress.side_effect = progress_store.__setitem__
    return filler


def _make_service(mock_filler: MagicMock, mock_client: MagicMock) -> DicomWebProxyService:
    return DicomWebProxyService(client=mock_client, pacs=MagicMock(), filler=mock_filler)


class TestPreloadWorker:
    """_preload_worker: sequential multi-study caching with aggregated progress."""

    @pytest.mark.asyncio
    async def test_multi_study_aggregates_progress(
        self,
        mock_filler: MagicMock,
        progress_store: dict[str, dict[str, Any]],
    ) -> None:
        """Two studies x 3 instances: received is monotonic, study fields track each study."""
        task_id = "t1"
        progress_store[task_id] = {"status": "starting", "received": 0}
        snapshots: list[dict[str, Any]] = []

        mock_client = MagicMock()
        mock_client.find_series = AsyncMock(
            side_effect=lambda query, peer: [_make_series_result(f"{query.study_instance_uid}_s1")]
        )

        async def fake_ensure_study(
            study_uid: str,
            series_uids: list[str],
            on_progress: Any = None,
        ) -> dict[str, MagicMock]:
            for i in range(1, 4):
                on_progress(i, 3)
                snapshots.append(dict(progress_store[task_id]))
            return {series_uids[0]: _make_cached_entry(3)}

        mock_filler.ensure_study = AsyncMock(side_effect=fake_ensure_study)
        service = _make_service(mock_filler, mock_client)

        await service._preload_worker(["study1", "study2"], task_id)

        final = progress_store[task_id]
        assert final["status"] == "ready"
        assert final["received"] == 6
        assert final["total"] == 6

        received_values = [s["received"] for s in snapshots]
        assert received_values == [1, 2, 3, 4, 5, 6]
        assert [s["study_index"] for s in snapshots] == [1, 1, 1, 2, 2, 2]
        assert all(s["study_count"] == 2 for s in snapshots)
        assert [s["study_received"] for s in snapshots] == [1, 2, 3, 1, 2, 3]
        assert all(s["study_total"] == 3 for s in snapshots)

    @pytest.mark.asyncio
    async def test_fail_fast_on_second_study(
        self,
        mock_filler: MagicMock,
        progress_store: dict[str, dict[str, Any]],
    ) -> None:
        """An error on study 2 reports status=error; study 1 stays cached (no rollback)."""
        task_id = "t2"
        progress_store[task_id] = {"status": "starting", "received": 0}

        mock_client = MagicMock()
        mock_client.find_series = AsyncMock(
            side_effect=lambda query, peer: [_make_series_result(f"{query.study_instance_uid}_s1")]
        )

        call_count = 0

        async def fail_on_second(*args: Any, **kwargs: Any) -> dict[str, MagicMock]:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("C-GET failed")
            return {"study1_s1": _make_cached_entry(2)}

        mock_filler.ensure_study = AsyncMock(side_effect=fail_on_second)
        service = _make_service(mock_filler, mock_client)

        await service._preload_worker(["study1", "study2"], task_id)

        final = progress_store[task_id]
        assert final["status"] == "error"
        assert "C-GET failed" in final["error"]
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_studies_without_series_reach_ready_zero(
        self,
        mock_filler: MagicMock,
        progress_store: dict[str, dict[str, Any]],
    ) -> None:
        """Studies with no series are skipped; terminal status is ready/0."""
        task_id = "t3"
        progress_store[task_id] = {"status": "starting", "received": 0}

        mock_client = MagicMock()
        mock_client.find_series = AsyncMock(return_value=[])
        mock_filler.ensure_study = AsyncMock()
        service = _make_service(mock_filler, mock_client)

        await service._preload_worker(["study1", "study2"], task_id)

        final = progress_store[task_id]
        assert final["status"] == "ready"
        assert final["received"] == 0
        assert final["total"] == 0
        mock_filler.ensure_study.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_progress_entry_returns_silently(
        self,
        mock_filler: MagicMock,
    ) -> None:
        """A task_id without a progress entry (TTL-evicted) aborts the worker."""
        mock_client = MagicMock()
        mock_client.find_series = AsyncMock()
        service = _make_service(mock_filler, mock_client)

        await service._preload_worker(["study1"], "unknown_task")

        mock_client.find_series.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_preload_registers_task(
        self,
        mock_filler: MagicMock,
        progress_store: dict[str, dict[str, Any]],
    ) -> None:
        """start_preload seeds a 'starting' entry and spawns the worker."""
        mock_client = MagicMock()
        mock_client.find_series = AsyncMock(return_value=[])
        service = _make_service(mock_filler, mock_client)

        task_id = await service.start_preload(["study1"])

        assert task_id.startswith("preload_")
        await asyncio.gather(*service._preload_tasks)
        assert progress_store[task_id]["status"] == "ready"
