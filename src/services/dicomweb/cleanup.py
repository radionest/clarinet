"""Background service for cleaning the DICOMweb disk cache."""

import asyncio
import contextlib

from src.services.dicomweb.cache import DicomWebCache
from src.settings import settings
from src.utils.logger import logger


class DicomWebCacheCleanupService:
    """Periodic cleanup of expired and oversized DICOMweb disk cache entries.

    Follows the same pattern as ``SessionCleanupService`` — runs an ``asyncio.Task``
    loop that calls ``evict_expired()`` and ``evict_by_size()`` on a configurable interval.
    """

    def __init__(
        self,
        cache: DicomWebCache,
        cleanup_interval: int | None = None,
    ):
        """Initialize the cleanup service.

        Args:
            cache: The DicomWebCache instance to clean
            cleanup_interval: Interval between cleanups in seconds
        """
        self._cache = cache
        self.cleanup_interval = cleanup_interval or settings.dicomweb_cache_cleanup_interval
        self.is_running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background cleanup loop."""
        if self.is_running:
            logger.warning("DICOMweb cache cleanup service already running")
            return

        self.is_running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info("DICOMweb cache cleanup service started")

    async def stop(self) -> None:
        """Stop the background cleanup loop."""
        self.is_running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("DICOMweb cache cleanup service stopped")

    async def _cleanup_loop(self) -> None:
        """Main cleanup loop — runs until ``is_running`` is False."""
        while self.is_running:
            try:
                await self._perform_cleanup()
                await asyncio.sleep(self.cleanup_interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in DICOMweb cache cleanup: {e}")
                await asyncio.sleep(60)

    async def _perform_cleanup(self) -> tuple[int, int]:
        """Run both eviction passes off the event loop thread.

        Returns:
            Tuple of (expired_count, size_evicted_count)
        """
        expired, by_size = await asyncio.to_thread(self._cleanup_sync)
        if expired or by_size:
            logger.info(f"DICOMweb cache cleanup: removed {expired} expired, {by_size} by size")
        else:
            logger.debug("DICOMweb cache cleanup: nothing to remove")
        return expired, by_size

    def _cleanup_sync(self) -> tuple[int, int]:
        """Synchronous cleanup — called via ``asyncio.to_thread``.

        Returns:
            Tuple of (expired_count, size_evicted_count)
        """
        expired = self._cache.evict_expired()
        by_size = self._cache.evict_by_size()
        return expired, by_size

    async def cleanup_once(self) -> tuple[int, int]:
        """Perform a single cleanup run (for manual / testing use).

        Returns:
            Tuple of (expired_count, size_evicted_count)
        """
        return await self._perform_cleanup()
