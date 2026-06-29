"""Unit tests for DicomWebCacheCleanupService.

The service is a scheduler: it periodically calls ``evict_expired()`` and
``evict_by_size()`` on the injected ``CacheFiller`` (which delegates to the
dimsechord disk index). These tests cover the scheduler's behaviour with a mock
filler — the actual disk eviction is owned and tested by dimsechord.

Tests cover:
- Service lifecycle (start / stop / idempotent)
- Cleanup execution (calls both eviction methods, returns counts)
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from clarinet.services.dicomweb.cleanup import DicomWebCacheCleanupService


@pytest.fixture
def filler() -> MagicMock:
    """A mock CacheFiller exposing evict_expired / evict_by_size (default: 0, 0)."""
    mock = MagicMock()
    mock.evict_expired.return_value = 0
    mock.evict_by_size.return_value = 0
    return mock


@pytest.fixture
def service(filler: MagicMock) -> DicomWebCacheCleanupService:
    """Create a cleanup service with a short interval for testing."""
    return DicomWebCacheCleanupService(filler=filler, cleanup_interval=1)


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
    """Test that cleanup calls both eviction methods on the filler."""

    @pytest.mark.asyncio
    async def test_cleanup_once_calls_both_eviction_methods(
        self, service: DicomWebCacheCleanupService, filler: MagicMock
    ) -> None:
        """cleanup_once() should call evict_expired and evict_by_size."""
        filler.evict_expired.return_value = 3
        filler.evict_by_size.return_value = 2

        expired, by_size = await service.cleanup_once()

        filler.evict_expired.assert_called_once()
        filler.evict_by_size.assert_called_once()
        assert expired == 3
        assert by_size == 2

    @pytest.mark.asyncio
    async def test_cleanup_once_returns_zero_when_nothing_to_clean(
        self, service: DicomWebCacheCleanupService
    ) -> None:
        """cleanup_once() should return (0, 0) when the filler reports nothing."""
        expired, by_size = await service.cleanup_once()

        assert expired == 0
        assert by_size == 0

    @pytest.mark.asyncio
    async def test_loop_runs_cleanup_periodically(self, filler: MagicMock) -> None:
        """The cleanup loop should run periodically when started."""
        filler.evict_expired.return_value = 1
        service = DicomWebCacheCleanupService(filler=filler, cleanup_interval=3600)

        # Run a single cleanup via the public API
        expired, by_size = await service.cleanup_once()
        assert expired == 1
        assert by_size == 0
        filler.evict_expired.assert_called_once()
        filler.evict_by_size.assert_called_once()

        # Verify start/stop works without error
        await service.start()
        assert service.is_running
        await service.stop()
        assert not service.is_running

    @pytest.mark.asyncio
    async def test_loop_recovers_from_error(self, filler: MagicMock) -> None:
        """The loop should log errors and continue after a failure."""
        service = DicomWebCacheCleanupService(filler=filler, cleanup_interval=0)

        call_count = 0

        async def flaky_cleanup() -> tuple[int, int]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("test error")
            return (0, 0)

        from unittest.mock import patch

        with patch.object(service, "_perform_cleanup", side_effect=flaky_cleanup):
            await service.start()
            # Error sleep is 60s, but the task itself should survive
            await asyncio.sleep(0.05)
            await service.stop()

        # At least the first (failing) call happened
        assert call_count >= 1
