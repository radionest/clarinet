"""DICOMweb proxy service — translates DICOMweb HTTP semantics to DICOM Q/R operations."""

import asyncio
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from dimsechord import (
    build_multipart_response,
    convert_datasets_to_dicom_json,
    extract_frames_from_dataset,
    image_result_to_dicom_json,
    series_result_to_dicom_json,
    study_result_to_dicom_json,
)
from pydicom import Dataset

from clarinet.services.dicom.client import DicomClient
from clarinet.services.dicom.models import (
    DicomNode,
    ImageQuery,
    SeriesQuery,
    StudyQuery,
)
from clarinet.services.dicomweb.cache import DicomWebCache
from clarinet.services.dicomweb.models import MemoryCachedSeries
from clarinet.services.events.bus import get_event_bus
from clarinet.services.events.models import TaskProgressEvent
from clarinet.utils.logger import logger


def _build_study_metadata(
    cached_map: dict[str, MemoryCachedSeries],
    series_uids: list[str],
    base_url: str,
) -> list[dict[str, Any]]:
    """Build DICOM JSON metadata for all series in a study (CPU-bound, run via to_thread).

    Args:
        cached_map: Mapping of series_uid → MemoryCachedSeries
        series_uids: Ordered list of series UIDs to include
        base_url: Base URL for constructing BulkDataURIs

    Returns:
        Combined list of DICOM JSON metadata objects
    """
    all_metadata: list[dict[str, Any]] = []
    for series_uid in series_uids:
        cached = cached_map.get(series_uid)
        if cached is None:
            continue
        all_metadata.extend(
            convert_datasets_to_dicom_json(list(cached.instances.values()), base_url)
        )
    return all_metadata


