"""Service layer for record-related business logic with RecordFlow integration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from clarinet.models import Record, RecordRead, RecordStatus
from clarinet.services.file_validation import validate_record_files

if TYPE_CHECKING:
    from clarinet.repositories.record_repository import RecordRepository
    from clarinet.services.recordflow.engine import RecordFlowEngine
    from clarinet.types import RecordData


class RecordService:
    """Service wrapping record mutations with automatic RecordFlow triggers.

    Args:
        record_repo: Record repository instance.
        engine: Optional RecordFlow engine (None when RecordFlow is disabled).
    """

    def __init__(
        self,
        record_repo: RecordRepository,
        engine: RecordFlowEngine | None = None,
    ):
        self.repo = record_repo
        self.engine = engine

    # ── Public methods ───────────────────────────────────────────────────

    async def create_record(self, record: Record) -> Record:
        """Create a record with file validation, blocking, and RecordFlow trigger.

        Args:
            record: Record ORM instance to persist.

        Returns:
            Created record with relations loaded.
        """
        record = await self.repo.create_with_relations(record)

        # Validate input files
        record_read = RecordRead.model_validate(record)
        file_result = await validate_record_files(record_read)

        if file_result is not None:
            if file_result.valid and file_result.matched_files:
                await self.repo.set_files(record, file_result.matched_files)
                record = await self.repo.get_with_relations(record.id)  # type: ignore[arg-type]
            elif not file_result.valid:
                record, _ = await self.repo.update_status(record.id, RecordStatus.blocked)  # type: ignore[arg-type]

        # Fire status-change trigger for the initial status
        await self._fire_status_change(record, old_status=None)

        return record

    async def update_status(
        self, record_id: int, new_status: RecordStatus
    ) -> tuple[Record, RecordStatus]:
        """Update record status and fire RecordFlow trigger if status changed.

        Args:
            record_id: Record ID.
            new_status: New status to set.

        Returns:
            Tuple of (updated record, old status).
        """
        record, old_status = await self.repo.update_status(record_id, new_status)
        if old_status != new_status:
            await self._fire_status_change(record, old_status)
        return record, old_status

    async def assign_user(self, record_id: int, user_id: UUID) -> tuple[Record, RecordStatus]:
        """Assign user to a record and fire RecordFlow trigger if status changed.

        Args:
            record_id: Record ID.
            user_id: User UUID.

        Returns:
            Tuple of (updated record, old status).
        """
        record, old_status = await self.repo.assign_user(record_id, user_id)
        if old_status != record.status:
            await self._fire_status_change(record, old_status)
        return record, old_status

    async def submit_data(
        self,
        record_id: int,
        data: RecordData,
        new_status: RecordStatus,
        user_id: UUID | None = None,
    ) -> tuple[Record, RecordStatus]:
        """Submit record data with a status transition and fire RecordFlow trigger.

        Auto-assigns ``user_id`` when the record has no user yet (admin bypass).

        Args:
            record_id: Record ID.
            data: Validated record data.
            new_status: New status to set alongside data.
            user_id: Current user ID; assigned to the record when it has no user.

        Returns:
            Tuple of (updated record, old status).
        """
        if user_id is not None:
            await self.repo.ensure_user_assigned(record_id, user_id)

        record, old_status = await self.repo.update_data(record_id, data, new_status=new_status)
        await self._fire_status_change(record, old_status)
        return record, old_status

    async def update_data(self, record_id: int, data: RecordData) -> tuple[Record, RecordStatus]:
        """Update record data (no status change) and fire data-update trigger.

        Args:
            record_id: Record ID.
            data: Validated record data.

        Returns:
            Tuple of (updated record, old status).
        """
        record, old_status = await self.repo.update_data(record_id, data)
        await self._fire_data_update(record)
        return record, old_status

    async def notify_file_change(self, record: Record) -> None:
        """Fire a file-change trigger for a record.

        Args:
            record: Record whose files changed.
        """
        await self._fire_file_change(record)

    async def bulk_update_status(self, record_ids: list[int], new_status: RecordStatus) -> None:
        """Update status for multiple records and fire triggers for each changed record.

        Args:
            record_ids: List of record IDs.
            new_status: New status to set.
        """
        # Capture old statuses before bulk update
        old_statuses: dict[int, RecordStatus] = {}
        for record_id in record_ids:
            record = await self.repo.get_optional(record_id)
            if record:
                old_statuses[record_id] = record.status

        await self.repo.bulk_update_status(record_ids, new_status)

        # Fire triggers for each record whose status actually changed
        for record_id, old_status in old_statuses.items():
            if old_status != new_status:
                updated = await self.repo.get_with_relations(record_id)
                await self._fire_status_change(updated, old_status)

    async def notify_file_updates(self, patient_id: str, changed_files: list[str]) -> None:
        """Fire file-update triggers for project-level file changes.

        Args:
            patient_id: Patient whose files changed.
            changed_files: List of logical file names that changed.
        """
        if not self.engine:
            return
        for file_name in changed_files:
            await self.engine.handle_file_update(file_name, patient_id)

    # ── Private helpers ──────────────────────────────────────────────────

    async def _fire_status_change(self, record: Record, old_status: RecordStatus | None) -> None:
        """Convert record to RecordRead and fire status-change trigger."""
        if not self.engine:
            return
        record_read = RecordRead.model_validate(record)
        await self.engine.handle_record_status_change(record_read, old_status)

    async def _fire_data_update(self, record: Record) -> None:
        """Convert record to RecordRead and fire data-update trigger."""
        if not self.engine:
            return
        record_read = RecordRead.model_validate(record)
        await self.engine.handle_record_data_update(record_read)

    async def _fire_file_change(self, record: Record) -> None:
        """Convert record to RecordRead and fire file-change trigger."""
        if not self.engine:
            return
        record_read = RecordRead.model_validate(record)
        await self.engine.handle_record_file_change(record_read)
