"""Two-tier cache for DICOMweb proxy — memory-first with background disk persistence."""

import asyncio
import shutil
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pydicom
from cachetools import TTLCache
from pydicom import Dataset

from src.services.dicom.client import DicomClient
from src.services.dicom.models import DicomNode
from src.services.dicomweb.models import MemoryCachedSeries
from src.utils.logger import logger


class DicomWebCache:
    """Two-tier cache: in-memory (fast, O(1) lookup) + disk (persistent across restarts).

    On cache miss, retrieves series via C-GET directly to memory, returns immediately,
    and writes to disk in the background. Subsequent requests hit the memory cache.
    After restart, disk cache is loaded into memory on first access.
    """

    def __init__(
        self,
        base_dir: Path,
        ttl_hours: int = 24,
        max_size_gb: float = 10.0,
        memory_ttl_minutes: int = 30,
        memory_max_entries: int = 50,
    ):
        """Initialize the cache.

        Args:
            base_dir: Root directory for cached DICOM files
            ttl_hours: Time-to-live for disk cache entries in hours
            max_size_gb: Maximum disk cache size in gigabytes
            memory_ttl_minutes: Time-to-live for in-memory cache entries in minutes
            memory_max_entries: Maximum number of series in the in-memory TTLCache
        """
        self._base_dir = base_dir
        self._ttl_seconds = ttl_hours * 3600
        self._max_size_bytes = int(max_size_gb * 1024**3)
        self._locks: dict[str, asyncio.Lock] = {}
        self._memory_cache: TTLCache[str, MemoryCachedSeries] = TTLCache(
            maxsize=memory_max_entries, ttl=memory_ttl_minutes * 60
        )
        self._disk_write_tasks: set[asyncio.Task[None]] = set()

    def _series_dir(self, study_uid: str, series_uid: str) -> Path:
        return self._base_dir / study_uid / series_uid

    def _cache_key(self, study_uid: str, series_uid: str) -> str:
        return f"{study_uid}/{series_uid}"

    def _get_lock(self, study_uid: str, series_uid: str) -> asyncio.Lock:
        key = self._cache_key(study_uid, series_uid)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _get_from_memory(self, study_uid: str, series_uid: str) -> MemoryCachedSeries | None:
        """Get series from memory cache if present and not expired.

        TTL expiration is handled automatically by ``TTLCache.get()``.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID

        Returns:
            MemoryCachedSeries if valid, None otherwise
        """
        key = self._cache_key(study_uid, series_uid)
        result: MemoryCachedSeries | None = self._memory_cache.get(key)
        return result

    def _put_to_memory(
        self,
        study_uid: str,
        series_uid: str,
        instances: dict[str, Any],
        disk_persisted: bool = False,
    ) -> MemoryCachedSeries:
        """Store series in memory cache (TTLCache with LRU eviction).

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instances: Dict of datasets keyed by SOPInstanceUID
            disk_persisted: Whether this data is already on disk

        Returns:
            MemoryCachedSeries entry
        """
        key = self._cache_key(study_uid, series_uid)
        entry = MemoryCachedSeries(
            study_uid=study_uid,
            series_uid=series_uid,
            instances=instances,
            cached_at=time.time(),
            disk_persisted=disk_persisted,
        )
        self._memory_cache[key] = entry
        return entry

    def _load_from_disk(self, study_uid: str, series_uid: str) -> dict[str, Dataset] | None:
        """Load series from disk cache (synchronous, call via to_thread).

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID

        Returns:
            Dict of datasets keyed by SOPInstanceUID, or None if not on disk
        """
        series_dir = self._series_dir(study_uid, series_uid)
        marker = series_dir / ".cached_at"

        if not marker.exists():
            return None

        cached_at = float(marker.read_text().strip())
        if time.time() - cached_at > self._ttl_seconds:
            logger.debug(f"Disk cache expired for series {series_uid}")
            shutil.rmtree(series_dir, ignore_errors=True)
            return None

        dcm_files = sorted(series_dir.glob("*.dcm"))
        if not dcm_files:
            return None

        instances: dict[str, Dataset] = {}
        for path in dcm_files:
            try:
                ds = pydicom.dcmread(path)
                instances[str(ds.SOPInstanceUID)] = ds
            except Exception as e:
                logger.warning(f"Skipping unreadable cached file {path}: {e}")
                continue

        return instances if instances else None

    def _write_to_disk(self, study_uid: str, series_uid: str, instances: dict[str, Any]) -> None:
        """Write series to disk cache (synchronous, call via to_thread).

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instances: Dict of datasets keyed by SOPInstanceUID
        """
        series_dir = self._series_dir(study_uid, series_uid)
        series_dir.mkdir(parents=True, exist_ok=True)

        for sop_uid, ds in instances.items():
            filepath = series_dir / f"{sop_uid}.dcm"
            ds.save_as(filepath, enforce_file_format=True)

        marker = series_dir / ".cached_at"
        marker.write_text(str(time.time()))

    async def _write_to_disk_background(
        self, study_uid: str, series_uid: str, instances: dict[str, Any]
    ) -> None:
        """Write series to disk in background, update memory entry on completion.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instances: Dict of datasets keyed by SOPInstanceUID
        """
        try:
            await asyncio.to_thread(self._write_to_disk, study_uid, series_uid, instances)
            # Mark memory entry as disk-persisted
            key = self._cache_key(study_uid, series_uid)
            cached = self._memory_cache.get(key)
            if cached is not None:
                cached.disk_persisted = True
            logger.info(
                f"Background disk write complete for series {series_uid} "
                f"({len(instances)} instances)"
            )
        except Exception as e:
            logger.error(f"Background disk write failed for series {series_uid}: {e}")

    async def ensure_series_cached(
        self,
        study_uid: str,
        series_uid: str,
        client: DicomClient,
        pacs: DicomNode,
    ) -> MemoryCachedSeries:
        """Ensure a series is in memory cache, loading from disk or PACS as needed.

        Three-level lookup:
        1. Memory hit -> return immediately
        2. Disk hit -> load into memory, return
        3. Cache miss -> C-GET to memory -> return -> background disk write

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            client: DICOM client for C-GET operations
            pacs: Target PACS node

        Returns:
            MemoryCachedSeries with instances dict for O(1) lookup

        Raises:
            RuntimeError: If C-GET returns no instances
        """
        # 1. Memory hit (no lock needed for read)
        cached = self._get_from_memory(study_uid, series_uid)
        if cached is not None:
            logger.debug(f"Memory cache hit for series {series_uid}")
            return cached

        # Lock to prevent duplicate C-GETs for the same series
        lock = self._get_lock(study_uid, series_uid)
        async with lock:
            # Double-check after acquiring lock
            cached = self._get_from_memory(study_uid, series_uid)
            if cached is not None:
                logger.debug(f"Memory cache hit for series {series_uid} (after lock)")
                return cached

            # 2. Disk hit
            disk_instances = await asyncio.to_thread(self._load_from_disk, study_uid, series_uid)
            if disk_instances is not None:
                logger.info(
                    f"Disk cache hit for series {series_uid} — "
                    f"loading {len(disk_instances)} instances to memory"
                )
                return self._put_to_memory(
                    study_uid, series_uid, disk_instances, disk_persisted=True
                )

            # 3. Cache miss — retrieve from PACS to memory
            logger.info(f"Cache miss — retrieving series {series_uid} via C-GET (memory mode)")
            result = await client.get_series_to_memory(
                study_uid=study_uid,
                series_uid=series_uid,
                peer=pacs,
            )

            if result.num_completed == 0:
                raise RuntimeError(
                    f"C-GET returned 0 instances for series {series_uid} (status: {result.status})"
                )

            # Store in memory immediately
            entry = self._put_to_memory(
                study_uid, series_uid, result.instances, disk_persisted=False
            )
            logger.info(
                f"Cached {len(result.instances)} instances for series {series_uid} (memory)"
            )

            # Schedule background disk write
            task = asyncio.create_task(
                self._write_to_disk_background(study_uid, series_uid, result.instances)
            )
            self._disk_write_tasks.add(task)
            task.add_done_callback(self._disk_write_tasks.discard)

            return entry

    def read_instance_from_disk(
        self, study_uid: str, series_uid: str, instance_uid: str
    ) -> Dataset | None:
        """Read a single DICOM instance from disk cache.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instance_uid: SOP Instance UID

        Returns:
            pydicom Dataset if found and readable, None otherwise
        """
        dcm_path = self._series_dir(study_uid, series_uid) / f"{instance_uid}.dcm"
        if not dcm_path.exists():
            return None
        try:
            return pydicom.dcmread(dcm_path)
        except Exception as e:
            logger.warning(f"Failed to read cached instance {dcm_path}: {e}")
            return None

    def _iter_cached_entries(self) -> Iterator[tuple[Path, float, Path]]:
        """Yield (series_dir, cached_at, study_dir) for all valid cached entries on disk."""
        if not self._base_dir.exists():
            return
        for study_dir in self._base_dir.iterdir():
            if not study_dir.is_dir():
                continue
            for series_dir in study_dir.iterdir():
                if not series_dir.is_dir():
                    continue
                marker = series_dir / ".cached_at"
                if not marker.exists():
                    continue
                cached_at = float(marker.read_text().strip())
                yield series_dir, cached_at, study_dir

    @staticmethod
    def _cleanup_empty_study_dirs(study_dirs: Iterable[Path]) -> None:
        """Remove study directories that have no remaining series."""
        for study_dir in study_dirs:
            if study_dir.exists() and not any(study_dir.iterdir()):
                study_dir.rmdir()

    def evict_expired(self) -> int:
        """Remove all expired cache entries.

        Returns:
            Number of series directories removed
        """
        removed = 0
        study_dirs: set[Path] = set()
        for series_dir, cached_at, study_dir in self._iter_cached_entries():
            if time.time() - cached_at > self._ttl_seconds:
                shutil.rmtree(series_dir, ignore_errors=True)
                removed += 1
                study_dirs.add(study_dir)

        self._cleanup_empty_study_dirs(study_dirs)
        if removed > 0:
            logger.info(f"Evicted {removed} expired cache entries")
        return removed

    @staticmethod
    def _get_directory_size(directory: Path) -> int:
        """Calculate total size of all files in a directory tree.

        Args:
            directory: Root directory to measure

        Returns:
            Total size in bytes
        """
        total = 0
        for f in directory.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total

    def evict_by_size(self) -> int:
        """Remove oldest cache entries until total size is under the configured maximum.

        Returns:
            Number of series directories removed
        """
        entries = [
            (series_dir, cached_at, self._get_directory_size(series_dir), study_dir)
            for series_dir, cached_at, study_dir in self._iter_cached_entries()
        ]
        if not entries:
            return 0

        total_size = sum(e[2] for e in entries)
        if total_size <= self._max_size_bytes:
            return 0

        # Sort by cached_at ascending (oldest first)
        entries.sort(key=lambda e: e[1])

        removed = 0
        study_dirs_to_check: set[Path] = set()
        for series_dir, _, size, study_dir in entries:
            if total_size <= self._max_size_bytes:
                break
            shutil.rmtree(series_dir, ignore_errors=True)
            total_size -= size
            removed += 1
            study_dirs_to_check.add(study_dir)

        self._cleanup_empty_study_dirs(study_dirs_to_check)
        if removed > 0:
            logger.info(
                f"Evicted {removed} cache entries by size (target: {self._max_size_bytes} bytes)"
            )
        return removed

    async def shutdown(self) -> None:
        """Cancel pending background disk-write tasks and clear memory cache."""
        for task in self._disk_write_tasks:
            task.cancel()

        if self._disk_write_tasks:
            await asyncio.gather(*self._disk_write_tasks, return_exceptions=True)
            logger.info(f"Cancelled {len(self._disk_write_tasks)} pending disk-write tasks")

        self._disk_write_tasks.clear()
        self._memory_cache.clear()
        self._locks.clear()
        logger.info("DICOMweb cache shutdown complete")
