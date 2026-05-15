"""Built-in pipeline task: prefetch a study into the DICOMweb disk cache.

Performs a direct C-GET against PACS and stores files under
``{storage_path}/dicomweb_cache/{study_uid}/{series_uid}/`` with a
``.cached_at`` marker so ``DicomWebCache._load_from_disk`` will treat
them as a valid cache hit on the next OHIF request.

Bypasses the in-memory tier of ``DicomWebCache`` on purpose: the worker
runs in a separate process from the API server, and routing prefetches
through ``POST /dicom-web/preload/...`` would inflate the API server's
RAM (memory tier holds whole datasets keyed by SOPInstanceUID).

Triggered from RecordFlow via ``do_task``::

    from clarinet.flow import record
    from clarinet.services.pipeline.tasks.cache_dicomweb import prefetch_dicom_web

    record("first_check").on_finished().do_task(prefetch_dicom_web)
    record("manual_review").on_finished().do_task(prefetch_dicom_web, skip_if_anon=False)
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path

import pydicom
from pydicom.errors import InvalidDicomError

from clarinet.exceptions.domain import PipelineStepError
from clarinet.services.pipeline.context import TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.task import pipeline_task
from clarinet.settings import settings
from clarinet.utils.db_manager import db_manager
from clarinet.utils.logger import logger


def _has_disk_cache(cache_base: Path, study_uid: str, series_uid: str) -> bool:
    """Check whether a series has a disk cache entry.

    Mirrors ``DicomWebCache._load_from_disk``: a ``.cached_at`` marker
    plus at least one ``*.dcm`` file. No TTL check — DICOM data on the
    PACS is immutable, and disk cache lifecycle is managed by
    ``DicomWebCacheCleanupService``.
    """
    series_dir = cache_base / study_uid / series_uid
    if not (series_dir / ".cached_at").exists():
        return False
    return any(series_dir.glob("*.dcm"))


async def _has_dcm_anon(storage_path: Path, study_uid: str, series_uid: str) -> bool:
    """Check whether an anonymized copy of the series already exists.

    Renders ``settings.disk_path_template`` from DB state to compute the
    expected ``dcm_anon`` directory — same logic
    ``DicomWebCache._resolve_dcm_anon_dir`` uses. Stricter than the cache
    reader: requires at least one ``*.dcm`` file inside, so an empty
    ``dcm_anon/`` left by a failed anonymization run does not cause us to
    skip a genuinely needed prefetch.
    """
    from clarinet.models.base import DicomQueryLevel
    from clarinet.models.patient import Patient
    from clarinet.models.study import Series, Study
    from clarinet.services.common.storage_paths import (
        AnonPathError,
        build_context,
        render_working_folder,
    )

    async with db_manager.async_session_factory() as session:
        series = await session.get(Series, series_uid)
        study = await session.get(Study, study_uid) if series else None
        patient = await session.get(Patient, study.patient_id) if study else None

    if not (series and study and patient):
        return False

    try:
        ctx = build_context(
            patient=patient,
            study=study,
            series=series,
            template=settings.disk_path_template,
        )
        series_dir = render_working_folder(
            settings.disk_path_template, DicomQueryLevel.SERIES, ctx, storage_path
        )
    except AnonPathError as exc:
        logger.warning(
            f"prefetch_dicom_web: cannot resolve dcm_anon path for {study_uid}/{series_uid}: {exc}"
        )
        return False

    dcm_anon = series_dir / "dcm_anon"
    return await asyncio.to_thread(lambda: dcm_anon.is_dir() and any(dcm_anon.glob("*.dcm")))


def _organize_to_cache(tmp_dir: Path, cache_base: Path, study_uid: str) -> dict[str, int]:
    """Move retrieved DICOM files into ``dicomweb_cache`` structure.

    Reads only ``SeriesInstanceUID`` and ``SOPInstanceUID`` (no pixel
    data), then moves the file via ``shutil.move`` — ``os.rename`` on
    the same filesystem (guaranteed because ``tmp_dir`` is created
    inside ``cache_base``).

    Publication is atomic from the OHIF reader's point of view: for each
    series that receives at least one new file, the previous ``.cached_at``
    marker is removed *first*, then pre-existing ``*.dcm`` files are
    unlinked, then fresh files are moved in, and finally a new marker is
    written. This guarantees the API process never sees a series whose
    marker is present but whose directory contains a mix of stale and
    fresh instances mid-write.

    Returns:
        Mapping ``series_uid → instance count`` for the series that
        actually received files.
    """
    grouped: dict[str, int] = {}
    cleaned_series: set[str] = set()
    for dcm_path in tmp_dir.rglob("*.dcm"):
        try:
            ds = pydicom.dcmread(
                dcm_path,
                stop_before_pixels=True,
                specific_tags=["SeriesInstanceUID", "SOPInstanceUID"],
            )
        except (InvalidDicomError, OSError, AttributeError) as exc:
            logger.warning(f"Skipping unreadable retrieved DICOM {dcm_path}: {exc}")
            continue

        # Tags may be absent even after a successful read — pydicom's
        # specific_tags hint is best-effort, not a presence guarantee.
        series_uid_attr = getattr(ds, "SeriesInstanceUID", None)
        sop_uid_attr = getattr(ds, "SOPInstanceUID", None)
        if not series_uid_attr or not sop_uid_attr:
            logger.warning(
                f"Skipping retrieved DICOM with missing UIDs: {dcm_path} "
                f"(SeriesInstanceUID={series_uid_attr!r}, "
                f"SOPInstanceUID={sop_uid_attr!r})"
            )
            continue

        series_uid = str(series_uid_attr)
        sop_uid = str(sop_uid_attr)
        target_dir = cache_base / study_uid / series_uid
        target_dir.mkdir(parents=True, exist_ok=True)

        # Clear stale state from a previous cache entry before writing
        # the first new instance for this series. Marker is removed
        # first so the API reader never sees a present marker pointing
        # at a mix of stale and fresh *.dcm files during the publish.
        if series_uid not in cleaned_series:
            (target_dir / ".cached_at").unlink(missing_ok=True)
            for stale in target_dir.glob("*.dcm"):
                stale.unlink(missing_ok=True)
            cleaned_series.add(series_uid)

        target_path = target_dir / f"{sop_uid}.dcm"
        shutil.move(str(dcm_path), str(target_path))
        grouped[series_uid] = grouped.get(series_uid, 0) + 1

    now = str(time.time())
    for series_uid in grouped:
        (cache_base / study_uid / series_uid / ".cached_at").write_text(now)

    return grouped


async def _filter_series_to_fetch(
    series_uids: list[str],
    storage_path: Path,
    cache_base: Path,
    study_uid: str,
    skip_if_anon: bool,
) -> tuple[list[str], int, int]:
    """Partition series list into fetch / skip_cached / skip_anon.

    Filesystem scans run in worker threads, DB lookups go through the
    shared session factory (sequential — single ``AsyncSession`` per
    series is fine and avoids the concurrency-safety constraints).

    Returns:
        Tuple of ``(series_to_fetch, skipped_cached, skipped_anon)``.
    """
    series_to_fetch: list[str] = []
    skipped_cached = 0
    skipped_anon = 0
    for series_uid in series_uids:
        if await asyncio.to_thread(_has_disk_cache, cache_base, study_uid, series_uid):
            skipped_cached += 1
            continue
        if skip_if_anon and await _has_dcm_anon(storage_path, study_uid, series_uid):
            skipped_anon += 1
            continue
        series_to_fetch.append(series_uid)
    return series_to_fetch, skipped_cached, skipped_anon


async def _prefetch_dicom_web_impl(msg: PipelineMessage, _ctx: TaskContext) -> None:
    """Core prefetch logic — testable without TaskIQ broker.

    The ``TaskContext`` is intentionally unused: this task writes to a
    storage-wide cache directory rather than to a record's working folder,
    so it does not need ``ctx.files`` / ``ctx.records``. The parameter is
    kept for ``pipeline_task`` wrapper compatibility.

    Raises:
        PipelineStepError: If ``study_uid`` is missing or C-GET returns 0 instances.
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

    # 2. Decide which series need prefetching. Filesystem scans run off
    # the event loop — large deployments may have thousands of patient
    # directories to walk for the dcm_anon check.
    storage_path = Path(settings.storage_path)
    cache_base = storage_path / "dicomweb_cache"

    series_to_fetch, skipped_cached, skipped_anon = await _filter_series_to_fetch(
        series_uids,
        storage_path,
        cache_base,
        msg.study_uid,
        skip_if_anon,
    )

    if not series_to_fetch:
        logger.info(
            f"prefetch_dicom_web: study {msg.study_uid} fully covered "
            f"(disk={skipped_cached}, dcm_anon={skipped_anon}), skipping C-GET"
        )
        return

    logger.info(
        f"prefetch_dicom_web: study {msg.study_uid} — fetching {len(series_to_fetch)}/"
        f"{len(series_uids)} series (disk_skipped={skipped_cached}, "
        f"anon_skipped={skipped_anon})"
    )

    # 3. C-GET to a temp dir, then organize into the cache layout.
    # The temp dir is created *inside* cache_base so shutil.move in
    # _organize_to_cache can use atomic os.rename (same filesystem).
    # With /tmp on a different mount (Docker, NFS), move would fall back
    # to copy2+unlink — non-atomic, and a concurrent OHIF request could
    # read a partially-written DICOM. The ".prefetch-" prefix keeps the
    # temp dir invisible to DicomWebCacheCleanupService, which looks for
    # ".cached_at" markers.
    cache_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache_base, prefix=".prefetch-") as tmp:
        tmp_path = Path(tmp)

        # If everything is missing, one study-level C-GET avoids N associations.
        # Otherwise, fall back to per-series retrieval to avoid downloading
        # series that are already cached.
        if len(series_to_fetch) == len(series_uids):
            result = await client.get_study(
                study_uid=msg.study_uid,
                peer=pacs,
                output_dir=tmp_path,
            )
            if result.num_completed == 0:
                raise PipelineStepError(
                    "prefetch_dicom_web",
                    f"Study C-GET returned 0 instances for study {msg.study_uid}",
                )
            total_completed = result.num_completed
        else:
            total_completed = 0
            failed_series: list[str] = []
            for series_uid in series_to_fetch:
                result = await client.get_series(
                    study_uid=msg.study_uid,
                    series_uid=series_uid,
                    peer=pacs,
                    output_dir=tmp_path,
                )
                if result.num_completed == 0:
                    failed_series.append(series_uid)
                    continue
                total_completed += result.num_completed

            if total_completed == 0:
                raise PipelineStepError(
                    "prefetch_dicom_web",
                    f"Per-series C-GET retrieved 0 instances for study {msg.study_uid} "
                    f"(all {len(failed_series)} series failed)",
                )
            if failed_series:
                logger.error(
                    f"prefetch_dicom_web: partial failure for study {msg.study_uid} — "
                    f"{len(failed_series)}/{len(series_to_fetch)} series returned 0 "
                    f"instances: {failed_series}"
                )

        grouped = await asyncio.to_thread(_organize_to_cache, tmp_path, cache_base, msg.study_uid)

    logger.info(
        f"prefetch_dicom_web: cached study {msg.study_uid} — "
        f"{total_completed} instances across {len(grouped)} series"
    )


@pipeline_task(queue=settings.dicom_queue_name)
async def prefetch_dicom_web(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Prefetch a study into the DICOMweb disk cache via direct C-GET.

    Writes files directly to ``{storage_path}/dicomweb_cache/{study}/{series}/``
    so OHIF picks them up on its next request without further C-GET.
    Skips the in-memory tier to avoid RAM bloat on the API server.

    Idempotent: skips series that already have a valid disk cache entry
    (``.cached_at`` marker within TTL). Skips series available in
    ``dcm_anon/`` when ``skip_if_anon`` is ``True`` (default), since the
    DICOMweb proxy reads them directly from there.

    Payload (passed via ``do_task(prefetch_dicom_web, **kwargs)``):
        skip_if_anon (bool, default ``True``):
            Skip series whose ``dcm_anon/`` copy exists. Set to ``False``
            to force a fresh C-GET regardless of anonymized files.

    Raises:
        PipelineStepError: If ``study_uid`` is missing or C-GET fails entirely.
    """
    await _prefetch_dicom_web_impl(msg, ctx)
