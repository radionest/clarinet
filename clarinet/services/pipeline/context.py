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
from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import PipelineStepError
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.services.common.file_resolver import (
    FileResolver,
    resolve_pattern_from_dict,
)
from clarinet.utils.file_patterns import resolve_origin_type
from clarinet.utils.logger import logger

# Backward-compat alias — older code spells the helper with a leading underscore.
_resolve_pattern_from_dict = resolve_pattern_from_dict

if TYPE_CHECKING:
    from clarinet.client import ClarinetClient
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.record import RecordRead

    from .message import PipelineMessage


__all__ = [
    "FileResolver",
    "RecordQuery",
    "TaskContext",
    "build_task_context",
    "resolve_pattern_from_dict",
]


class RecordQuery:
    """Async record lookup via the Clarinet HTTP API.

    Args:
        client: Authenticated ``ClarinetClient``.
        files: ``FileResolver`` for the current task context.
    """

    def __init__(self, client: ClarinetClient, files: FileResolver) -> None:
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
        if record.file_links:
            for link in record.file_links:
                if link.name == file:
                    working_dirs = FileResolver.build_working_dirs(record)
                    file_registry = record.record_type.file_registry or []
                    fd_map = {fd.name: fd for fd in file_registry}
                    fd = fd_map.get(file)
                    level = (fd.level if fd else None) or record.record_type.level
                    return working_dirs[level] / link.filename

        # Fallback to pattern resolution
        working_dirs = FileResolver.build_working_dirs(record)
        fields = FileResolver.build_fields(record)
        file_registry = record.record_type.file_registry or []
        fd_map = {fd.name: fd for fd in file_registry}
        if file not in fd_map:
            raise PipelineStepError(
                type_name,
                f"File definition '{file}' not found in record type '{record.record_type.name}'",
            )
        fd = fd_map[file]
        level = fd.level or record.record_type.level
        filename = resolve_pattern_from_dict(fd.pattern, fields)
        return working_dirs[level] / filename


@dataclass
class TaskContext:
    """Container for pipeline task context.

    Attributes:
        files: Sync file path resolver.
        records: Async record query helper.
        client: Authenticated HTTP client.
        msg: The parsed pipeline message.
    """

    files: FileResolver
    records: RecordQuery
    client: ClarinetClient
    msg: PipelineMessage


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
    working_dirs: dict[DicomQueryLevel, Path] = {}
    file_registry: list[FileDefinitionRead] = []
    fields: dict[str, Any] = {}
    record_type_level = DicomQueryLevel.SERIES

    if msg.record_id is not None:
        record = await client.get_record(msg.record_id)
        working_dirs = FileResolver.build_working_dirs(record)
        fields = FileResolver.build_fields(record)
        file_registry = record.record_type.file_registry or []
        record_type_level = record.record_type.level

        # Override origin_type from parent record when available
        if record.parent_record_id is not None:
            try:
                parent = await client.get_record(record.parent_record_id)
                fields["origin_type"] = resolve_origin_type(record, parent)
            except Exception:
                logger.debug(f"Could not load parent {record.parent_record_id} for origin_type")

        logger.debug(f"TaskContext built from record_id={msg.record_id}")

    elif msg.series_uid is not None:
        series = await client.get_series(msg.series_uid)
        working_dirs = FileResolver.build_working_dirs_from_series(series)
        logger.debug(f"TaskContext built from series_uid={msg.series_uid}")

    elif msg.study_uid:
        study = await client.get_study(msg.study_uid)
        working_dirs = FileResolver.build_working_dirs_from_study(study)
        logger.debug(f"TaskContext built from study_uid={msg.study_uid}")

    else:
        logger.debug("TaskContext built with minimal empty context")

    files = FileResolver(
        working_dirs=working_dirs,
        record_type_level=record_type_level,
        file_registry=file_registry,
        fields=fields,
    )
    records = RecordQuery(client=client, files=files)
    return TaskContext(files=files, records=records, client=client, msg=msg)
