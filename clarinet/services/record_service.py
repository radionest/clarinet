"""Service layer for record-related business logic with RecordFlow integration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from clarinet.exceptions.domain import BusinessRuleViolationError, RecordUniquePerUserError
from clarinet.exceptions.domain import FileNotFoundError as DomainFileNotFoundError
from clarinet.models import Record, RecordRead, RecordStatus
from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.services.file_validation import _build_working_dirs, validate_record_files
from clarinet.utils.file_checksums import checksums_changed, compute_checksums
from clarinet.utils.file_patterns import glob_file_paths, resolve_pattern
from clarinet.utils.fs import run_in_fs_thread
from clarinet.utils.logger import logger

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

        # Fetch parent for fallback pattern resolution
        parent_read = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)

        # Validate input files
        record_read = RecordRead.model_validate(record)
        file_result = await validate_record_files(record_read, parent=parent_read)

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

        Raises:
            RecordConstraintViolationError: If unique_per_user is violated.
        """
        record = await self.repo.get_with_record_type(record_id)
        await self._check_unique_per_user(user_id, record)
        record, old_status = await self.repo.assign_user(record_id, user_id)
        if old_status != record.status:
            await self._fire_status_change(record, old_status)
        return record, old_status

    async def claim_record(self, record_id: int, user_id: UUID) -> Record:
        """Claim a record for a user with uniqueness constraint check.

        Args:
            record_id: Record ID.
            user_id: User UUID claiming the record.

        Returns:
            Updated record with inwork status.

        Raises:
            RecordConstraintViolationError: If unique_per_user is violated.
        """
        record = await self.repo.get_with_record_type(record_id)
        await self._check_unique_per_user(user_id, record)
        return await self.repo.claim_record(record_id, user_id)

    async def unassign_user(self, record_id: int) -> tuple[Record, RecordStatus]:
        """Remove user from a record and fire RecordFlow trigger if status changed.

        Args:
            record_id: Record ID.

        Returns:
            Tuple of (updated record, old status).
        """
        record, old_status = await self.repo.unassign_user(record_id)
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

        Raises:
            RecordConstraintViolationError: If unique_per_user is violated on auto-assign.
        """
        if user_id is not None:
            record_check = await self.repo.get_with_record_type(record_id)
            if record_check.user_id is None:
                await self._check_unique_per_user(user_id, record_check)
            await self.repo.ensure_user_assigned(record_id, user_id)

        record, old_status = await self.repo.update_data(record_id, data, new_status=new_status)
        await self._fire_status_change(record, old_status)

        # Detect output file changes and emit file events
        if new_status == RecordStatus.finished:
            await self._emit_output_file_events(record)

        return record, old_status

    async def prefill_data(self, record_id: int, data: RecordData) -> tuple[Record, RecordStatus]:
        """Write prefill data without firing RecordFlow triggers.

        For pipeline tasks writing preliminary data to pending/blocked records.
        Caller is responsible for status checks and data merging.

        Args:
            record_id: Record ID.
            data: Prefill data (already validated/merged by caller).

        Returns:
            Tuple of (updated record, old status).
        """
        return await self.repo.update_data(record_id, data)

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

    async def invalidate_record(
        self,
        record_id: int,
        mode: str,
        source_record_id: int | None = None,
        reason: str | None = None,
    ) -> Record:
        """Invalidate a record and fire RecordFlow trigger on hard mode.

        Args:
            record_id: ID of the record to invalidate.
            mode: "hard" resets to pending, "soft" only appends reason.
            source_record_id: ID of the triggering record.
            reason: Human-readable reason.

        Returns:
            Updated record with relations.
        """
        old_record = await self.repo.get(record_id)
        old_status = old_record.status

        record = await self.repo.invalidate_record(
            record_id=record_id,
            mode=mode,
            source_record_id=source_record_id,
            reason=reason,
        )

        # Fire trigger on hard mode if status actually changed
        if mode == "hard" and old_status != record.status:
            await self._fire_status_change(record, old_status)

        return record

    async def fail_record(self, record_id: int, reason: str) -> Record:
        """Mark a record as failed with a reason and fire RecordFlow triggers.

        Args:
            record_id: ID of the record to fail.
            reason: Human-readable reason for failure.

        Returns:
            Updated record with relations.
        """
        record, old_status = await self.repo.fail_record(record_id, reason)
        logger.info(f"Record {record_id} manually failed")

        if old_status != record.status:
            await self._fire_status_change(record, old_status)

        return record

    async def check_files(self, record_id: int) -> tuple[list[str], dict[str, str]]:
        """Check file status, auto-unblock if ready, compute & compare checksums.

        For blocked records: validates input files, transitions to pending if valid.
        For non-blocked: computes checksums, updates DB, notifies on change.

        Returns:
            Tuple of (changed file keys, current checksums).
            Empty tuple ([], {}) if record stays blocked.
        """
        record = await self.repo.get_with_relations(record_id)
        record_read = RecordRead.model_validate(record)

        # Auto-unblock: if record is blocked, check whether input files are now present
        if record.status == RecordStatus.blocked:
            # Fetch parent for fallback pattern resolution only when needed
            parent_read = None
            if record.parent_record_id is not None:
                parent = await self.repo.get_with_relations(record.parent_record_id)
                parent_read = RecordRead.model_validate(parent)

            file_result = await validate_record_files(record_read, parent=parent_read)
            if file_result is not None and file_result.valid:
                if file_result.matched_files:
                    await self.repo.set_files(record, file_result.matched_files)
                record, _ = await self.update_status(record_id, RecordStatus.pending)
                record_read = RecordRead.model_validate(record)
            else:
                return [], {}

        new_checksums = await compute_checksums(
            record_read.record_type.file_registry or [],
            record_read,
            Path(record_read.working_folder),
        )
        old_checksums = {
            link.name: link.checksum for link in (record_read.file_links or []) if link.checksum
        }
        changed = checksums_changed(old_checksums, new_checksums)

        await self.repo.update_checksums(record, new_checksums)

        if changed:
            await self.notify_file_change(record)

        return list(changed), new_checksums

    async def delete_record_cascade(self, record_id: int) -> tuple[list[int], int]:
        """Delete a record, all its descendants, and their OUTPUT files.

        Check-and-delete runs inside a single DB transaction with row locks
        on the whole subtree (``SELECT ... FOR UPDATE``), so a concurrent
        transaction cannot flip a record to ``inwork`` between the guard and
        the delete. If any record in the subtree is in ``inwork`` status the
        operation aborts and nothing is deleted.

        Files on disk are unlinked AFTER the DB commit; a filesystem failure
        at that stage is logged but does not undo the DB delete — the API
        response reflects the committed DB state, with orphan files at worst.

        Args:
            record_id: ID of the subtree root to delete.

        Returns:
            Tuple of (deleted record IDs in BFS order, number of files removed).

        Raises:
            RecordNotFoundError: If the root record doesn't exist.
            BusinessRuleViolationError: If any record in the subtree is inwork.
        """
        records = await self.repo.collect_descendants(record_id, for_update=True)

        inwork_ids = [r.id for r in records if r.status == RecordStatus.inwork]
        if inwork_ids:
            raise BusinessRuleViolationError(
                f"Cannot delete record {record_id}: subtree contains "
                f"{len(inwork_ids)} inwork record(s) (ids={inwork_ids})"
            )

        # Resolve the root's parent (outside the subtree) so pattern
        # resolution for the subtree root can use parent-derived fields.
        root_parent_read: RecordRead | None = None
        root_parent_id = records[0].parent_record_id if records else None
        if root_parent_id is not None:
            parent = await self.repo.get_with_relations(root_parent_id)
            root_parent_read = RecordRead.model_validate(parent)

        # BFS order: parents appear before children — build reads iteratively
        # so children can look up parent_read without a second pass.
        reads: dict[int, RecordRead] = {}
        paths_to_unlink: list[Path] = []
        for record in records:
            assert record.id is not None
            record_read = RecordRead.model_validate(record)
            reads[record.id] = record_read
            if record.parent_record_id is None:
                parent_read = None
            else:
                parent_read = reads.get(record.parent_record_id, root_parent_read)
            paths_to_unlink.extend(await self._collect_output_file_paths(record_read, parent_read))

        # Deduplicate — glob patterns (multiple=True) on shared working_dirs
        # can yield the same path from multiple records in the subtree.
        paths_to_unlink = list(dict.fromkeys(paths_to_unlink))

        deleted_ids = list(reads.keys())
        # Keep the transaction open: commit only after we've issued the DELETE,
        # so the row locks acquired above cover the whole check-and-delete.
        await self.repo.delete_records(deleted_ids, commit=False)
        await self.repo.session.commit()

        files_removed = 0
        for p in paths_to_unlink:
            try:
                await run_in_fs_thread(p.unlink)
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning(
                    f"Failed to delete output file {p} during cascade delete "
                    f"of record {record_id}: {exc}"
                )
                continue
            files_removed += 1
            logger.info(f"Deleted output file {p} during cascade delete of record {record_id}")

        logger.info(
            f"Cascade-deleted record {record_id}: {len(deleted_ids)} records, "
            f"{files_removed} files removed"
        )
        return deleted_ids, files_removed

    async def _resolve_paths_for_file_def(
        self,
        file_def: FileDefinitionRead,
        record_read: RecordRead,
        parent_read: RecordRead | None,
    ) -> list[Path]:
        """Resolve on-disk paths for one ``FileDefinition``, sandboxed to its target dir.

        For ``multiple=True`` returns all glob matches inside the resolved
        target directory; paths escaping via symlinks or ``..`` are filtered
        out (defence in depth — patterns are admin-controlled but the guard
        keeps this method safe for any caller).

        For ``multiple=False`` returns a single-element list when the
        resolved path exists on disk, else an empty list.
        """
        working_dir = Path(record_read.working_folder)
        working_dirs = _build_working_dirs(record_read)
        target_dir = (
            working_dirs[file_def.level]
            if file_def.level and file_def.level in working_dirs
            else working_dir
        )
        target_resolved = target_dir.resolve()

        if file_def.multiple:
            candidates = await run_in_fs_thread(glob_file_paths, file_def, target_dir)
        else:
            file_path = target_dir / resolve_pattern(file_def.pattern, record_read, parent_read)
            if not await run_in_fs_thread(file_path.is_file):
                return []
            candidates = [file_path]

        return [p for p in candidates if p.resolve().is_relative_to(target_resolved)]

    async def _collect_output_file_paths(
        self,
        record_read: RecordRead,
        parent_read: RecordRead | None,
    ) -> list[Path]:
        """Resolve OUTPUT file paths that exist on disk for a single record.

        Shared between ``clear_output_files`` and ``delete_record_cascade``.
        """
        output_defs = [
            fd for fd in (record_read.record_type.file_registry or []) if fd.role == FileRole.OUTPUT
        ]
        if not output_defs:
            return []

        resolved: list[Path] = []
        for fd in output_defs:
            resolved.extend(await self._resolve_paths_for_file_def(fd, record_read, parent_read))
        return resolved

    async def resolve_output_file(self, record_id: int, file_name: str) -> list[Path]:
        """Resolve OUTPUT file path(s) for a record by ``FileDefinition.name``.

        Returns a list to support both ``multiple=False`` (single path) and
        ``multiple=True`` (glob expansion) without changing the contract.

        Raises:
            FileNotFoundError: If the name is not an OUTPUT definition for
                this record's type, or no matching files exist on disk.
        """
        record = await self.repo.get_with_relations(record_id)
        record_read = RecordRead.model_validate(record)

        file_def = next(
            (
                fd
                for fd in (record_read.record_type.file_registry or [])
                if fd.role == FileRole.OUTPUT and fd.name == file_name
            ),
            None,
        )
        if file_def is None:
            raise DomainFileNotFoundError(
                f"Output file '{file_name}' is not defined for record {record_id}"
            )

        parent_read: RecordRead | None = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)

        paths = await self._resolve_paths_for_file_def(file_def, record_read, parent_read)
        if not paths:
            raise DomainFileNotFoundError(
                f"Output file '{file_name}' is not available on disk for record {record_id}"
            )
        return paths

    async def clear_output_files(self, record_id: int) -> tuple[list[str], int]:
        """Delete OUTPUT files from disk and their RecordFileLink rows.

        Only allowed for records NOT in ``finished`` status. Intended for
        clearing stale output files before retrying a failed pipeline task.

        Args:
            record_id: Record ID.

        Returns:
            Tuple of (list of deleted filenames, number of deleted DB links).

        Raises:
            BusinessRuleViolationError: If the record is in ``finished`` status.
        """
        record = await self.repo.get_with_relations(record_id)

        if record.status == RecordStatus.finished:
            raise BusinessRuleViolationError("Cannot clear output files for a finished record")

        record_read = RecordRead.model_validate(record)

        # Resolve parent for pattern fallback
        parent_read: RecordRead | None = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)

        paths = await self._collect_output_file_paths(record_read, parent_read)

        deleted_files: list[str] = []
        for p in paths:
            try:
                await run_in_fs_thread(p.unlink)
            except FileNotFoundError:
                continue
            deleted_files.append(p.name)
            logger.info(f"Deleted output file {p} for record {record_id}")

        deleted_links = await self.repo.delete_output_file_links(record)

        logger.info(
            f"Cleared output files for record {record_id}: "
            f"{len(deleted_files)} files, {deleted_links} links"
        )
        return deleted_files, deleted_links

    async def notify_file_updates(
        self,
        patient_id: str,
        changed_files: list[str],
        source_record: RecordRead | None = None,
    ) -> None:
        """Fire file-update triggers for project-level file changes.

        Args:
            patient_id: Patient whose files changed.
            changed_files: List of logical file names that changed.
            source_record: Record that caused the file change (for skip logic).
        """
        if not self.engine:
            return
        for file_name in changed_files:
            await self.engine.handle_file_update(file_name, patient_id, source_record=source_record)

    # ── Private helpers ──────────────────────────────────────────────────

    async def _check_unique_per_user(self, user_id: UUID, record: Record) -> None:
        """Check that assigning user_id to record does not violate unique_per_user.

        Does nothing when record_type.unique_per_user is False.

        Args:
            user_id: User being assigned.
            record: Record with record_type eagerly loaded.

        Raises:
            RecordUniquePerUserError: If user already has a record
                of this type for the same DICOM context.
        """
        record_type = record.record_type
        if not record_type.unique_per_user:
            return

        count = await self.repo.count_user_records_for_context(
            user_id=user_id,
            record_type_name=record.record_type_name,
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=record.series_uid,
            level=record_type.level,
        )
        if count > 0:
            raise RecordUniquePerUserError(
                f"User already has a record of type '{record_type.name}' "
                f"for this {record_type.level.lower()} context"
            )

    async def _emit_output_file_events(self, record: Record) -> None:
        """Detect output file changes and emit project-level file events.

        Computes checksums on disk for OUTPUT files and compares against
        stored checksums in ``file_links``. Emits file-update events for
        any changed files so that downstream file flows (e.g. invalidation)
        are triggered.

        Args:
            record: Record with relations loaded (must have record_type, patient).
        """
        if not self.engine:
            return

        record_read = RecordRead.model_validate(record)
        output_defs = [
            fd for fd in (record_read.record_type.file_registry or []) if fd.role == FileRole.OUTPUT
        ]
        if not output_defs:
            return

        working_dir = Path(record_read.working_folder)
        try:
            new_checksums = await compute_checksums(output_defs, record_read, working_dir)
        except Exception as e:
            logger.warning(f"Failed to compute output checksums for record {record.id}: {e}")
            return

        old_checksums = {
            link.name: link.checksum for link in (record_read.file_links or []) if link.checksum
        }

        changed = checksums_changed(old_checksums, new_checksums)
        if not changed:
            return

        # Update stored checksums in DB
        try:
            await self.repo.update_checksums(record, new_checksums)
        except Exception as e:
            logger.warning(f"Failed to update checksums for record {record.id}: {e}")

        # Extract logical file names (strip collection suffix "name:filename" → "name")
        changed_file_names = {key.split(":")[0] for key in changed}

        # Fire file events with source_record for downstream flows
        for file_name in changed_file_names:
            await self.engine.handle_file_update(
                file_name, record_read.patient.id, source_record=record_read
            )

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
