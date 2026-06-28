"""CacheFiller — Clarinet adapter over dimsechord's DicomCache + PullEngine.

dimsechord ships a neutral memory+disk ``DicomCache`` and a mem->disk->transport
``PullEngine``, but deliberately omits two Clarinet-specific concerns that this
adapter re-adds around them:

1. **dcm_anon tier-0** — anonymized ``.dcm`` files written by the
   ``AnonymizationService`` into ``{storage_path}/.../dcm_anon/``. They are
   served before ever touching the PACS and are *safe-by-default*: when the
   anonymized path cannot be resolved (``AnonPathError``) the lookup reports a
   cache miss rather than falling back to the raw (non-anonymized) UID.
2. **preload progress store** — a ``TTLCache`` feeding the SSE preload widget.

The dcm_anon machinery (``_resolve_dcm_anon_dir``, ``invalidate_dcm_anon_path``,
``_load_from_dcm_anon``, ``_read_dcm_files``) is moved from ``cache.py`` and
rewired onto this adapter's attributes; the old in-process ``TTLCache`` memory
tier is replaced by delegation to ``DicomCache.{get,put}_series_to_memory``.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import pydicom
from cachetools import TTLCache
from dimsechord import DicomCache, DicomClient, DicomNode, MemoryCachedSeries, PullEngine
from pydicom import Dataset
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clarinet.exceptions import AnonPathError
from clarinet.files import Files
from clarinet.models.base import DicomQueryLevel
from clarinet.utils.logger import logger


class CacheFiller:
    """Adapter wrapping a dimsechord cache + pull engine with the dcm_anon tier."""

    def __init__(
        self,
        *,
        cache: DicomCache,
        engine: PullEngine,
        client: DicomClient,
        pacs: DicomNode,
        retrieve_mode: str,
        session_factory: async_sessionmaker[AsyncSession] | None,
        storage_path: Path,
        dcm_anon_path_cache_max_entries: int = 1000,
        dcm_anon_path_cache_ttl_seconds: int = 300,
        preload_progress_ttl_seconds: int = 14400,
    ) -> None:
        """Initialize the adapter.

        Args:
            cache: dimsechord memory+disk cache (owns disk + memory tiers).
            engine: dimsechord pull engine (mem->disk->transport retrieval).
            client: DICOM client for study-level retrieves.
            pacs: Target PACS node.
            retrieve_mode: One of ``c-get`` / ``c-get-study`` / ``c-move`` /
                ``c-move-study``; selects the ``ensure_study`` strategy.
            session_factory: Async session factory used to resolve the dcm_anon
                path from Study/Patient/Series state via
                ``settings.disk_path_template``. When ``None``, dcm_anon lookups
                always miss — only safe for tests that never write anonymized
                files.
            storage_path: Base storage path for resolving dcm_anon folders.
            dcm_anon_path_cache_max_entries: Max entries in the dcm_anon
                path-resolution cache (hits and misses; LRU eviction when full).
            dcm_anon_path_cache_ttl_seconds: TTL for dcm_anon path cache entries.
                Negative results (anonymize-pending series) expire after this
                window so the next request re-checks disk.
            preload_progress_ttl_seconds: TTL bounding unclaimed preload progress
                entries (default 4h covers any realistic retrieve).
        """
        self._cache = cache
        self._engine = engine
        self._client = client
        self._pacs = pacs
        self._mode = retrieve_mode
        self._session_factory = session_factory
        self._storage_path = storage_path
        self._dcm_anon_path_cache: TTLCache[str, Path | None] = TTLCache(
            maxsize=dcm_anon_path_cache_max_entries,
            ttl=dcm_anon_path_cache_ttl_seconds,
        )
        self._preload_progress: TTLCache[str, dict[str, Any]] = TTLCache(
            maxsize=512, ttl=preload_progress_ttl_seconds
        )

    @staticmethod
    def _key(study_uid: str, series_uid: str) -> str:
        return f"{study_uid}/{series_uid}"

    # --- series-level retrieval -------------------------------------------

    async def ensure_series(self, study_uid: str, series_uid: str) -> MemoryCachedSeries:
        """Ensure a series is in memory: memory -> dcm_anon -> engine.

        The engine owns the disk + transport tiers; CacheFiller only prepends the
        dcm_anon tier-0 (anonymized files, no TTL — served before the PACS).
        """
        cached = self._cache.get_series_from_memory(study_uid, series_uid)
        if cached is not None:
            return cached
        anon_dir = await self._resolve_dcm_anon_dir(study_uid, series_uid)
        if anon_dir is not None:
            instances = await asyncio.to_thread(self._read_dcm_files, anon_dir)
            if instances:
                return self._cache.put_series_to_memory(
                    study_uid, series_uid, instances, disk_persisted=True
                )
        return await self._engine.ensure_series(study_uid, series_uid)

    async def ensure_study(
        self,
        study_uid: str,
        series_uids: list[str],
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> dict[str, MemoryCachedSeries]:
        """Ensure all requested series of a study are cached.

        Every retrieve mode keeps a single PACS association for the whole study
        (the reason these study-level retrievals exist — large studies / PACS
        that reject per-series associations):

        - ``c-get`` (default) / ``c-get-study``: one study-level C-GET
          (``get_study_to_memory`` carrying real per-instance progress), grouped
          by series and tee'd to disk.
        - ``c-move`` / ``c-move-study``: drive ``engine.stream_study`` over the
          still-missing series, counting arrivals for progress.

        Every branch first resolves memory -> dcm_anon -> disk per series, so an
        anonymized series is served locally and never re-fetched (raw) from the
        PACS.
        """
        if self._mode in ("c-get", "c-get-study"):
            return await self._ensure_study_cget(study_uid, series_uids, on_progress)
        # c-move / c-move-study
        return await self._ensure_study_cmove(study_uid, series_uids, on_progress)

    async def _ensure_study_cget(
        self,
        study_uid: str,
        series_uids: list[str],
        on_progress: Callable[[int, int | None], None] | None,
    ) -> dict[str, MemoryCachedSeries]:
        result, missing = await self._collect_local(study_uid, series_uids)
        if not missing:
            return result

        cget = await self._client.get_study_to_memory(
            study_uid=study_uid, peer=self._pacs, on_progress=on_progress
        )
        if cget.num_completed == 0:
            raise RuntimeError(
                f"Study C-GET returned 0 instances for study {study_uid} (status: {cget.status})"
            )

        requested = set(series_uids)
        for ser_uid, instances in self._group_by_series(cget.instances).items():
            # A series already resolved locally (memory/dcm_anon/disk) must not be
            # overwritten by its raw PACS copy — that would leak non-anonymized data.
            if ser_uid in result:
                continue
            entry = self._cache.put_series_to_memory(study_uid, ser_uid, instances)
            for sop_uid, ds in instances.items():
                self._cache.schedule_tee(study_uid, ser_uid, sop_uid, ds)
            if ser_uid in requested:
                result[ser_uid] = entry
        return result

    async def _ensure_study_cmove(
        self,
        study_uid: str,
        series_uids: list[str],
        on_progress: Callable[[int, int | None], None] | None,
    ) -> dict[str, MemoryCachedSeries]:
        result, missing = await self._collect_local(study_uid, series_uids)
        if not missing:
            return result

        grouped: dict[str, dict[str, Dataset]] = {}
        received = 0
        async for ds in self._engine.stream_study(study_uid, missing):
            grouped.setdefault(str(ds.SeriesInstanceUID), {})[str(ds.SOPInstanceUID)] = ds
            received += 1
            if on_progress is not None:
                on_progress(received, None)

        requested = set(series_uids)
        for ser_uid, instances in grouped.items():
            entry = self._cache.put_series_to_memory(study_uid, ser_uid, instances)
            if ser_uid in requested:
                result[ser_uid] = entry
        return result

    async def _collect_local(
        self, study_uid: str, series_uids: list[str]
    ) -> tuple[dict[str, MemoryCachedSeries], list[str]]:
        """Resolve each series locally; return ``(resolved, still_missing)``."""
        result: dict[str, MemoryCachedSeries] = {}
        missing: list[str] = []
        for series_uid in series_uids:
            local = await self._resolve_local_series(study_uid, series_uid)
            if local is not None:
                result[series_uid] = local
            else:
                missing.append(series_uid)
        return result, missing

    async def _resolve_local_series(
        self, study_uid: str, series_uid: str
    ) -> MemoryCachedSeries | None:
        """Memory -> dcm_anon -> disk. ``None`` when the series needs the PACS."""
        cached = self._cache.get_series_from_memory(study_uid, series_uid)
        if cached is not None:
            return cached
        anon = await self._load_from_dcm_anon(study_uid, series_uid)
        if anon:
            return self._cache.put_series_to_memory(
                study_uid, series_uid, anon, disk_persisted=True
            )
        disk = await asyncio.to_thread(self._cache.load_series_from_disk, study_uid, series_uid)
        if disk:
            return self._cache.put_series_to_memory(
                study_uid, series_uid, disk, disk_persisted=True
            )
        return None

    @staticmethod
    def _group_by_series(instances: dict[str, Dataset]) -> dict[str, dict[str, Dataset]]:
        grouped: dict[str, dict[str, Dataset]] = {}
        for sop_uid, ds in instances.items():
            grouped.setdefault(str(ds.SeriesInstanceUID), {})[sop_uid] = ds
        return grouped

    # --- single-instance / archive ---------------------------------------

    async def read_instance(self, study_uid: str, series_uid: str, sop_uid: str) -> Dataset | None:
        """Read a single instance: dcm_anon first, then the dimsechord disk index.

        The dcm_anon priority is a PHI guard: a series evicted from memory between
        ``ensure_series`` and ``read_instance`` must re-serve the *anonymized*
        instance, never a raw PACS copy that may have been tee'd to the disk index
        under the same SOP UID.
        """
        anon_dir = await self._resolve_dcm_anon_dir(study_uid, series_uid)
        if anon_dir is not None:
            ds = await asyncio.to_thread(self._read_single_dcm, anon_dir, sop_uid)
            if ds is not None:
                return ds
        return await asyncio.to_thread(self._cache.read_instance, study_uid, series_uid, sop_uid)

    @staticmethod
    def _read_single_dcm(anon_dir: Path, sop_uid: str) -> Dataset | None:
        """Read one ``{sop_uid}.dcm`` from a dir; None when missing or unreadable."""
        path = anon_dir / f"{sop_uid}.dcm"
        if not path.exists():
            return None
        try:
            return pydicom.dcmread(path)
        except Exception as e:
            logger.warning(f"Failed to read DICOM instance {path}: {e}")
            return None

    def build_series_zip(self, cached: MemoryCachedSeries, output: IO[bytes]) -> int:
        return self._cache.build_series_zip(cached, output)

    # --- preload progress store ------------------------------------------

    def set_preload_progress(self, task_id: str, data: dict[str, Any]) -> None:
        self._preload_progress[task_id] = data

    def get_preload_progress(self, task_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = self._preload_progress.get(task_id)
        return result

    # --- eviction + lifecycle (delegate to the dimsechord cache) ---------

    def evict_expired(self) -> int:
        return self._cache.evict_expired()

    def evict_by_size(self) -> int:
        return self._cache.evict_by_size()

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._cache.flush_pending_writes)
        self._cache.shutdown()

    # --- dcm_anon machinery (moved from cache.py, rewired to this adapter) -

    async def _resolve_dcm_anon_dir(self, study_uid: str, series_uid: str) -> Path | None:
        """Resolve the dcm_anon directory for a study/series pair.

        Loads Study/Patient/Series from the DB (one shared session, sequential
        queries — ``AsyncSession`` is not concurrency-safe) and renders
        ``settings.disk_path_template`` at SERIES level, then appends
        ``/dcm_anon``. The candidate path is checked for existence; both hits and
        misses are cached in ``_dcm_anon_path_cache``.

        Returns:
            Path to dcm_anon directory if found, None otherwise.
        """
        if self._storage_path is None or self._session_factory is None:
            return None

        cache_key = self._key(study_uid, series_uid)
        try:
            # Single __getitem__ avoids a TOCTOU between `in` and `[]` —
            # under TTL an entry can expire between the two.
            cached: Path | None = self._dcm_anon_path_cache[cache_key]
        except KeyError:
            pass
        else:
            return cached

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
            if series.study_uid != study_uid:
                self._dcm_anon_path_cache[cache_key] = None
                return None
            patient = await session.get(Patient, study.patient_id)
            if patient is None:
                self._dcm_anon_path_cache[cache_key] = None
                return None

        try:
            series_dir = Files.working_dirs(
                patient=patient,
                study=study,
                series=series,
                storage_path=self._storage_path,
            )[DicomQueryLevel.SERIES]
        except AnonPathError as exc:
            logger.warning(f"Cannot resolve dcm_anon dir for {study_uid}/{series_uid}: {exc}")
            self._dcm_anon_path_cache[cache_key] = None
            return None

        candidate = series_dir / "dcm_anon"
        exists = await asyncio.to_thread(candidate.is_dir)
        result = candidate if exists else None
        self._dcm_anon_path_cache[cache_key] = result
        return result

    def invalidate_dcm_anon_path(self, study_uid: str, series_uid: str) -> None:
        """Drop a single dcm_anon path cache entry.

        Useful when anonymization just wrote files and the caller wants the next
        read to re-check disk immediately without waiting for the TTL window.
        """
        self._dcm_anon_path_cache.pop(self._key(study_uid, series_uid), None)

    async def _load_from_dcm_anon(
        self, study_uid: str, series_uid: str
    ) -> dict[str, Dataset] | None:
        """Load series from the dcm_anon directory.

        Resolves the path via DB-backed template rendering and reads ``*.dcm``
        files from disk in a worker thread. Unlike disk-cache reads this has no
        TTL check — dcm_anon files don't expire.

        Returns:
            Dict of datasets keyed by SOPInstanceUID, or None if not found.
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