class DicomWebProxyService:
    """Proxy that translates DICOMweb requests into DICOM C-FIND/C-GET operations.

    Supports QIDO-RS (search) and WADO-RS (retrieve) operations, using a two-tier
    memory+disk cache to avoid repeated C-GET retrievals and enable O(1) instance lookup.
    """

    def __init__(
        self,
        client: DicomClient,
        pacs: DicomNode,
        cache: DicomWebCache,
    ):
        """Initialize the proxy service.

        Args:
            client: DICOM client for Q/R operations
            pacs: Target PACS node configuration
            cache: Two-tier cache for retrieved series
        """
        self._client = client
        self._pacs = pacs
        self._cache = cache
        self._preload_tasks: set[asyncio.Task[None]] = set()

    async def search_studies(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """QIDO-RS: Search for studies via C-FIND.

        Args:
            params: DICOMweb query parameters (e.g. PatientID, StudyDate)

        Returns:
            List of DICOM JSON objects
        """
        query = StudyQuery(
            patient_id=params.get("PatientID") or params.get("00100020"),
            patient_name=params.get("PatientName") or params.get("00100010"),
            study_instance_uid=params.get("StudyInstanceUID") or params.get("0020000D"),
            study_date=params.get("StudyDate") or params.get("00080020"),
            study_description=params.get("StudyDescription") or params.get("00081030"),
            accession_number=params.get("AccessionNumber") or params.get("00080050"),
            modality=params.get("ModalitiesInStudy") or params.get("00080061"),
        )

        results = await self._client.find_studies(query=query, peer=self._pacs)
        logger.info(f"QIDO-RS: found {len(results)} studies")
        return [study_result_to_dicom_json(r) for r in results]

    async def search_series(self, study_uid: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """QIDO-RS: Search for series within a study via C-FIND.

        Args:
            study_uid: Study Instance UID
            params: DICOMweb query parameters

        Returns:
            List of DICOM JSON objects
        """
        query = SeriesQuery(
            study_instance_uid=study_uid,
            series_instance_uid=params.get("SeriesInstanceUID") or params.get("0020000E"),
            modality=params.get("Modality") or params.get("00080060"),
            series_number=params.get("SeriesNumber") or params.get("00200011"),
            series_description=params.get("SeriesDescription") or params.get("0008103E"),
        )

        results = await self._client.find_series(query=query, peer=self._pacs)
        logger.info(f"QIDO-RS: found {len(results)} series for study {study_uid}")
        return [series_result_to_dicom_json(r) for r in results]

    async def search_instances(
        self, study_uid: str, series_uid: str, params: dict[str, str]
    ) -> list[dict[str, Any]]:
        """QIDO-RS: Search for instances within a series via C-FIND.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            params: DICOMweb query parameters

        Returns:
            List of DICOM JSON objects
        """
        query = ImageQuery(
            study_instance_uid=study_uid,
            series_instance_uid=series_uid,
            sop_instance_uid=params.get("SOPInstanceUID") or params.get("00080018"),
            instance_number=params.get("InstanceNumber") or params.get("00200013"),
        )

        results = await self._client.find_images(query=query, peer=self._pacs)
        logger.info(f"QIDO-RS: found {len(results)} instances for series {series_uid}")
        return [image_result_to_dicom_json(r) for r in results]

    async def retrieve_series_metadata(
        self, study_uid: str, series_uid: str, base_url: str
    ) -> list[dict[str, Any]]:
        """WADO-RS: Retrieve metadata for all instances in a series.

        Uses in-memory cached datasets (no disk I/O on hot path). PixelData is
        skipped during JSON serialization via a bulk data handler in the converter,
        so the original dataset is never mutated.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            base_url: Base URL for constructing BulkDataURIs

        Returns:
            List of DICOM JSON metadata objects
        """
        cached = await self._cache.ensure_series_cached(
            study_uid=study_uid,
            series_uid=series_uid,
            client=self._client,
            pacs=self._pacs,
        )

        metadata = await asyncio.to_thread(
            convert_datasets_to_dicom_json, list(cached.instances.values()), base_url
        )
        logger.info(f"WADO-RS metadata: {len(metadata)} instances for series {series_uid}")
        return metadata

    async def retrieve_study_metadata(self, study_uid: str, base_url: str) -> list[dict[str, Any]]:
        """WADO-RS: Retrieve metadata for all instances in a study.

        Discovers series via C-FIND, then retrieves all series with a single study-level
        C-GET (instead of N parallel per-series C-GETs) to avoid PACS association overload.

        Args:
            study_uid: Study Instance UID
            base_url: Base URL for constructing BulkDataURIs

        Returns:
            List of DICOM JSON metadata objects for all instances in the study
        """
        results = await self._client.find_series(
            query=SeriesQuery(study_instance_uid=study_uid), peer=self._pacs
        )
        series_uids = [r.series_instance_uid for r in results if r.series_instance_uid]

        if not series_uids:
            return []

        # Single study-level C-GET instead of N per-series C-GETs
        cached_map = await self._cache.ensure_study_cached(
            study_uid=study_uid,
            series_uids=series_uids,
            client=self._client,
            pacs=self._pacs,
        )

        all_metadata = await asyncio.to_thread(
            _build_study_metadata, cached_map, series_uids, base_url
        )

        logger.info(
            f"WADO-RS study metadata: {len(all_metadata)} instances "
            f"across {len(series_uids)} series for study {study_uid}"
        )
        return all_metadata

    async def retrieve_frames(
        self,
        study_uid: str,
        series_uid: str,
        instance_uid: str,
        frame_numbers: list[int],
    ) -> tuple[bytes, str]:
        """WADO-RS: Retrieve pixel data frames for a specific instance.

        Uses O(1) dict lookup instead of linear scan through cached files.
        Falls back to disk read if the instance has been evicted from memory.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instance_uid: SOP Instance UID
            frame_numbers: 1-based frame numbers to retrieve

        Returns:
            Tuple of (multipart response body, Content-Type header)

        Raises:
            FileNotFoundError: If the instance is not found in cache
        """
        cached = await self._cache.ensure_series_cached(
            study_uid=study_uid,
            series_uid=series_uid,
            client=self._client,
            pacs=self._pacs,
        )

        ds = cached.instances.get(instance_uid)

        if ds is None:
            # Fallback: try reading from disk if memory entry is incomplete
            logger.debug(
                f"Instance {instance_uid} not in memory cache "
                f"(cache has {len(cached.instances)} instances, "
                f"keys sample: {list(cached.instances.keys())[:3]})"
            )
            ds = await self._read_instance_from_disk(study_uid, series_uid, instance_uid)

        if ds is None:
            raise FileNotFoundError(
                f"Instance {instance_uid} not found in cached series {series_uid}"
            )

        # Check if PixelData is present; if not, re-read from disk
        if not hasattr(ds, "PixelData") or ds.PixelData is None:
            logger.warning(
                f"PixelData missing from cached instance {instance_uid} — attempting disk fallback"
            )
            disk_ds = await self._read_instance_from_disk(study_uid, series_uid, instance_uid)
            if disk_ds is not None:
                ds = disk_ds
            else:
                raise FileNotFoundError(f"PixelData not available for instance {instance_uid}")

        frames = extract_frames_from_dataset(ds, frame_numbers)
        if not frames:
            raise FileNotFoundError(f"No pixel data frames found for instance {instance_uid}")
        body, content_type = build_multipart_response(frames)

        logger.debug(f"WADO-RS frames: {len(frames)} frames for instance {instance_uid}")
        return body, content_type

    async def start_preload(self, study_uids: list[str], user_id: UUID | None = None) -> str:
        """Start background preloading of one or more studies into cache.

        Studies are fetched sequentially (same rationale as the study-level
        C-GET: avoid overloading the PACS with concurrent associations).
        ``user_id`` addresses SSE progress pushes to the initiating user.
        Returns a task_id that can be used to poll progress.
        """
        task_id = f"preload_{uuid4().hex[:12]}"
        self._cache.set_preload_progress(task_id, {"status": "starting", "received": 0})
        task = asyncio.create_task(self._preload_worker(study_uids, task_id, user_id))
        self._preload_tasks.add(task)
        task.add_done_callback(self._preload_tasks.discard)
        return task_id

    async def _preload_worker(
        self, study_uids: list[str], task_id: str, user_id: UUID | None = None
    ) -> None:
        """Sequentially cache each study, aggregating progress across all of them.

        Fail-fast: an error on study N leaves studies 1..N-1 warm in cache and
        reports status="error" — a retry resumes faster, no partial-success state.
        """
        progress = self._cache.get_preload_progress(task_id)
        if progress is None:
            return

        last_push = 0.0

        def _publish(force: bool = False) -> None:
            """Push a progress frame over SSE (throttled to ~1 per 500ms).

            Called from pynetdicom worker threads via on_progress, so it must
            use ``publish_threadsafe``. Terminal frames pass force=True.
            """
            nonlocal last_push
            now = monotonic()
            if not force and now - last_push < 0.5:
                return
            last_push = now
            bus = get_event_bus()
            snapshot = self._cache.get_preload_progress(task_id)
            if bus is not None and snapshot is not None:
                bus.publish_threadsafe(
                    TaskProgressEvent(
                        task="preload",
                        task_id=task_id,
                        payload=dict(snapshot),
                        user_id=user_id,
                    )
                )

        total_received = 0
        try:
            for idx, study_uid in enumerate(study_uids, start=1):
                progress.update(
                    status="fetching",
                    study_index=idx,
                    study_count=len(study_uids),
                    study_received=0,
                    study_total=None,
                )
                _publish()
                results = await self._client.find_series(
                    query=SeriesQuery(study_instance_uid=study_uid), peer=self._pacs
                )
                series_uids = [r.series_instance_uid for r in results if r.series_instance_uid]
                if not series_uids:
                    continue

                # _base default binds the current value — a plain closure would
                # late-bind and every callback would see the final total_received
                def on_progress(
                    received: int, total: int | None, _base: int = total_received
                ) -> None:
                    progress.update(
                        status="fetching",
                        received=_base + received,
                        study_received=received,
                        study_total=total,
                    )
                    _publish()

                cached_map = await self._cache.ensure_study_cached(
                    study_uid,
                    series_uids,
                    self._client,
                    self._pacs,
                    on_progress=on_progress,
                )
                total_received += sum(len(e.instances) for e in cached_map.values())
            progress.update(status="ready", received=total_received, total=total_received)
            _publish(force=True)
        except Exception as e:
            logger.error(f"Preload failed (task {task_id}): {e}")
            self._cache.set_preload_progress(task_id, {"status": "error", "error": str(e)})
            _publish(force=True)

    def get_preload_progress(self, task_id: str) -> dict[str, Any] | None:
        return self._cache.get_preload_progress(task_id)

    async def _read_instance_from_disk(
        self, study_uid: str, series_uid: str, instance_uid: str
    ) -> Dataset | None:
        """Fallback: read a single instance from disk cache.

        Args:
            study_uid: Study Instance UID
            series_uid: Series Instance UID
            instance_uid: SOP Instance UID

        Returns:
            pydicom Dataset or None if not found on disk
        """
        return await self._cache.read_instance_from_disk(study_uid, series_uid, instance_uid)
