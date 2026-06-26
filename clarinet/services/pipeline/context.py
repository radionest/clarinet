"""
TaskContext system for pipeline tasks.

Provides RecordQuery (async record lookup) and TaskContext (container) to
eliminate boilerplate in pipeline tasks. The ``FileResolver`` class
itself lives in ``clarinet.services.common.file_resolver`` so that
non-pipeline callers (API routers, Slicer context builder, record
service) can use it without dragging the broker / TaskIQ import chain.
``FileResolver`` is re-exported here for backward-compat with existing
``from clarinet.services.pipeline.context import FileResolver`` callers.

Example:
    @pipeline_task()
    async def my_task(msg: PipelineMessage, ctx: TaskContext):
        if ctx.files.exists(master_model):
            return
        seg_path = await ctx.records.file_path(
            "segment-ct-single", file="segmentation",
            series_uid=msg.series_uid,
        )
        img.save(result, ctx.files.resolve(master_model))
        await ctx.client.update_record_data(msg.record_id, {...})
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.exceptions.domain import PipelineStepError
from clarinet.files import Files
from clarinet.models.base import RecordStatus
from clarinet.services.common.file_resolver import FileResolver
from clarinet.utils.logger import logger

# Backward-compat alias — older code spells the helper with a leading underscore.
_resolve_pattern_from_dict = Files.render_template

if TYPE_CHECKING:
    from clarinet.client import ClarinetClient
    from clarinet.models.record import RecordRead

    from .message import PipelineMessage


__all__ = [
    "FileResolver",
    "RecordQuery",
    "TaskContext",
    "build_task_context",
]


class RecordQuery:
    """Async record lookup via the Clarinet HTTP API.

    Args:
        client: Authenticated ``ClarinetClient``.
        files: File resolver for the current task context.
    """

    def __init__(self, client: ClarinetClient, files: Files) -> None:
        self._client = client
        self._files = files

    async def find(
        self,
        type_name: str,
        *,
        series_uid: str | None = None,
        study_uid: str | None = None,
        patient_id: str | None = None,
        status: RecordStatus | None = None,
        limit: int = 100,
    ) -> list[RecordRead]:
        """Find records by criteria.

        Args:
            type_name: Record type name filter.
            series_uid: Optional series UID filter.
            study_uid: Optional study UID filter.
            patient_id: Optional patient ID filter.
            status: Optional ``RecordStatus`` filter.
            limit: Max results (default 100).

        Returns:
            List of matching ``RecordRead`` objects.
        """
        return await self._client.find_records_advanced(
            record_type_name=type_name,
            series_uid=series_uid,
            study_uid=study_uid,
            patient_id=patient_id,
            record_status=status,
            limit=limit,
        )

    async def file_path(
        self,
        type_name: str,
        *,
        file: str,
        series_uid: str | None = None,
        study_uid: str | None = None,
        patient_id: str | None = None,
        status: RecordStatus | None = None,
    ) -> Path:
        """Find a record and resolve a file path from it.

        Sugar method: finds a single record, then resolves the named file
        to an absolute ``Path``.

        Args:
            type_name: Record type name to search for.
            file: File definition name to resolve.
            series_uid: Optional series UID filter.
            study_uid: Optional study UID filter.
            patient_id: Optional patient ID filter.
            status: Optional ``RecordStatus`` filter.

        Returns:
            Absolute path to the resolved file.

        Raises:
            PipelineStepError: If no record is found.
        """
        records = await self.find(
            type_name,
            series_uid=series_uid,
            study_uid=study_uid,
            patient_id=patient_id,
            status=status,
            limit=1,
        )
        if not records:
            raise PipelineStepError(
                type_name,
                f"No record found (series_uid={series_uid}, study_uid={study_uid}, "
                f"patient_id={patient_id})",
            )
        record = records[0]

        # Check file_links first — if a link with matching name exists, use its filename
        f = Files(record)
        file_registry = record.record_type.file_registry or []
        fd_map = {fd.name: fd for fd in file_registry}
        if record.file_links:
            for link in record.file_links:
                if link.name == file:
                    fd = fd_map.get(file)
                    level = (fd.level if fd else None) or record.record_type.level
                    return f.dir(level) / link.filename

        # Fallback to pattern resolution
        if file not in fd_map:
            raise PipelineStepError(
                type_name,
                f"File definition '{file}' not found in record type '{record.record_type.name}'",
            )
        return f.resolve(fd_map[file])


@dataclass
class TaskContext:
    """Container for pipeline task context.

    Attributes:
        files: Sync file path resolver for the task's own record.
        records: Async record query helper.
        client: Authenticated HTTP client.
        msg: The parsed pipeline message.
    """

    files: Files
    records: RecordQuery
    client: ClarinetClient
    msg: PipelineMessage

    def files_for(self, record: RecordRead) -> Files:
        """Build a resolver for *another* record you already hold.

        ``files`` resolves the task's own record (``msg.record_id``); use
        ``files_for`` to resolve files of a different ``RecordRead`` — a
        parent, a reloaded copy, or a cross-patient record — without
        reassembling the resolver by hand. For lookup-by-criteria use
        ``records.file_path`` instead.
        """
        return Files(record)


async def build_task_context(msg: PipelineMessage, client: ClarinetClient) -> TaskContext:
    """Build a ``TaskContext`` from a pipeline message.

    Makes at most one HTTP call to fetch the primary entity, then builds
    all context objects from it.

    Fallback chain:
    1. ``msg.record_id`` → ``client.get_record()``
    2. ``msg.series_uid`` → ``client.get_series()``
    3. ``msg.study_uid``  → ``client.get_study()``
    4. Nothing → minimal empty context

    Args:
        msg: The pipeline message.
        client: Authenticated ``ClarinetClient``.

    Returns:
        Fully initialised ``TaskContext``.
    """
    if msg.record_id is not None:
        record = await client.get_record(msg.record_id)
        parent: RecordRead | None = None
        if record.parent_record_id is not None:
            try:
                parent = await client.get_record(record.parent_record_id)
            except Exception:
                logger.debug(f"Could not load parent {record.parent_record_id} for origin_type")
        files = Files(record, parent=parent)
        logger.debug(f"TaskContext built from record_id={msg.record_id}")

    elif msg.series_uid is not None:
        series = await client.get_series(msg.series_uid)
        files = Files(series)
        logger.debug(f"TaskContext built from series_uid={msg.series_uid}")

    elif msg.study_uid:
        study = await client.get_study(msg.study_uid)
        files = Files(study)
        logger.debug(f"TaskContext built from study_uid={msg.study_uid}")

    else:
        files = Files.empty()
        logger.debug("TaskContext built with minimal empty context")

    records = RecordQuery(client=client, files=files)
    return TaskContext(files=files, records=records, client=client, msg=msg)
