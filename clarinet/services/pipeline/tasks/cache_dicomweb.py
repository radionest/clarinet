"""Built-in pipeline task: prefetch a study into the DICOMweb disk cache.

Warms the dimsechord ``DicomCache`` so OHIF serves a study without a fresh
PACS retrieve on its first request. Missing series are pulled through
dimsechord's ``PullEngine`` (C-GET by default; C-MOVE when the worker runs in
``c-move`` mode with a Storage SCP) — one study-level retrieval when the whole
study is cold, otherwise per-series — teeing every instance to
``{storage_path}/dicomweb_cache/{study_uid}/{series_uid}/`` and recording it
in the shared SQLite index (``dicomweb_cache/index.db``) the API reads.

Runs in the worker process, separate from the API server. The cache and
engine are built per task invocation from ``settings`` (no ``app.state``)
and torn down at the end so the worker never exits mid-write. The cache
layout mirrors the API's ``CacheFiller`` (``clarinet/api/app.py`` lifespan)
exactly — otherwise the warmed files would be invisible to the proxy.

Triggered from RecordFlow via ``do_task``::

    from clarinet.flow import record
    from clarinet.services.pipeline.tasks.cache_dicomweb import prefetch_dicom_web

    record("first_check").on_finished().do_task(prefetch_dicom_web)
    record("manual_review").on_finished().do_task(prefetch_dicom_web, skip_if_anon=False)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.exceptions.domain import PipelineStepError
from clarinet.services.pipeline.context import TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.task import pipeline_task
from clarinet.settings import settings
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from dimsechord import DicomCache, DicomNode, PullEngine

    from clarinet.client import ClarinetClient
    from clarinet.models.patient import PatientInfo
    from clarinet.models.study import SeriesBase, StudyBase


def _build_cache(storage_path: Path) -> DicomCache:
    """Build a ``DicomCache`` whose layout matches the API's ``CacheFiller``.

    Mirrors ``clarinet.api.app`` lifespan verbatim — same ``base_dir``,
    ``index_path`` and TTL/size knobs — so series warmed by the worker are
    visible to the DICOMweb proxy reading the same ``dicomweb_cache/index.db``.
    """
    from dimsechord import DicomCache

    cache_dir = storage_path / "dicomweb_cache"
    return DicomCache(
        base_dir=cache_dir,
        index_path=cache_dir / "index.db",
        ttl_hours=settings.dicomweb_cache_ttl_hours,
        max_size_gb=settings.dicomweb_cache_max_size_gb,
        memory_ttl_minutes=settings.dicomweb_memory_cache_ttl_minutes,
        memory_max_entries=settings.dicomweb_memory_cache_max_entries,
        disk_write_concurrency=settings.dicomweb_disk_write_concurrency,
    )


def _build_engine(cache: DicomCache, pacs: DicomNode) -> PullEngine:
    """Build a mode-based ``PullEngine`` mirroring the API lifespan.

    Default (``c-get`` / ``c-get-study``) uses ``PullEngine.via_cget`` — no
    pool or SCP needed. ``c-move`` / ``c-move-study`` drives move-to-self
    through this worker's process-global Storage SCP, started by
    ``run_worker(start_scp=True)`` when the worker is launched with
    ``--dicom AET:PORT`` (which also forces ``dicom_retrieve_mode=c-move``).
    Without a running SCP the engine has nowhere to receive instances, so a
    missing SCP is a hard error rather than a silent C-MOVE stall.

    Raises:
        PipelineStepError: c-move mode requested but no Storage SCP is running
            in this worker process.
    """
    from dimsechord import AssociationPool, PullEngine

    if settings.dicom_retrieve_mode in ("c-move", "c-move-study"):
        from clarinet.services.dicom.scp import get_storage_scp

        scp = get_storage_scp()
        if not scp.is_running:
            raise PipelineStepError(
                "prefetch_dicom_web",
                "c-move retrieve mode needs a running Storage SCP — start the worker "
                "with `--dicom AET:PORT` so move-to-self has a destination",
            )
        pool = AssociationPool(
            [settings.dicom_aet],
            per_aet_cap=settings.dicom_max_concurrent_associations,
        )
        return PullEngine(
            pool,
            scp,
            cache,
            pacs,
            max_pdu=settings.dicom_max_pdu,
            cmove_timeout=settings.dicom_cmove_timeout,
        )
    return PullEngine.via_cget(
        cache,
        pacs,
        calling_aet=settings.dicom_aet,
        max_pdu=settings.dicom_max_pdu,
        cget_timeout=settings.dicom_cmove_timeout,
    )


def _has_dcm_anon(
    storage_path: Path,
    patient: PatientInfo,
    study: StudyBase,
    series: SeriesBase,
) -> bool:
    """Check whether an anonymized copy of the series already exists.

    Renders ``settings.disk_path_template`` from pre-loaded entities to
    compute the expected ``dcm_anon`` directory — same logic
    ``CacheFiller._resolve_dcm_anon_dir`` uses. Stricter than the cache
    reader: requires at least one ``*.dcm`` file inside, so an empty
    ``dcm_anon/`` left by a failed anonymization run does not cause us to
    skip a genuinely needed prefetch.

    Synchronous — only does template rendering and a filesystem probe.
    Callers offload it to a worker thread via ``asyncio.to_thread`` so the
    event loop stays responsive when scanning many series.

    Callers must pre-load Patient/Study/Series via the API
    (``ctx.client.get_study()``) — this function performs no I/O beyond the
    filesystem probe, keeping the worker off the Postgres network surface
    that may not be reachable from every host (e.g. the Windows DICOM worker).
    """
    from clarinet.files import AnonPathError, Files
    from clarinet.models.base import DicomQueryLevel

    try:
        series_dir = Files.working_dirs(
            patient=patient,
            study=study,
            series=series,
            storage_path=storage_path,
        )[DicomQueryLevel.SERIES]
    except AnonPathError as exc:
        # Race vs anonymization run: entity exists but anon_uid hasn't
        # propagated yet. Symmetric with `CacheFiller._resolve_dcm_anon_dir`
        # (returns None silently) — we degrade to "no anon copy here, fetch
        # it via C-GET", which is the safe default.
        logger.debug(
            f"prefetch_dicom_web: cannot resolve dcm_anon path for "
            f"{study.study_uid}/{series.series_uid}: {exc}"
        )
        return False

    dcm_anon = series_dir / "dcm_anon"
    return dcm_anon.is_dir() and any(dcm_anon.glob("*.dcm"))


async def _filter_series_to_fetch(
    series_uids: list[str],
    storage_path: Path,
    cache: DicomCache,
    study_uid: str,
    skip_if_anon: bool,
    client: ClarinetClient,
) -> tuple[list[str], int, int]:
    """Partition series list into fetch / skip_cached / skip_anon.

    The disk check is the dimsechord SQLite index (``cache.series_cached``):
    a series with at least one indexed instance is already warm. Index and
    filesystem probes run in worker threads. The dcm_anon shortcut needs
    Patient/Study/Series metadata to render the storage template — one
    ``client.get_study()`` HTTP call loads them all and feeds the per-series
    fan-out, keeping the worker off the Postgres network surface.

    Degradation paths (all safe — fall back to "fetch everything"):
      * ``skip_if_anon`` is False → API call skipped entirely.
      * ``client.get_study`` returns 404 → legitimate race vs C-FIND on PACS,
        where series can arrive before the API row exists. The dcm_anon
        shortcut is bypassed; other ``ClarinetAPIError`` statuses re-raise so
        retry/DLQ surfaces them.
      * A series listed by C-FIND is missing from ``study_read.series`` (e.g.
        PACS reported a series the API hasn't imported yet) → that series goes
        to fetch.

    Returns:
        Tuple of ``(series_to_fetch, skipped_cached, skipped_anon)``.
    """
    from clarinet.client import ClarinetAPIError

    study_read = None
    patient: PatientInfo | None = None
    series_map: dict[str, SeriesBase] = {}
    if skip_if_anon:
        try:
            study_read = await client.get_study(study_uid)
            patient = study_read.patient
            series_map = {s.series_uid: s for s in study_read.series if s.series_uid}
        except ClarinetAPIError as exc:
            # Only the 404 race is benign. Auth misconfig (401/403), 5xx, and
            # httpx-wrapped transport failures must propagate to RetryMiddleware
            # → DLQ — silently re-fetching the whole study on every failure
            # would hide config drift behind gigabytes of redundant PACS traffic.
            if exc.status_code != 404:
                raise
            logger.debug(
                f"prefetch_dicom_web: study {study_uid} not in API yet "
                f"({exc}); skipping dcm_anon check, will retrieve all series"
            )

    series_to_fetch: list[str] = []
    skipped_cached = 0
    skipped_anon = 0
    for series_uid in series_uids:
        if await asyncio.to_thread(cache.series_cached, study_uid, series_uid):
            skipped_cached += 1
            continue
        if skip_if_anon and study_read is not None and patient is not None:
            series_obj = series_map.get(series_uid)
            if series_obj is not None and await asyncio.to_thread(
                _has_dcm_anon, storage_path, patient, study_read, series_obj
            ):
                skipped_anon += 1
                continue
        series_to_fetch.append(series_uid)
    return series_to_fetch, skipped_cached, skipped_anon


async def _prefetch_dicom_web_impl(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Core prefetch logic — testable without TaskIQ broker.

    This task writes files to a storage-wide cache directory rather than a
    record's working folder, so ``ctx.files`` / ``ctx.records`` stay unused.
    ``ctx.client`` IS used: ``_filter_series_to_fetch`` fetches Study metadata
    via the API to decide which series can be skipped via the ``dcm_anon``
    shortcut. This keeps the worker off Postgres — a deliberate choice so
    Windows workers (where ``5432`` is firewalled) can run this task.

    Retrieval goes through dimsechord's ``DicomCache`` + ``PullEngine`` (built
    per invocation, torn down at the end), teeing every instance to disk and
    the SQLite index the API process reads. A fully-cold study is pulled in ONE
    study-level C-GET/C-MOVE (``engine.stream_study``); a partial miss falls
    back to a per-series ``engine.ensure_series`` loop so already-cached series
    are never re-retrieved.

    Raises:
        PipelineStepError: If ``study_uid`` is missing or the retrieve returns
            0 instances across every series.
    """
    if not msg.study_uid:
        raise PipelineStepError(
            "prefetch_dicom_web",
            "study_uid is required — RecordFlow must trigger this task with "
            "a record that has a study_uid",
        )

    # Strict bool check — `bool("false") == True` would silently invert
    # user intent if the payload was carried as JSON and arrived as a string.
    raw_skip_if_anon = msg.payload.get("skip_if_anon", True)
    if not isinstance(raw_skip_if_anon, bool):
        raise PipelineStepError(
            "prefetch_dicom_web",
            f"payload.skip_if_anon must be a bool, got {type(raw_skip_if_anon).__name__}: "
            f"{raw_skip_if_anon!r}",
        )
    skip_if_anon = raw_skip_if_anon

    from clarinet.services.dicom import DicomClient, DicomNode, SeriesQuery

    pacs = DicomNode(
        aet=settings.pacs_aet,
        host=settings.pacs_host,
        port=settings.pacs_port,
    )
    client = DicomClient(
        calling_aet=settings.dicom_aet,
        max_pdu=settings.dicom_max_pdu,
    )

    # 1. Discover series via C-FIND
    series_results = await client.find_series(
        query=SeriesQuery(study_instance_uid=msg.study_uid),
        peer=pacs,
    )
    series_uids = [r.series_instance_uid for r in series_results if r.series_instance_uid]

    if not series_uids:
        logger.info(f"prefetch_dicom_web: no series found for study {msg.study_uid}, nothing to do")
        return

    storage_path = Path(settings.storage_path)

    # 2. Build the index cache (matches the API's CacheFiller layout) and
    # decide which series still need pulling. The cache owns a SQLite
    # connection + a tee thread pool, so it is always torn down below.
    cache = _build_cache(storage_path)
    try:
        series_to_fetch, skipped_cached, skipped_anon = await _filter_series_to_fetch(
            series_uids=series_uids,
            storage_path=storage_path,
            cache=cache,
            study_uid=msg.study_uid,
            skip_if_anon=skip_if_anon,
            client=ctx.client,
        )

        if not series_to_fetch:
            logger.info(
                f"prefetch_dicom_web: study {msg.study_uid} fully covered "
                f"(disk={skipped_cached}, dcm_anon={skipped_anon}), skipping retrieve"
            )
            return

        logger.info(
            f"prefetch_dicom_web: study {msg.study_uid} — fetching {len(series_to_fetch)}/"
            f"{len(series_uids)} series (disk_skipped={skipped_cached}, "
            f"anon_skipped={skipped_anon})"
        )

        # 3. Retrieve the missing series through the engine — it tees every
        # instance to disk + the SQLite index the API reads.
        #
        # Whole study cold (nothing cached, nothing anon-covered): ONE
        # study-level C-GET/C-MOVE via ``stream_study`` instead of N per-series
        # associations — ``iter_study`` issues a single study-level retrieval
        # and ``schedule_tee``s every dataset to disk + the index, matching the
        # API ``CacheFiller`` path. A partial miss keeps the per-series loop so
        # already-covered series (filtered above) are never re-retrieved.
        engine = _build_engine(cache, pacs)
        total_completed = 0
        failed_series: list[str] = []
        if len(series_to_fetch) == len(series_uids):
            async for _ds in engine.stream_study(msg.study_uid, series_to_fetch):
                total_completed += 1
        else:
            for series_uid in series_to_fetch:
                cached = await engine.ensure_series(msg.study_uid, series_uid)
                count = len(cached.instances)
                if count == 0:
                    failed_series.append(series_uid)
                    continue
                total_completed += count

        # Drain background tee writes so disk + index are durable before we
        # report success (engine tees are scheduled on a thread pool).
        await asyncio.to_thread(cache.flush_pending_writes)

        if total_completed == 0:
            raise PipelineStepError(
                "prefetch_dicom_web",
                f"Retrieved 0 instances for study {msg.study_uid} "
                f"(tried {len(series_to_fetch)} series)",
            )
        if failed_series:
            logger.error(
                f"prefetch_dicom_web: partial failure for study {msg.study_uid} — "
                f"{len(failed_series)}/{len(series_to_fetch)} series returned 0 "
                f"instances: {failed_series}"
            )

        logger.info(
            f"prefetch_dicom_web: cached study {msg.study_uid} — "
            f"{total_completed} instances across "
            f"{len(series_to_fetch) - len(failed_series)} series"
        )
    finally:
        # shutdown() does executor.shutdown(wait=True) + closes the SQLite
        # connection; run it off the loop so a slow drain can't stall other
        # concurrent worker tasks sharing this event loop.
        await asyncio.to_thread(cache.shutdown)


@pipeline_task(queue=settings.dicom_queue_name)
async def prefetch_dicom_web(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Prefetch a study into the DICOMweb disk cache via dimsechord's engine.

    Pulls each missing series through ``PullEngine`` (C-GET by default,
    C-MOVE in c-move worker mode), which tees instances to
    ``{storage_path}/dicomweb_cache/{study}/{series}/`` and the shared SQLite
    index, so OHIF picks them up on its next request without a further PACS
    retrieve.

    Idempotent: skips series already present in the disk index
    (``cache.series_cached``). Skips series available in ``dcm_anon/`` when
    ``skip_if_anon`` is ``True`` (default), since the DICOMweb proxy reads
    them directly from there.

    Payload (passed via ``do_task(prefetch_dicom_web, **kwargs)``):
        skip_if_anon (bool, default ``True``):
            Skip series whose ``dcm_anon/`` copy exists. Set to ``False`` to
            force a fresh retrieve regardless of anonymized files.

    Raises:
        PipelineStepError: If ``study_uid`` is missing or the retrieve fails
            entirely.
    """
    await _prefetch_dicom_web_impl(msg, ctx)
