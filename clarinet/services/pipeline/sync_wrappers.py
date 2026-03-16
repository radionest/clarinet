"""Synchronous wrappers for async pipeline types.

Provides ``SyncRecordQuery``, ``SyncPipelineClient``, and ``SyncTaskContext``
for use in sync pipeline task handlers that run via ``asyncio.to_thread()``.

The sync wrappers use ``asyncio.run_coroutine_threadsafe()`` to bridge
from the worker thread back to the main event loop for async operations.

Example:
    @pipeline_task()
    def my_sync_task(msg: PipelineMessage, ctx: SyncTaskContext) -> None:
        records = ctx.records.find("ct_seg", series_uid=msg.series_uid)
        ctx.client.submit_record_data(msg.record_id, {"status": "done"})
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path
    from uuid import UUID

    from clarinet.client import ClarinetClient
    from clarinet.models import Patient, RecordCreate, RecordRead, RecordStatus
    from clarinet.models.study import SeriesRead, StudyRead
    from clarinet.types import RecordData

    from .context import FileResolver, RecordQuery, TaskContext
    from .message import PipelineMessage


def _call_async[T](coro: Coroutine[Any, Any, T], loop: asyncio.AbstractEventLoop) -> T:
    """Call an async coroutine from a sync thread context.

    Submits the coroutine to the event loop via ``run_coroutine_threadsafe``
    and blocks the current thread until the result is available.

    Args:
        coro: Async coroutine to execute.
        loop: The running event loop (main thread).

    Returns:
        The coroutine's return value.
    """
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


class SyncRecordQuery:
    """Synchronous wrapper for ``RecordQuery``.

    Delegates to the async ``RecordQuery`` via ``_call_async``.

    Args:
        query: The async ``RecordQuery`` to wrap.
        loop: The running event loop.
    """

    def __init__(self, query: RecordQuery, loop: asyncio.AbstractEventLoop) -> None:
        self._query = query
        self._loop = loop

    def find(
        self,
        type_name: str,
        *,
        series_uid: str | None = None,
        study_uid: str | None = None,
        patient_id: str | None = None,
        status: Any | None = None,
        limit: int = 100,
    ) -> list[RecordRead]:
        """Find records by criteria (sync version of ``RecordQuery.find``)."""
        return _call_async(
            self._query.find(
                type_name,
                series_uid=series_uid,
                study_uid=study_uid,
                patient_id=patient_id,
                status=status,
                limit=limit,
            ),
            self._loop,
        )

    def file_path(
        self,
        type_name: str,
        *,
        file: str,
        series_uid: str | None = None,
        study_uid: str | None = None,
        patient_id: str | None = None,
        status: Any | None = None,
    ) -> Path:
        """Find a record and resolve a file path (sync version of ``RecordQuery.file_path``)."""
        return _call_async(
            self._query.file_path(
                type_name,
                file=file,
                series_uid=series_uid,
                study_uid=study_uid,
                patient_id=patient_id,
                status=status,
            ),
            self._loop,
        )


class SyncPipelineClient:
    """Synchronous wrapper for ``ClarinetClient``.

    Wraps the most commonly used client methods for pipeline tasks.
    Each method delegates to ``_call_async`` to bridge the sync/async boundary.

    Args:
        client: The async ``ClarinetClient`` to wrap.
        loop: The running event loop.
    """

    def __init__(self, client: ClarinetClient, loop: asyncio.AbstractEventLoop) -> None:
        self._client = client
        self._loop = loop

    def submit_record_data(self, record_id: int, data: RecordData) -> RecordRead:
        """Submit data for a record."""
        return _call_async(self._client.submit_record_data(record_id, data), self._loop)

    def get_record(self, record_id: int) -> RecordRead:
        """Get record by ID."""
        return _call_async(self._client.get_record(record_id), self._loop)

    def find_records(self, skip: int = 0, limit: int = 100, **filters: Any) -> list[RecordRead]:
        """Find records by various criteria."""
        return _call_async(self._client.find_records(skip, limit, **filters), self._loop)

    def find_records_advanced(
        self,
        patient_id: str | None = None,
        patient_anon_id: str | None = None,
        series_uid: str | None = None,
        study_uid: str | None = None,
        user_id: UUID | None = None,
        record_type_name: str | None = None,
        record_status: RecordStatus | None = None,
        wo_user: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[RecordRead]:
        """Advanced record search with multiple filter options."""
        return _call_async(
            self._client.find_records_advanced(
                patient_id=patient_id,
                patient_anon_id=patient_anon_id,
                series_uid=series_uid,
                study_uid=study_uid,
                user_id=user_id,
                record_type_name=record_type_name,
                record_status=record_status,
                wo_user=wo_user,
                skip=skip,
                limit=limit,
            ),
            self._loop,
        )

    def update_record_data(self, record_id: int, data: RecordData) -> RecordRead:
        """Update data on a finished record."""
        return _call_async(self._client.update_record_data(record_id, data), self._loop)

    def update_record_status(self, record_id: int, status: RecordStatus) -> RecordRead:
        """Update record status."""
        return _call_async(self._client.update_record_status(record_id, status), self._loop)

    def get_study(self, study_uid: str) -> StudyRead:
        """Get study by UID."""
        return _call_async(self._client.get_study(study_uid), self._loop)

    def get_series(self, series_uid: str) -> SeriesRead:
        """Get series by UID."""
        return _call_async(self._client.get_series(series_uid), self._loop)

    def check_record_files(self, record_id: int) -> dict[str, Any]:
        """Check record files for changes."""
        return _call_async(self._client.check_record_files(record_id), self._loop)

    def create_record(self, record: RecordCreate | dict[str, Any]) -> RecordRead:
        """Create a new record."""
        return _call_async(self._client.create_record(record), self._loop)

    def invalidate_record(
        self,
        record_id: int,
        mode: str = "hard",
        source_record_id: int | None = None,
        reason: str | None = None,
    ) -> RecordRead:
        """Invalidate a record."""
        return _call_async(
            self._client.invalidate_record(record_id, mode, source_record_id, reason),
            self._loop,
        )

    def anonymize_patient(self, patient_id: str) -> Patient:
        """Anonymize a patient."""
        return _call_async(self._client.anonymize_patient(patient_id), self._loop)


@dataclass
class SyncTaskContext:
    """Container for sync pipeline task context.

    Mirror of ``TaskContext`` with sync wrappers for ``records`` and ``client``.

    Attributes:
        files: Sync file path resolver (same as ``TaskContext.files``).
        records: Sync record query helper.
        client: Sync HTTP client wrapper.
        msg: The parsed pipeline message.
    """

    files: FileResolver
    records: SyncRecordQuery
    client: SyncPipelineClient
    msg: PipelineMessage


def build_sync_context(ctx: TaskContext, loop: asyncio.AbstractEventLoop) -> SyncTaskContext:
    """Build a ``SyncTaskContext`` from an async ``TaskContext``.

    Args:
        ctx: The async task context.
        loop: The running event loop.

    Returns:
        A ``SyncTaskContext`` with sync wrappers for async components.
    """
    return SyncTaskContext(
        files=ctx.files,
        records=SyncRecordQuery(ctx.records, loop),
        client=SyncPipelineClient(ctx.client, loop),
        msg=ctx.msg,
    )
