"""Service layer for record-related business logic with RecordFlow integration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from clarinet.exceptions.domain import (
    BusinessRuleViolationError,
    RecordEditLockedError,
)
from clarinet.exceptions.domain import FileNotFoundError as DomainFileNotFoundError
from clarinet.files import Files
from clarinet.models import Record, RecordRead, RecordStatus, is_record_editable
from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.models.record_event import RecordEvent
from clarinet.services.events.capture import emit_record_events, mark_pending_audit
from clarinet.services.events.models import EntityEvent
from clarinet.services.file_validation import validate_record_files
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from clarinet.models import User
    from clarinet.models.record_event import RecordEventKind
    from clarinet.repositories.record_event_repository import RecordEventRepository
    from clarinet.repositories.record_repository import RecordRepository, RecordSearchCriteria
    from clarinet.services.recordflow.engine import RecordFlowEngine
    from clarinet.types import RecordData


def ensure_record_editable(record: Record, acting_user: User | None) -> None:
    """Raise when *acting_user* may not change a submitted (finished) record.

    Enforces ``RecordType.editable`` / ``RecordType.edit_window_days``.
    ``acting_user=None`` marks a trusted caller — in-process service calls
    (RecordFlow triggers, check-files auto-unblock) and admin endpoints that
    deliberately bypass the lock. Superusers (including pipeline service
    tokens) also bypass. Requires ``record.record_type`` to be loaded.

    Raises:
        RecordEditLockedError: 409 via the BusinessRuleViolationError handler.
    """
    if acting_user is None or acting_user.is_superuser:
        return
    if is_record_editable(record.status, record.finished_at, record.record_type):
        return
    if not record.record_type.editable:
        raise RecordEditLockedError(
            f"Record {record.id}: record type '{record.record_type_name}' "
            f"does not allow changing submitted records."
        )
    raise RecordEditLockedError(
        f"Record {record.id}: editing window of "
        f"{record.record_type.edit_window_days} days after submission has passed."
    )


def _filter_in_sandbox(paths: list[Path], sandbox: Path) -> list[Path]:
    """Drop paths whose resolved location escapes the sandbox directory.

    Both ``Path.resolve()`` calls hit the filesystem (symlink chasing), so
    callers should run this through ``Files.in_thread``.
    """
    sandbox_resolved = sandbox.resolve()
    return [p for p in paths if p.resolve().is_relative_to(sandbox_resolved)]


def _missing_output_links(
    record: RecordRead,
    checksums: dict[str, str],
    parent: RecordRead | None = None,
) -> dict[str, str]:
    """Derive OUTPUT file links to create from freshly computed checksums.

    OUTPUT files appear on disk only after pipeline tasks or users produce
    them, so creation-time matching (``set_files``) never sees them — without
    this reconciliation no ``RecordFileLink`` would ever exist for outputs.
    ``Files(record).checksums()`` keys every found file by definition name
    (singular) or ``"name:filename"`` (collections), so each key proves the
    file existed on disk at scan time — no second filesystem scan is needed.
    Returns name → filename for OUTPUT definitions that have no link yet; for
    collections the lexicographically first file is stored, matching the
    download endpoint's pick. ``parent`` must mirror the fallback passed to
    ``Files`` so the stored filename matches the scanned path.
    """
    output_defs = {
        fd.name: fd for fd in (record.record_type.file_registry or []) if fd.role == FileRole.OUTPUT
    }
    linked = {link.name for link in (record.file_links or [])}
    missing: dict[str, str] = {}
    for key in sorted(checksums):
        name, _, collection_file = key.partition(":")
        fd = output_defs.get(name)
        if fd is None or name in linked or name in missing:
            continue
        missing[name] = collection_file or Files.render_for(record, fd.pattern, parent=parent)
    return missing


def _stored_checksums(record: RecordRead) -> dict[str, str]:
    """Checksums stored on file links, keyed to match ``Files(record).checksums()``.

    Emits both ``name`` (singular definitions) and ``"name:filename"``
    (collections) for every link — the irrelevant key of the pair never
    collides with computed keys, so comparisons stay exact.
    """
    stored: dict[str, str] = {}
    for link in record.file_links or []:
        if link.checksum:
            stored[link.name] = link.checksum
            stored[f"{link.name}:{link.filename}"] = link.checksum
    return stored


class RecordService:
    """Service wrapping record mutations with automatic RecordFlow triggers.

    When *event_repo* is provided, every mutation also appends a
    :class:`RecordEvent` audit row (``actor_id=None`` marks system /
    worker / RecordFlow calls — see ``get_audit_actor``).

    Args:
        record_repo: Record repository instance.
        engine: Optional RecordFlow engine (None when RecordFlow is disabled).
        event_repo: Optional record event repository (None disables auditing).
    """

    def __init__(
        self,
        record_repo: RecordRepository,
        engine: RecordFlowEngine | None = None,
        event_repo: RecordEventRepository | None = None,
    ):
        self.repo = record_repo
        self.engine = engine
        self.event_repo = event_repo

    async def _record_event(
        self,
        *,
        record_id: int | None,
        kind: RecordEventKind,
        actor_id: UUID | None,
        from_status: RecordStatus | None = None,
        to_status: RecordStatus | None = None,
        old_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        """Append an audit event right after a record mutation.

        The event is flushed immediately — before any RecordFlow dispatch,
        so an engine failure cannot lose it — and committed by the next
        commit on the shared session (request teardown). A process crash
        between the mutation's own commit and teardown loses the event but
        never the mutation (accepted trade-off; cascade delete is the one
        path where events share the mutation's transaction). No-op when
        auditing is disabled (``event_repo is None``).
        """
        if self.event_repo is None:
            return
        await self.event_repo.add(
            RecordEvent(
                record_id=record_id,
                record_key=record_id,
                kind=kind,
                actor_id=actor_id,
                from_status=from_status.value if from_status else None,
                to_status=to_status.value if to_status else None,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
            )
        )

    def _mark_audit(self, record_id: int | None, actor_id: UUID | None) -> None:
        """Announce the *next* committing record mutation to the SSE capture.

        The repo write commits internally and the matching ``RecordEvent``
        commits later, so the SSE capture cannot pair them per-commit. Setting
        this breadcrumb right before the committing write lets the capture emit
        one enriched record event (``user_id`` = the acting user) and skip the
        drift warning. No-op when SSE is off (see ``mark_pending_audit``).
        """
        mark_pending_audit(self.repo.session, record_id, actor_id)

    # ── Public methods ───────────────────────────────────────────────────

    async def create_record(self, record: Record, *, actor_id: UUID | None = None) -> Record:
        """Create a record with file validation, blocking, and RecordFlow trigger.

        When ``parent_record_id`` is set, the parent is validated to exist
        (raises ``RecordNotFoundError`` otherwise). ``user_id`` is inherited
        from the parent only when the record's type has
        ``inherit_user_from_parent`` enabled and no explicit ``user_id`` was
        provided.

        Args:
            record: Record ORM instance to persist.

        Returns:
            Created record with relations loaded.
        """
        # Fetch parent up front: validates existence, drives opt-in user_id
        # inheritance, and feeds fallback file-pattern resolution below.
        parent_read = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)
            if record.user_id is None:
                record_type = await self.repo.get_record_type(
                    record.record_type_name, with_files=False
                )
                if record_type.inherit_user_from_parent and parent.user_id is not None:
                    # The route-level constraint check ran with user_id=None
                    # and could not see the inherited user — re-check here.
                    await self.repo.ensure_unique_by(
                        record_type,
                        user_id=parent.user_id,
                        parent_record_id=record.parent_record_id,
                        patient_id=record.patient_id,
                        study_uid=record.study_uid,
                        series_uid=record.series_uid,
                    )
                    record.user_id = parent.user_id

        record = await self.repo.create_with_relations(record)

        # Validate input files
        record_read = RecordRead.model_validate(record)
        file_result = await validate_record_files(record_read, parent=parent_read)

        if file_result is not None:
            if file_result.valid and file_result.matched_files:
                await self.repo.set_files(record, file_result.matched_files)
                record = await self.repo.get_with_relations(record.id)  # type: ignore[arg-type]
            elif not file_result.valid and record.status != RecordStatus.preparing:
                self._mark_audit(record.id, actor_id)
                record, _ = await self.repo.update_status(record.id, RecordStatus.blocked)  # type: ignore[arg-type]

        await self._record_event(
            record_id=record.id,
            kind="created",
            actor_id=actor_id,
            to_status=record.status,
            new_value={"record_type_name": record.record_type_name},
        )

        # Fire status-change trigger for the initial status
        await self._fire_status_change(record, old_status=None)

        return record

    async def update_status(
        self,
        record_id: int,
        new_status: RecordStatus,
        *,
        acting_user: User | None = None,
        actor_id: UUID | None = None,
    ) -> tuple[Record, RecordStatus]:
        """Update record status and fire RecordFlow trigger if status changed.

        When a record leaves ``preparing`` for ``pending``, input files are
        re-validated *before* any status is written: an invalid file set sends
        the record to ``blocked`` instead (check-files unblocks it later once
        files appear), so the record is never observable as
        pending-with-invalid-files. Direct ``preparing`` → ``inwork``/
        ``finished`` transitions are rejected — a preparing record must exit
        via ``pending``.

        Args:
            record_id: Record ID.
            new_status: New status to set.
            acting_user: API caller; ``None`` marks a trusted in-process call
                that bypasses the post-submit edit lock.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Tuple of (updated record, old status). The record's final status
            may differ from ``new_status`` (see above).

        Raises:
            RecordEditLockedError: If the record is finished and its type
                locks submitted records for *acting_user*.
            BusinessRuleViolationError: On a direct preparing → inwork/finished
                transition.
        """
        if acting_user is not None and not acting_user.is_superuser:
            record = await self.repo.get_with_relations(record_id)
            ensure_record_editable(record, acting_user)
        target_status = new_status
        matched_files: dict[str, str] = {}
        current = await self.repo.get(record_id)
        if current.status == RecordStatus.preparing:
            target_status, matched_files = await self._resolve_preparing_exit(record_id, new_status)
        self._mark_audit(record_id, actor_id)
        record, old_status = await self.repo.update_status(record_id, target_status)
        if matched_files:
            await self.repo.set_files(record, matched_files)
            record = await self.repo.get_with_relations(record_id)
        if old_status != record.status:
            await self._record_event(
                record_id=record_id,
                kind="status_changed",
                actor_id=actor_id,
                from_status=old_status,
                to_status=record.status,
            )
            await self._fire_status_change(record, old_status)
        return record, old_status

    async def assign_user(
        self, record_id: int, user_id: UUID, *, actor_id: UUID | None = None
    ) -> tuple[Record, RecordStatus]:
        """Assign user to a record and fire RecordFlow trigger if status changed.

        Args:
            record_id: Record ID.
            user_id: User UUID.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Tuple of (updated record, old status).

        Raises:
            RecordConstraintViolationError: If unique_by is violated.
        """
        record = await self.repo.get_with_record_type(record_id)
        await self._check_unique_per_user(user_id, record)
        self._mark_audit(record_id, actor_id)
        record, old_status = await self.repo.assign_user(record_id, user_id)
        await self._record_event(
            record_id=record_id,
            kind="assigned",
            actor_id=actor_id,
            from_status=old_status,
            to_status=record.status,
            new_value={"user_id": str(user_id)},
        )
        if old_status != record.status:
            await self._fire_status_change(record, old_status)
        return record, old_status

    async def claim_record(
        self, record_id: int, user_id: UUID, *, actor_id: UUID | None = None
    ) -> Record:
        """Claim a record for a user with uniqueness constraint check.

        Mirrors ``assign_user``: assigns the user, moves the record to
        ``inwork`` and fires the RecordFlow status-change trigger, so taking a
        task from the pool runs the same automation as an admin assignment.

        Args:
            record_id: Record ID.
            user_id: User UUID claiming the record.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Updated record (relations loaded) with inwork status.

        Raises:
            RecordConstraintViolationError: If unique_by is violated.
        """
        record = await self.repo.get_with_record_type(record_id)
        old_status = record.status
        await self._check_unique_per_user(user_id, record)
        self._mark_audit(record_id, actor_id)
        await self.repo.claim_record(record_id, user_id)
        updated = await self.repo.get_with_relations(record_id)
        await self._record_event(
            record_id=record_id,
            kind="assigned",
            actor_id=actor_id,
            from_status=old_status,
            to_status=updated.status,
            new_value={"user_id": str(user_id), "via": "claim"},
        )
        if old_status != updated.status:
            await self._fire_status_change(updated, old_status)
        return updated

    async def claim_random_from_pool(
        self,
        criteria: RecordSearchCriteria,
        user_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> Record | None:
        """Claim a random record matching ``criteria`` for ``user_id``.

        Picks one random record from the pool (typically an unassigned
        ``pending`` record of a given type) and claims it via ``claim_record``.
        Returns ``None`` when nothing matches, so the router can answer 404
        without reaching into the repository itself.

        ``find_random`` runs with ``for_update=True`` (``FOR UPDATE SKIP
        LOCKED``): the chosen row is locked for this transaction until
        ``claim_record`` commits, so a concurrent claimer skips it and two
        users can never win the same pool record.
        """
        record = await self.repo.find_random(criteria, for_update=True)
        if record is None:
            return None
        assert record.id is not None  # find_random returns a persisted record
        return await self.claim_record(record.id, user_id, actor_id=actor_id)

    async def unassign_user(
        self, record_id: int, *, actor_id: UUID | None = None
    ) -> tuple[Record, RecordStatus]:
        """Remove user from a record and fire RecordFlow trigger if status changed.

        Args:
            record_id: Record ID.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Tuple of (updated record, old status).
        """
        self._mark_audit(record_id, actor_id)
        record, old_status = await self.repo.unassign_user(record_id)
        await self._record_event(
            record_id=record_id,
            kind="unassigned",
            actor_id=actor_id,
            from_status=old_status,
            to_status=record.status,
        )
        if old_status != record.status:
            await self._fire_status_change(record, old_status)
        return record, old_status

    async def submit_data(
        self,
        record_id: int,
        data: RecordData,
        new_status: RecordStatus,
        user_id: UUID | None = None,
        *,
        actor_id: UUID | None = None,
    ) -> tuple[Record, RecordStatus]:
        """Submit record data with a status transition and fire RecordFlow trigger.

        Auto-assigns ``user_id`` when the record has no user yet (admin bypass).

        Args:
            record_id: Record ID.
            data: Validated record data.
            new_status: New status to set alongside data.
            user_id: Current user ID; assigned to the record when it has no user.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Tuple of (updated record, old status).

        Raises:
            RecordConstraintViolationError: If unique_by is violated on auto-assign.
        """
        transfer_to: UUID | None = None
        if user_id is not None:
            record_check = await self.repo.get_with_record_type(record_id)
            if record_check.user_id is None:
                await self._check_unique_per_user(user_id, record_check)
                self._mark_audit(record_id, actor_id)
                await self.repo.ensure_user_assigned(record_id, user_id)
                await self._record_event(
                    record_id=record_id,
                    kind="assigned",
                    actor_id=actor_id,
                    new_value={"user_id": str(user_id), "via": "submit"},
                )
            elif (
                record_check.record_type.shared_editing
                and record_check.user_id != user_id
                and actor_id is not None
            ):
                transfer_to = user_id
                await self._record_event(
                    record_id=record_id,
                    kind="assigned",
                    actor_id=actor_id,
                    new_value={"user_id": str(user_id), "via": "shared_submit"},
                )
            else:
                await self.repo.ensure_user_assigned(record_id, user_id)

        self._mark_audit(record_id, actor_id)
        record, old_status = await self.repo.update_data(
            record_id, data, new_status=new_status, reassign_to=transfer_to
        )
        await self._record_event(
            record_id=record_id,
            kind="data_submitted",
            actor_id=actor_id,
            from_status=old_status,
            to_status=record.status,
            new_value={"fields": sorted(data.keys())},
        )
        await self._fire_status_change(record, old_status)

        # Register output files that appeared on disk and emit file events
        if new_status == RecordStatus.finished:
            await self._sync_output_files(record)

        return record, old_status

    async def prefill_data(self, record_id: int, data: RecordData) -> tuple[Record, RecordStatus]:
        """Write prefill data without firing RecordFlow triggers or audit events.

        For pipeline tasks writing preliminary data to pending/blocked/preparing
        records.
        Caller is responsible for status checks and data merging.

        Args:
            record_id: Record ID.
            data: Prefill data (already validated/merged by caller).

        Returns:
            Tuple of (updated record, old status).
        """
        return await self.repo.update_data(record_id, data)

    async def update_data(
        self,
        record_id: int,
        data: RecordData,
        *,
        acting_user: User | None = None,
        actor_id: UUID | None = None,
    ) -> tuple[Record, RecordStatus]:
        """Update record data (no status change) and fire data-update trigger.

        Args:
            record_id: Record ID.
            data: Validated record data.
            acting_user: API caller; ``None`` marks a trusted in-process call
                that bypasses the post-submit edit lock.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Tuple of (updated record, old status).

        Raises:
            RecordEditLockedError: If the record is finished and its type
                locks submitted records for *acting_user*.
        """
        transfer_to: UUID | None = None
        if acting_user is not None:
            record = await self.repo.get_with_relations(record_id)
            if not acting_user.is_superuser:
                ensure_record_editable(record, acting_user)
            if (
                record.record_type.shared_editing
                and record.user_id != acting_user.id
                and actor_id is not None
            ):
                transfer_to = acting_user.id
                await self._record_event(
                    record_id=record_id,
                    kind="assigned",
                    actor_id=actor_id,
                    new_value={"user_id": str(acting_user.id), "via": "shared_update"},
                )
        self._mark_audit(record_id, actor_id)
        record, old_status = await self.repo.update_data(record_id, data, reassign_to=transfer_to)
        await self._record_event(
            record_id=record_id,
            kind="data_updated",
            actor_id=actor_id,
            new_value={"fields": sorted(data.keys())},
        )
        await self._fire_data_update(record)
        return record, old_status

    async def notify_file_change(self, record: Record) -> None:
        """Fire a file-change trigger for a record.

        Args:
            record: Record whose files changed.
        """
        await self._fire_file_change(record)

    async def bulk_update_status(
        self,
        record_ids: list[int],
        new_status: RecordStatus,
        *,
        acting_user: User | None = None,
        actor_id: UUID | None = None,
    ) -> None:
        """Update status for multiple records and fire triggers for each changed record.

        ``preparing`` records are routed through :meth:`update_status` one by
        one so the exit re-validation applies (preparing → pending may land in
        ``blocked``) — the bulk repo path would bypass it.

        Args:
            record_ids: List of record IDs.
            new_status: New status to set.
            acting_user: API caller; ``None`` marks a trusted in-process call
                that bypasses the post-submit edit lock.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Raises:
            RecordEditLockedError: If any target record is finished and its
                type locks submitted records for *acting_user*. Raised before
                any status is mutated.
            BusinessRuleViolationError: If any target record is preparing and
                ``new_status`` is inwork/finished. Raised before any status
                is mutated.
        """
        # Capture old statuses (and enforce the edit lock) before bulk update
        old_statuses: dict[int, RecordStatus] = {}
        preparing_ids: list[int] = []
        for record_id in record_ids:
            record = await self.repo.get_optional(record_id)
            if record:
                if acting_user is not None and not acting_user.is_superuser:
                    with_type = await self.repo.get_with_relations(record_id)
                    ensure_record_editable(with_type, acting_user)
                if record.status == RecordStatus.preparing:
                    if new_status in (RecordStatus.inwork, RecordStatus.finished):
                        raise BusinessRuleViolationError(
                            f"Record {record_id} is still preparing — it must leave "
                            f"via 'pending' (with file re-validation) before "
                            f"'{new_status.value}'."
                        )
                    preparing_ids.append(record_id)
                    continue
                old_statuses[record_id] = record.status

        for marked_id in old_statuses:
            self._mark_audit(marked_id, actor_id)
        await self.repo.bulk_update_status(list(old_statuses), new_status)

        # Preparing records take the single-record path: exit re-validation
        # applies and each fires its own trigger.
        for record_id in preparing_ids:
            await self.update_status(record_id, new_status, acting_user=acting_user)

        # Fire triggers for each record whose status actually changed
        for record_id, old_status in old_statuses.items():
            if old_status != new_status:
                updated = await self.repo.get_with_relations(record_id)
                await self._record_event(
                    record_id=record_id,
                    kind="status_changed",
                    actor_id=actor_id,
                    from_status=old_status,
                    to_status=new_status,
                    new_value={"via": "bulk"},
                )
                await self._fire_status_change(updated, old_status)

    async def invalidate_record(
        self,
        record_id: int,
        mode: str,
        source_record_id: int | None = None,
        reason: str | None = None,
        *,
        acting_user: User | None = None,
        actor_id: UUID | None = None,
    ) -> Record:
        """Invalidate a record and fire RecordFlow trigger on hard mode.

        Hard mode always fires the status trigger — even when the record was
        already pending — so on_status("pending") flows re-run on every
        re-invalidation. Soft mode never changes status and never fires.

        Args:
            record_id: ID of the record to invalidate.
            mode: "hard" resets to pending, "soft" only appends reason.
            source_record_id: ID of the triggering record.
            reason: Human-readable reason.
            acting_user: API caller; ``None`` marks a trusted in-process call
                that bypasses the post-submit edit lock. Only hard mode is
                gated — soft mode never changes data or status.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Updated record with relations.

        Raises:
            RecordEditLockedError: On hard mode, if the record is finished
                and its type locks submitted records for *acting_user*.
        """
        if mode == "hard" and acting_user is not None and not acting_user.is_superuser:
            with_type = await self.repo.get_with_relations(record_id)
            ensure_record_editable(with_type, acting_user)

        old_record = await self.repo.get(record_id)
        old_status = old_record.status

        self._mark_audit(record_id, actor_id)
        record = await self.repo.invalidate_record(
            record_id=record_id,
            mode=mode,
            source_record_id=source_record_id,
            reason=reason,
        )

        status_changed = old_status != record.status
        await self._record_event(
            record_id=record_id,
            kind="invalidated",
            actor_id=actor_id,
            from_status=old_status if status_changed else None,
            to_status=record.status if status_changed else None,
            new_value={"mode": mode, "source_record_id": source_record_id},
            reason=reason,
        )

        # Hard invalidation means "needs processing again" — fire even when the
        # status didn't change (pending → pending), so on_status("pending")
        # flows re-run. Handlers must be idempotent.
        if mode == "hard":
            await self._fire_invalidation(record, old_status)

        return record

    async def fail_record(
        self, record_id: int, reason: str, *, actor_id: UUID | None = None
    ) -> Record:
        """Mark a record as failed with a reason and fire RecordFlow triggers.

        Args:
            record_id: ID of the record to fail.
            reason: Human-readable reason for failure.
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Updated record with relations.
        """
        self._mark_audit(record_id, actor_id)
        record, old_status = await self.repo.fail_record(record_id, reason)
        logger.info(f"Record {record_id} manually failed")

        await self._record_event(
            record_id=record_id,
            kind="failed",
            actor_id=actor_id,
            from_status=old_status,
            to_status=record.status,
            reason=reason,
        )
        if old_status != record.status:
            await self._fire_status_change(record, old_status)

        return record

    async def check_files(
        self, record_id: int, *, actor_id: UUID | None = None
    ) -> tuple[list[str], dict[str, str]]:
        """Check file status, auto-unblock if ready, compute & compare checksums.

        For preparing records: no-op — prefill / file generation is in flight,
        so neither auto-unblock nor checksum bookkeeping may run.
        For blocked records: validates input files, transitions to pending if valid.
        For the rest: computes checksums, registers newly appeared OUTPUT
        files as ``RecordFileLink`` rows, updates DB, notifies on change.

        Returns:
            Tuple of (changed file keys, current checksums).
            Empty tuple ([], {}) if record stays blocked or is preparing.
        """
        record = await self.repo.get_with_relations(record_id)
        record_read = RecordRead.model_validate(record)

        if record.status == RecordStatus.preparing:
            return [], {}

        # Fetch parent once — feeds fallback pattern resolution for both
        # input validation (blocked records) and the OUTPUT checksum scan.
        parent_read = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)

        # Auto-unblock: if record is blocked, check whether input files are now present
        if record.status == RecordStatus.blocked:
            file_result = await validate_record_files(record_read, parent=parent_read)
            if file_result is not None and file_result.valid:
                if file_result.matched_files:
                    await self.repo.set_files(record, file_result.matched_files)
                record, _ = await self.update_status(
                    record_id, RecordStatus.pending, actor_id=actor_id
                )
                record_read = RecordRead.model_validate(record)
            else:
                return [], {}

        new_checksums = await Files.for_reader(record_read, parent=parent_read).checksums(
            record_read.record_type.file_registry or []
        )
        old_checksums = _stored_checksums(record_read)
        changed = Files.checksums_changed(old_checksums, new_checksums)

        await self._register_output_links(record, record_read, new_checksums, parent_read)

        await self.repo.update_checksums(record, new_checksums)

        if changed:
            await self.notify_file_change(record)

        return list(changed), new_checksums

    async def delete_record_cascade(
        self, record_id: int, *, actor_id: UUID | None = None
    ) -> tuple[list[int], int]:
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
        # Audit snapshots flush in the same transaction as the DELETE; the
        # FK's ON DELETE SET NULL detaches them from the removed rows.
        for rid, snapshot in reads.items():
            await self._record_event(
                record_id=rid,
                kind="deleted",
                actor_id=actor_id,
                from_status=snapshot.status,
                old_value={
                    "record_id": rid,
                    "record_type_name": snapshot.record_type_name,
                    "patient_id": snapshot.patient_id,
                    "study_uid": snapshot.study_uid,
                    "series_uid": snapshot.series_uid,
                    "user_id": str(snapshot.user_id) if snapshot.user_id else None,
                    "parent_record_id": snapshot.parent_record_id,
                },
                new_value={"via": "cascade", "root_record_id": record_id},
            )
        # Keep the transaction open: commit only after we've issued the DELETE,
        # so the row locks acquired above cover the whole check-and-delete.
        await self.repo.delete_records(deleted_ids, commit=False)
        await self.repo.session.commit()
        # sse-capture: explicit emit, UoW-invisible (Core bulk DML in delete_records).
        # Enriched from pre-delete snapshots so the owning non-admin user gets
        # the delete — a bare id-only event carries no record_type_name/user_id
        # and the RBAC filter would deliver it to admins only.
        emit_record_events(
            EntityEvent(
                entity="record",
                action="deleted",
                id=str(rid),
                record_type_name=read.record_type_name,
                user_id=read.user_id,
            )
            for rid, read in reads.items()
        )

        files_removed = 0
        for p in paths_to_unlink:
            try:
                await Files.in_thread(p.unlink)
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

        Records whose anonymized identifiers are missing fall back to raw
        UIDs (admin/UI-triggered cascade keeps working on legacy data —
        cf. ``Files.for_reader``).
        """
        f = Files.for_reader(record_read)
        working_dirs = f.dirs()
        target_dir = (
            working_dirs[file_def.level]
            if file_def.level and file_def.level in working_dirs
            else f.dir()
        )

        if file_def.multiple:
            candidates = await Files.in_thread(f.glob, file_def)
        else:
            file_path = target_dir / Files.render_for(
                record_read, file_def.pattern, parent=parent_read
            )
            if not await Files.in_thread(file_path.is_file):
                return []
            candidates = [file_path]

        return cast(list[Path], await Files.in_thread(_filter_in_sandbox, candidates, target_dir))

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

    async def clear_output_files(
        self, record_id: int, *, actor_id: UUID | None = None
    ) -> tuple[list[str], int]:
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
                await Files.in_thread(p.unlink)
            except FileNotFoundError:
                continue
            deleted_files.append(p.name)
            logger.info(f"Deleted output file {p} for record {record_id}")

        deleted_links = await self.repo.delete_output_file_links(record)

        await self._record_event(
            record_id=record_id,
            kind="files_cleared",
            actor_id=actor_id,
            new_value={"files": deleted_files, "links": deleted_links},
        )

        logger.info(
            f"Cleared output files for record {record_id}: "
            f"{len(deleted_files)} files, {deleted_links} links"
        )
        return deleted_files, deleted_links

    async def update_context_info(
        self, record_id: int, context_info: str | None, *, actor_id: UUID | None = None
    ) -> Record:
        """Replace ``context_info`` on a record with an audit event.

        Args:
            record_id: Record ID.
            context_info: New markdown source (``None`` clears the field).
            actor_id: Audit actor; ``None`` marks a system/worker call.

        Returns:
            Updated record with relations loaded.
        """
        record = await self.repo.get(record_id)
        old_value = record.context_info
        self._mark_audit(record_id, actor_id)
        updated = await self.repo.update_fields(record_id, {"context_info": context_info})
        await self._record_event(
            record_id=record_id,
            kind="context_info_updated",
            actor_id=actor_id,
            old_value={"context_info": old_value},
            new_value={"context_info": context_info},
        )
        return updated

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

        Raises:
            InvalidPatientIdentifierError: If ``patient_id`` violates DICOM
                format. Matches the trim-on-read contract used by
                :class:`StudyService`.
        """
        from clarinet.models.patient import validate_patient_id

        patient_id = validate_patient_id(patient_id)
        if not self.engine:
            return
        for file_name in changed_files:
            await self.engine.handle_file_update(file_name, patient_id, source_record=source_record)

    # ── Private helpers ──────────────────────────────────────────────────

    async def _check_unique_per_user(self, user_id: UUID, record: Record) -> None:
        """Check that assigning user_id to record does not violate unique_by.

        Thin wrapper over ``RecordRepository.ensure_unique_by`` — that method
        self-gates on ``record_type.unique_by`` (no-op when ``None``).

        Args:
            user_id: User being assigned.
            record: Record with record_type eagerly loaded.

        Raises:
            RecordUniquePerUserError: If an existing record already matches
                every selected unique_by partition for this DICOM context.
        """
        await self.repo.ensure_unique_by(
            record.record_type,
            user_id=user_id,
            parent_record_id=record.parent_record_id,
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=record.series_uid,
        )

    async def _resolve_preparing_exit(
        self, record_id: int, new_status: RecordStatus
    ) -> tuple[RecordStatus, dict[str, str]]:
        """Resolve the target status for a record leaving ``preparing``.

        Records created as ``preparing`` skip creation-time auto-blocking, so
        missing input files are caught on exit instead. For the ``pending``
        target the files are validated before any status is written; an
        invalid set redirects the transition to ``blocked`` (check-files
        unblocks it later). No file registry → pending. ``inwork`` and
        ``finished`` are rejected — a preparing record must pass through
        ``pending`` and its file re-validation first.

        Returns:
            Tuple of (resolved status, matched files for ``set_files``).

        Raises:
            BusinessRuleViolationError: On a direct preparing → inwork/finished
                transition (→ 409).
        """
        if new_status in (RecordStatus.inwork, RecordStatus.finished):
            raise BusinessRuleViolationError(
                f"Record {record_id} is still preparing — it must leave via "
                f"'pending' (with file re-validation) before '{new_status.value}'."
            )
        if new_status != RecordStatus.pending:
            return new_status, {}
        record = await self.repo.get_with_relations(record_id)
        record_read = RecordRead.model_validate(record)
        parent_read = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)
        file_result = await validate_record_files(record_read, parent=parent_read)
        if file_result is None:
            return RecordStatus.pending, {}
        if not file_result.valid:
            return RecordStatus.blocked, {}
        return RecordStatus.pending, file_result.matched_files or {}

    async def _register_output_links(
        self,
        record: Record,
        record_read: RecordRead,
        checksums: dict[str, str],
        parent: RecordRead | None = None,
    ) -> None:
        """Create links for OUTPUT files discovered by a checksum scan.

        Never raises: link registration is bookkeeping on top of the caller's
        main flow (submit / check-files) and must not fail it after the data
        is already committed.
        """
        record_id = record.id
        new_links = _missing_output_links(record_read, checksums, parent)
        if not new_links:
            return
        try:
            created = await self.repo.add_file_links(record, new_links)
        except Exception as e:
            logger.warning(f"Failed to register output file links for record {record_id}: {e}")
            return
        if created:
            logger.info(
                f"Record {record_id}: registered {created} output file link(s): {sorted(new_links)}"
            )

    async def _sync_output_files(self, record: Record) -> None:
        """Reconcile OUTPUT file state on disk with the DB after a submission.

        Computes checksums on disk for OUTPUT files, registers files that
        appeared since the last sync as ``RecordFileLink`` rows, updates
        stored checksums, and emits file-update events for any changed files
        so that downstream file flows (e.g. invalidation) are triggered.
        Link/checksum bookkeeping runs even without a RecordFlow engine —
        only event emission requires it. The SHA256 scan adds I/O latency to
        finished submissions proportional to output size — the same trade-off
        the engine-enabled path has always had.

        Args:
            record: Record with relations loaded (must have record_type, patient).
        """
        record_read = RecordRead.model_validate(record)
        output_defs = [
            fd for fd in (record_read.record_type.file_registry or []) if fd.role == FileRole.OUTPUT
        ]
        if not output_defs:
            return

        # Parent feeds fallback placeholder resolution, e.g. {user_id} on
        # auto-records — must match the download path's resolution.
        parent_read: RecordRead | None = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)

        try:
            new_checksums = await Files.for_reader(record_read, parent=parent_read).checksums(
                output_defs
            )
        except Exception as e:
            logger.warning(f"Failed to compute output checksums for record {record.id}: {e}")
            return

        old_checksums = _stored_checksums(record_read)

        # A file without a link has no stored checksum, so any link to create
        # implies a non-empty changed set — safe to early-return here.
        changed = Files.checksums_changed(old_checksums, new_checksums)
        if not changed:
            return

        await self._register_output_links(record, record_read, new_checksums, parent_read)

        # Update stored checksums in DB
        try:
            await self.repo.update_checksums(record, new_checksums)
        except Exception as e:
            logger.warning(f"Failed to update checksums for record {record.id}: {e}")

        if not self.engine:
            return

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

    async def _fire_invalidation(self, record: Record, old_status: RecordStatus | None) -> None:
        """Convert record to RecordRead and fire the cycle-guarded invalidation dispatch."""
        if not self.engine:
            return
        record_read = RecordRead.model_validate(record)
        await self.engine.handle_record_invalidation(record_read, old_status)

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
