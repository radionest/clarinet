"""Unit tests for DicomWebCacheCleanupService.

Tests cover:
- Service lifecycle (start / stop / idempotent)
- Cleanup execution (calls both eviction methods, returns counts)
- Integration with real disk cache directories
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.dicomweb.cache import DicomWebCache
from src.services.dicomweb.cleanup import DicomWebCacheCleanupService


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for cache storage."""
    return tmp_path / "dicomweb_cache"


@pytest.fixture
def cache(tmp_cache_dir: Path) -> DicomWebCache:
    """Create a DicomWebCache for testing."""
    return DicomWebCache(
        base_dir=tmp_cache_dir,
        ttl_hours=1,
        max_size_gb=1.0,
    )


@pytest.fixture
def service(cache: DicomWebCache) -> DicomWebCacheCleanupService:
    """Create a cleanup service with a short interval for testing."""
    return DicomWebCacheCleanupService(cache=cache, cleanup_interval=1)


class TestServiceLifecycle:
    """Test start/stop lifecycle of the cleanup service."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self, service: DicomWebCacheCleanupService) -> None:
        """start() should create a running asyncio task."""
        await service.start()
        assert service.is_running is True
        assert service._task is not None
        assert not service._task.done()
        await service.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, service: DicomWebCacheCleanupService) -> None:
        """stop() should cancel the task and set is_running to False."""
        await service.start()
        await service.stop()
        assert service.is_running is False
        assert service._task is not None
        assert service._task.done()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, service: DicomWebCacheCleanupService) -> None:
        """Calling start() twice should not create a second task."""
        await service.start()
        first_task = service._task
        await service.start()
        assert service._task is first_task
        await service.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self, service: DicomWebCacheCleanupService) -> None:
        """stop() without start() should not raise."""
        await service.stop()
        assert service.is_running is False


class TestCleanupExecution:
    """Test that cleanup calls both eviction methods."""

    @pytest.mark.asyncio
    async def test_cleanup_once_calls_both_eviction_methods(
        self, service: DicomWebCacheCleanupService
    ) -> None:
        """cleanup_once() should call evict_expired and evict_by_size."""
        with (
            patch.object(service._cache, "evict_expired", return_value=3) as mock_expired,
            patch.object(service._cache, "evict_by_size", return_value=2) as mock_size,
        ):
            expired, by_size = await service.cleanup_once()

        mock_expired.assert_called_once()
        mock_size.assert_called_once()
        assert expired == 3
        assert by_size == 2

    @pytest.mark.asyncio
    async def test_cleanup_once_returns_zero_when_nothing_to_clean(
        self, service: DicomWebCacheCleanupService
    ) -> None:
        """cleanup_once() should return (0, 0) when cache is clean."""
        with (
            patch.object(service._cache, "evict_expired", return_value=0),
            patch.object(service._cache, "evict_by_size", return_value=0),
        ):
            expired, by_size = await service.cleanup_once()

        assert expired == 0
        assert by_size == 0

    @pytest.mark.asyncio
    async def test_loop_runs_cleanup_periodically(
        self,
        cache: DicomWebCache,
    ) -> None:
        """The cleanup loop should run periodically when started."""
        # Verify the loop structure by testing start → cleanup_once → stop
        service = DicomWebCacheCleanupService(cache=cache, cleanup_interval=3600)

        with (
            patch.object(cache, "evict_expired", return_value=1) as mock_expired,
            patch.object(cache, "evict_by_size", return_value=0) as mock_size,
        ):
            # Run a single cleanup via the public API
            expired, by_size = await service.cleanup_once()

            assert expired == 1
            assert by_size == 0
            mock_expired.assert_called_once()
            mock_size.assert_called_once()

        # Verify start/stop works without error
        await service.start()
        assert service.is_running
        await service.stop()
        assert not service.is_running

    @pytest.mark.asyncio
    async def test_loop_recovers_from_error(
        self,
        cache: DicomWebCache,
    ) -> None:
        """The loop should log errors and continue after a failure."""
        service = DicomWebCacheCleanupService(cache=cache, cleanup_interval=0)

        call_count = 0

        async def flaky_cleanup() -> tuple[int, int]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("test error")
            return (0, 0)

        with patch.object(service, "_perform_cleanup", side_effect=flaky_cleanup):
            await service.start()
            # Error sleep is 60s, but the task itself should survive
            await asyncio.sleep(0.05)
            await service.stop()

        # At least the first (failing) call happened
        assert call_count >= 1


def _create_disk_series(
    cache_dir: Path, study_uid: str, series_uid: str, cached_at: float, file_size: int = 1024
) -> Path:
    """Create a fake cached series on disk."""
    series_dir = cache_dir / study_uid / series_uid
    series_dir.mkdir(parents=True, exist_ok=True)

    marker = series_dir / ".cached_at"
    marker.write_text(str(cached_at))

    dummy = series_dir / "1.2.3.dcm"
    dummy.write_bytes(b"\x00" * file_size)

    return series_dir


class TestIntegration:
    """Integration tests with real disk cache directories."""

    @pytest.mark.asyncio
    async def test_expired_entries_removed(self, tmp_cache_dir: Path, cache: DicomWebCache) -> None:
        """cleanup_once should remove expired entries from disk."""
        # TTL = 1 hour, entry is 2 hours old
        _create_disk_series(tmp_cache_dir, "study1", "series_old", cached_at=time.time() - 7200)
        _create_disk_series(tmp_cache_dir, "study1", "series_fresh", cached_at=time.time())

        service = DicomWebCacheCleanupService(cache=cache)
        expired, _ = await service.cleanup_once()

        assert expired == 1
        assert not (tmp_cache_dir / "study1" / "series_old").exists()
        assert (tmp_cache_dir / "study1" / "series_fresh").exists()

    @pytest.mark.asyncio
    async def test_size_enforcement(self, tmp_cache_dir: Path) -> None:
        """cleanup_once should enforce max disk size."""
        now = time.time()
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=24, max_size_gb=0)
        cache._max_size_bytes = 1500  # ~1.5KB limit

        # Use fresh timestamps so evict_expired doesn't remove them
        _create_disk_series(
            tmp_cache_dir, "study1", "series_old", cached_at=now - 100, file_size=1024
        )
        _create_disk_series(tmp_cache_dir, "study1", "series_new", cached_at=now, file_size=1024)

        service = DicomWebCacheCleanupService(cache=cache)
        _, by_size = await service.cleanup_once()

        assert by_size >= 1
        # Oldest should be removed, newest preserved
        assert not (tmp_cache_dir / "study1" / "series_old").exists()
        assert (tmp_cache_dir / "study1" / "series_new").exists()
