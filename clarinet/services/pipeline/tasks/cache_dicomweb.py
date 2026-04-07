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

import shutil
import tempfile
import time
from pathlib import Path

import pydicom

from clarinet.exceptions.domain import PipelineStepError
from clarinet.services.pipeline.context import TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.task import pipeline_task
from clarinet.settings import settings
from clarinet.utils.logger import logger


def _is_disk_cache_valid(
    cache_base: Path, study_uid: str, series_uid: str, ttl_seconds: int
) -> bool:
    """Check whether a series already has a non-expired disk cache entry.

    Mirrors the validity check in ``DicomWebCache._load_from_disk``: a
    ``.cached_at`` marker must exist, hold a parseable timestamp within
    TTL, and the directory must contain at least one ``*.dcm`` file.
    """
    series_dir = cache_base / study_uid / series_uid
    marker = series_dir / ".cached_at"
    if not marker.exists():
        return False
    try:
        cached_at = float(marker.read_text().strip())
    except (ValueError, OSError):
        return False
    if time.time() - cached_at > ttl_seconds:
        return False
    return any(series_dir.glob("*.dcm"))


def _has_dcm_anon(storage_path: Path, study_uid: str, series_uid: str) -> bool:
    """Check whether an anonymized copy of the series already exists.

    Walks ``{storage_path}/*/{study_uid}/{series_uid}/dcm_anon/*.dcm``
    across patient directories — same lookup that ``DicomWebCache`` uses
    for its dcm_anon tier. Stricter than ``DicomWebCache._find_dcm_anon_dir``:
    requires at least one ``*.dcm`` file to be present, so that an empty
    ``dcm_anon/`` left by a failed anonymization run does not cause us
    to skip a genuinely needed prefetch.
    """
    if not storage_path.exists():
        return False
    for patient_dir in storage_path.iterdir():
        if not patient_dir.is_dir():
            continue
        dcm_anon = patient_dir / study_uid / series_uid / "dcm_anon"
        if dcm_anon.is_dir() and any(dcm_anon.glob("*.dcm")):
            return True
    return False


def _organize_to_cache(tmp_dir: Path, cache_base: Path, study_uid: str) -> dict[str, int]:
    """Move retrieved DICOM files into ``dicomweb_cache`` structure.

    Reads only ``SeriesInstanceUID`` and ``SOPInstanceUID`` (no pixel
    data), then moves the file via ``shutil.move`` — fast when source
    and destination share a filesystem.

    Writes a ``.cached_at`` marker per series so the disk tier in
    ``DicomWebCache`` recognises the entry on the next OHIF request.

    Returns:
        Mapping ``series_uid → instance count`` for the series that
        actually received files.
    """
    grouped: dict[str, int] = {}
    for dcm_path in tmp_dir.rglob("*.dcm"):
        try:
            ds = pydicom.dcmread(
                dcm_path,
                stop_before_pixels=True,
                specific_tags=["SeriesInstanceUID", "SOPInstanceUID"],
            )
        except Exception as exc:
            logger.warning(f"Skipping unreadable retrieved DICOM {dcm_path}: {exc}")
            continue

        series_uid = str(ds.SeriesInstanceUID)
        sop_uid = str(ds.SOPInstanceUID)
        target_dir = cache_base / study_uid / series_uid
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{sop_uid}.dcm"
        shutil.move(str(dcm_path), str(target_path))
        grouped[series_uid] = grouped.get(series_uid, 0) + 1

    now = str(time.time())
    for series_uid in grouped:
        (cache_base / study_uid / series_uid / ".cached_at").write_text(now)

    return grouped


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

    skip_if_anon = bool(msg.payload.get("skip_if_anon", True))

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

    # 2. Decide which series need prefetching
    storage_path = Path(settings.storage_path)
    cache_base = storage_path / "dicomweb_cache"
    ttl_seconds = settings.dicomweb_cache_ttl_hours * 3600

    series_to_fetch: list[str] = []
    skipped_cached = 0
    skipped_anon = 0
    for series_uid in series_uids:
        if _is_disk_cache_valid(cache_base, msg.study_uid, series_uid, ttl_seconds):
            skipped_cached += 1
            continue
        if skip_if_anon and _has_dcm_anon(storage_path, msg.study_uid, series_uid):
            skipped_anon += 1
            continue
        series_to_fetch.append(series_uid)

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
            for series_uid in series_to_fetch:
                result = await client.get_series(
                    study_uid=msg.study_uid,
                    series_uid=series_uid,
                    peer=pacs,
                    output_dir=tmp_path,
                )
                if result.num_completed == 0:
                    logger.warning(
                        f"prefetch_dicom_web: C-GET returned 0 instances for "
                        f"series {series_uid} (study {msg.study_uid})"
                    )
                    continue
                total_completed += result.num_completed

            if total_completed == 0:
                raise PipelineStepError(
                    "prefetch_dicom_web",
                    f"Per-series C-GET retrieved 0 instances for study {msg.study_uid}",
                )

        from asyncio import to_thread

        grouped = await to_thread(_organize_to_cache, tmp_path, cache_base, msg.study_uid)

    logger.info(
        f"prefetch_dicom_web: cached study {msg.study_uid} — "
        f"{total_completed} instances across {len(grouped)} series"
    )


@pipeline_task(queue="clarinet.dicom")
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
