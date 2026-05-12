"""Two-tier cache for DICOMweb proxy — memory-first with background disk persistence."""

import asyncio
import io
import shutil
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import IO, Any

import pydicom
from cachetools import TTLCache
from pydicom import Dataset
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clarinet.models.base import DicomQueryLevel
from clarinet.services.dicom.anon_path import (
    AnonPathError,
    build_context,
    render_working_folder,
)
from clarinet.services.dicom.client import DicomClient
from clarinet.services.dicom.models import DicomNode
from clarinet.services.dicomweb.models import MemoryCachedSeries
from clarinet.settings import settings
from clarinet.utils.logger import logger


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
        storage_path: Path | None = None,
        disk_write_concurrency: int = 4,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ):
        """Initialize the cache.

        Args:
            base_dir: Root directory for cached DICOM files
            ttl_hours: Time-to-live for disk cache entries in hours
            max_size_gb: Maximum disk cache size in gigabytes
            memory_ttl_minutes: Time-to-live for in-memory cache entries in minutes
            memory_max_entries: Maximum number of series in the in-memory TTLCache
            storage_path: Base storage path for resolving dcm_anon folders
            disk_write_concurrency: Max concurrent background disk write operations
            session_factory: Async session factory used to resolve the
                dcm_anon path from Study/Patient/Series state via
                ``settings.disk_path_template``. When ``None``, dcm_anon
                lookups always miss — only safe for tests that never write
                anonymized files.
        """
        self._base_dir = base_dir
        self._storage_path = storage_path
        self._session_factory = session_factory
        self._ttl_seconds = ttl_hours * 3600
        self._max_size_bytes = int(max_size_gb * 1024**3)
        self._locks: dict[str, asyncio.Lock] = {}
        self._memory_cache: TTLCache[str, MemoryCachedSeries] = TTLCache(
            maxsize=memory_max_entries, ttl=memory_ttl_minutes * 60
        )
        self._disk_write_tasks: set[asyncio.Task[None]] = set()
        self._dcm_anon_path_cache: dict[str, Path | None] = {}
        self._disk_write_semaphore = asyncio.Semaphore(disk_write_concurrency)
        self._preload_progress: dict[str, dict[str, Any]] = {}

    def _series_dir(self, study_uid: str, series_uid: str) -> Path:
        return self._base_dir / study_uid / series_uid

    @staticmethod
    def _validate_series_in_study(
        study_uid: str, series_uid: str, instances: dict[str, Any]
    ) -> None:
        """Raise if any instance's StudyInstanceUID differs from study_uid.

        Compensatory guard against inconsistent caller input — e.g. an anonymized
        StudyInstanceUID paired with an original SeriesInstanceUID. Without this,
        OHIF would receive series metadata whose embedded StudyInstanceUID
        doesn't match the one in its request URL and crash with
        ``Cannot read properties of undefined (reading 'StudyInstanceUID')`` in
        ``HangingProtocolService._setProtocol``.

        Scans every instance (cheap dict iteration, n usually ≤ few hundred)
        to catch mixed payloads where only some instances are mismatched.
        Best-effort: silently passes when instances have no usable
        StudyInstanceUID attribute (e.g. MagicMock fixtures in unit tests).
        """
        for sop_uid, ds in instances.items():
            actual = getattr(ds, "StudyInstanceUID", None)
            if not isinstance(actual, str):
                continue
            if actual != study_uid:
                raise RuntimeError(
                    f"Instance {sop_uid} of series {series_uid} does not "
                    f"belong to the requested study {study_uid}: "
                    f"StudyInstanceUID is {actual}. This usually indicates "
                    "an anonymized study paired with a non-anonymized "
                    "series UID."
                )

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

    async def _resolve_dcm_anon_dir(self, study_uid: str, series_uid: str) -> Path | None:
        """Resolve the dcm_anon directory for a study/series pair.

        Loads Study/Patient/Series from the DB (one shared session,
        sequential queries — `AsyncSession` is not concurrency-safe) and
        renders ``settings.disk_path_template`` at SERIES level, then
        appends ``/dcm_anon``. The candidate path is checked for
        existence; both hits and misses are cached in
        ``_dcm_anon_path_cache``.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID

        Returns:
            Path to dcm_anon directory if found, None otherwise
        """
        if self._storage_path is None or self._session_factory is None:
            return None

        cache_key = self._cache_key(study_uid, series_uid)
        if cache_key in self._dcm_anon_path_cache:
            return self._dcm_anon_path_cache[cache_key]

        # Local imports avoid an import cycle (models -> services -> models)
        from clarinet.models.patient import Patient
        from clarinet.models.study import Series, Study

        async with self._session_factory() as session:
            series = await session.get(Series, series_uid)
            if series is None:
                self._dcm_anon_path_cache[cache_key] = None
                return None
            study = await session.get(Study, study_uid)
            if study is None:
                self._dcm_anon_path_cache[cache_key] = None
                return None
            patient = await session.get(Patient, study.patient_id)
            if patient is None:
                self._dcm_anon_path_cache[cache_key] = None
                return None

        try:
            ctx = build_context(patient=patient, study=study, series=series)
            series_dir = render_working_folder(
                settings.disk_path_template,
                DicomQueryLevel.SERIES,
                ctx,
                self._storage_path,
            )
        except AnonPathError as exc:
            logger.warning(f"Cannot resolve dcm_anon dir for {study_uid}/{series_uid}: {exc}")
            self._dcm_anon_path_cache[cache_key] = None
            return None

        candidate = series_dir / "dcm_anon"
        exists = await asyncio.to_thread(candidate.is_dir)
        result = candidate if exists else None
        self._dcm_anon_path_cache[cache_key] = result
        return result

    async def _load_from_dcm_anon(
        self, study_uid: str, series_uid: str
    ) -> dict[str, Dataset] | None:
        """Load series from the dcm_anon directory.

        Resolves the path via DB-backed template rendering and reads
        ``*.dcm`` files from disk in a worker thread.

        Unlike _load_from_disk, this has no TTL check — dcm_anon files
        don't expire.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID

        Returns:
            Dict of datasets keyed by SOPInstanceUID, or None if not found
        """
        dcm_anon_dir = await self._resolve_dcm_anon_dir(study_uid, series_uid)
        if dcm_anon_dir is None:
            return None
        return await asyncio.to_thread(self._read_dcm_files, dcm_anon_dir)

    @staticmethod
    def _read_dcm_files(dcm_anon_dir: Path) -> dict[str, Dataset] | None:
        """Read all DICOM files from a directory (synchronous, call via to_thread)."""
        dcm_files = sorted(dcm_anon_dir.glob("*.dcm"))
        if not dcm_files:
            return None

        instances: dict[str, Dataset] = {}
        for path in dcm_files:
            try:
                ds = pydicom.dcmread(path)
                instances[str(ds.SOPInstanceUID)] = ds
            except Exception as e:
                logger.warning(f"Skipping unreadable dcm_anon file {path}: {e}")
                continue

        return instances if instances else None

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

        Disk cache entries live until ``DicomWebCacheCleanupService`` removes
        them (TTL- or size-based eviction). No staleness check here: DICOM
        data on the PACS is immutable, so a present entry is always valid
        as long as the marker and at least one ``*.dcm`` file exist.

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

        Guarded by ``_disk_write_semaphore`` to limit concurrent thread-pool usage
        and avoid starving other ``asyncio.to_thread`` callers (e.g. metadata conversion).

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instances: Dict of datasets keyed by SOPInstanceUID
        """
        async with self._disk_write_semaphore:
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

        Four-level lookup:
        1. Memory hit -> return immediately
        2. dcm_anon hit -> load into memory, return (no TTL — populated by anonymization)
        3. Disk cache hit -> load into memory, return
        4. Cache miss -> C-GET to memory -> return -> background disk write

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

            # 2. dcm_anon hit (anonymized files — no TTL expiration)
            anon_instances = await self._load_from_dcm_anon(study_uid, series_uid)
            if anon_instances is not None:
                logger.debug(
                    f"dcm_anon hit for series {series_uid} — "
                    f"loading {len(anon_instances)} instances to memory"
                )
                self._validate_series_in_study(study_uid, series_uid, anon_instances)
                return self._put_to_memory(
                    study_uid, series_uid, anon_instances, disk_persisted=True
                )

            # 3. Disk cache hit
            disk_instances = await asyncio.to_thread(self._load_from_disk, study_uid, series_uid)
            if disk_instances is not None:
                logger.debug(
                    f"Disk cache hit for series {series_uid} — "
                    f"loading {len(disk_instances)} instances to memory"
                )
                self._validate_series_in_study(study_uid, series_uid, disk_instances)
                return self._put_to_memory(
                    study_uid, series_uid, disk_instances, disk_persisted=True
                )

            # 3. Cache miss — retrieve from PACS to memory
            logger.debug(
                f"Cache miss — retrieving series {series_uid} via DICOM retrieve (memory mode)"
            )
            result = await client.get_series_to_memory(
                study_uid=study_uid,
                series_uid=series_uid,
                peer=pacs,
            )

            if result.num_completed == 0:
                raise RuntimeError(
                    f"C-GET returned 0 instances for series {series_uid} (status: {result.status})"
                )

            self._validate_series_in_study(study_uid, series_uid, result.instances)

            # Store in memory immediately
            entry = self._put_to_memory(
                study_uid, series_uid, result.instances, disk_persisted=False
            )
            logger.debug(
                f"Cached {len(result.instances)} instances for series {series_uid} (memory)"
            )

            # Schedule background disk write
            task = asyncio.create_task(
                self._write_to_disk_background(study_uid, series_uid, result.instances)
            )
            self._disk_write_tasks.add(task)
            task.add_done_callback(self._disk_write_tasks.discard)

            return entry

    async def ensure_study_cached(
        self,
        study_uid: str,
        series_uids: list[str],
        client: DicomClient,
        pacs: DicomNode,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, MemoryCachedSeries]:
        """Ensure all series of a study are cached, using a single study-level C-GET.

        Checks memory → dcm_anon → disk for each series. Missing series are fetched
        with one study-level C-GET instead of N per-series C-GETs, avoiding PACS
        association overload for large studies.

        Args:
            study_uid: Study Instance UID
            series_uids: List of Series Instance UIDs to cache
            client: DICOM client for C-GET operations
            pacs: Target PACS node
            progress: Optional mutable dict for reporting progress phases

        Returns:
            Dict mapping series_uid → MemoryCachedSeries for all requested series

        Raises:
            RuntimeError: If C-GET returns no instances for missing series
        """
        result: dict[str, MemoryCachedSeries] = {}
        missing_series: list[str] = []

        # Check all tiers for each series
        for series_uid in series_uids:
            if progress is not None:
                progress.update(
                    status="checking_cache",
                    cached_series=len(result),
                    total_series=len(series_uids),
                )
            # 1. Memory hit
            cached = self._get_from_memory(study_uid, series_uid)
            if cached is not None:
                result[series_uid] = cached
                continue

            # 2. dcm_anon hit
            anon_instances = await self._load_from_dcm_anon(study_uid, series_uid)
            if anon_instances is not None:
                logger.info(
                    f"dcm_anon hit for series {series_uid} — "
                    f"loading {len(anon_instances)} instances to memory"
                )
                result[series_uid] = self._put_to_memory(
                    study_uid, series_uid, anon_instances, disk_persisted=True
                )
                continue

            # 3. Disk cache hit
            disk_instances = await asyncio.to_thread(self._load_from_disk, study_uid, series_uid)
            if disk_instances is not None:
                logger.info(
                    f"Disk cache hit for series {series_uid} — "
                    f"loading {len(disk_instances)} instances to memory"
                )
                result[series_uid] = self._put_to_memory(
                    study_uid, series_uid, disk_instances, disk_persisted=True
                )
                continue

            missing_series.append(series_uid)

        if not missing_series:
            logger.debug(f"All {len(series_uids)} series for study {study_uid} found in cache")
            if progress is not None:
                progress["status"] = "ready"
            return result

        # Study-level lock to prevent duplicate study C-GETs
        study_lock_key = f"{study_uid}/__STUDY__"
        if study_lock_key not in self._locks:
            self._locks[study_lock_key] = asyncio.Lock()
        study_lock = self._locks[study_lock_key]

        async with study_lock:
            # Double-check: another coroutine may have fetched while we waited
            still_missing: list[str] = []
            for series_uid in missing_series:
                cached = self._get_from_memory(study_uid, series_uid)
                if cached is not None:
                    result[series_uid] = cached
                else:
                    still_missing.append(series_uid)

            if not still_missing:
                logger.debug(f"All missing series for study {study_uid} resolved after lock")
                if progress is not None:
                    progress["status"] = "ready"
                return result

            # Single study-level C-GET
            logger.info(
                f"Cache miss for {len(still_missing)} series — "
                f"retrieving study {study_uid} via single DICOM retrieve"
            )

            on_progress: Callable[[int, int | None], None] | None = None
            if progress is not None:
                progress.update(status="fetching", received=0, total=None)

                def on_progress(received: int, total: int | None) -> None:
                    progress.update(status="fetching", received=received, total=total)

            cget_result = await client.get_study_to_memory(
                study_uid=study_uid, peer=pacs, on_progress=on_progress
            )

            if cget_result.num_completed == 0:
                raise RuntimeError(
                    f"Study C-GET returned 0 instances for study {study_uid} "
                    f"(status: {cget_result.status})"
                )

            # Group instances by SeriesInstanceUID
            grouped: dict[str, dict[str, object]] = {}
            for sop_uid, ds in cget_result.instances.items():
                ser_uid = str(ds.SeriesInstanceUID)
                if ser_uid not in grouped:
                    grouped[ser_uid] = {}
                grouped[ser_uid][sop_uid] = ds

            logger.info(
                f"Study C-GET completed: {cget_result.num_completed} instances "
                f"across {len(grouped)} series"
            )

            # Cache all series from C-GET (including unexpected SR/KO/PR)
            requested_set = set(series_uids)
            for ser_uid, instances in grouped.items():
                entry = self._put_to_memory(study_uid, ser_uid, instances, disk_persisted=False)

                if ser_uid in requested_set:
                    result[ser_uid] = entry

                if ser_uid not in requested_set:
                    logger.debug(
                        f"Cached unexpected series {ser_uid} from study C-GET "
                        f"({len(instances)} instances)"
                    )

                # Schedule background disk write
                task = asyncio.create_task(
                    self._write_to_disk_background(study_uid, ser_uid, instances)
                )
                self._disk_write_tasks.add(task)
                task.add_done_callback(self._disk_write_tasks.discard)

            if progress is not None:
                progress.update(status="ready", received=cget_result.num_completed)

        return result

    def build_series_zip(self, cached: MemoryCachedSeries, output: IO[bytes]) -> int:
        """Write cached series as ZIP archive (sync, call via to_thread).

        Each instance is stored as {SOPInstanceUID}.dcm inside the ZIP.

        Args:
            cached: In-memory cached series with instances dict.
            output: Writable binary stream for the ZIP archive.

        Returns:
            Number of instances written.
        """
        count = 0
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for sop_uid, ds in cached.instances.items():
                buf = io.BytesIO()
                pydicom.dcmwrite(buf, ds, enforce_file_format=True)
                zf.writestr(f"{sop_uid}.dcm", buf.getvalue())
                count += 1
        return count

    # --- Preload progress store ---

    def get_preload_progress(self, key: str) -> dict[str, Any] | None:
        return self._preload_progress.get(key)

    def set_preload_progress(self, key: str, data: dict[str, Any]) -> None:
        self._preload_progress[key] = data

    def clear_preload_progress(self, key: str) -> None:
        self._preload_progress.pop(key, None)

    async def read_instance_from_disk(
        self, study_uid: str, series_uid: str, instance_uid: str
    ) -> Dataset | None:
        """Read a single DICOM instance from dcm_anon or disk cache.

        Checks dcm_anon first, then falls back to dicomweb_cache.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instance_uid: SOP Instance UID

        Returns:
            pydicom Dataset if found and readable, None otherwise
        """
        # Check dcm_anon first
        dcm_anon_dir = await self._resolve_dcm_anon_dir(study_uid, series_uid)
        if dcm_anon_dir is not None:
            anon_path = dcm_anon_dir / f"{instance_uid}.dcm"
            ds = await asyncio.to_thread(self._read_single_dcm, anon_path)
            if ds is not None:
                return ds

        # Fall back to dicomweb_cache
        dcm_path = self._series_dir(study_uid, series_uid) / f"{instance_uid}.dcm"
        return await asyncio.to_thread(self._read_single_dcm, dcm_path)

    @staticmethod
    def _read_single_dcm(path: Path) -> Dataset | None:
        """Read one DICOM file, return None when missing or unreadable."""
        if not path.exists():
            return None
        try:
            return pydicom.dcmread(path)
        except Exception as e:
            logger.warning(f"Failed to read DICOM instance {path}: {e}")
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
        tasks = list(self._disk_write_tasks)
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.wait(tasks, timeout=5.0)
            logger.info(f"Cancelled {len(tasks)} pending disk-write tasks")

        self._disk_write_tasks.clear()
        self._memory_cache.clear()
        self._locks.clear()
        self._dcm_anon_path_cache.clear()
        logger.info("DICOMweb cache shutdown complete")
