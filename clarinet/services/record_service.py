"""Service layer for record-related business logic with RecordFlow integration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_never, cast
from uuid import UUID

from clarinet.exceptions.domain import (
    AnonPathError,
    AuthorizationError,
    BusinessRuleViolationError,
    ConcurrentTransitionError,
    RecordOwnerLacksRoleError,
    TransitionNotAllowedError,
    UnsafePathError,
)
from clarinet.exceptions.domain import FileNotFoundError as DomainFileNotFoundError
from clarinet.exceptions.http import UNPROCESSABLE_ENTITY
from clarinet.files import Files, join_within
from clarinet.models import Record, RecordRead, RecordStatus
from clarinet.models.actor import Actor, audit_actor_id, can_access
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.models.record_event import RecordEvent
from clarinet.repositories.user_repository import UserRepository
from clarinet.services.events.capture import emit_record_events, mark_pending_audit
from clarinet.services.events.models import EntityEvent
from clarinet.services.file_validation import validate_record_files
from clarinet.services.record_lifecycle import (
    Assign,
    Claim,
    Command,
    Create,
    Edit,
    Effect,
    Fail,
    InputsVerdict,
    RecordSnapshot,
    Restart,
    SetStatus,
    Submit,
    TypeRules,
    Unassign,
    Unblock,
    decide,
    decide_create,
    needs_inputs,
)
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from clarinet.models.record import RecordType
    from clarinet.models.record_event import RecordEventKind
    from clarinet.repositories.record_event_repository import RecordEventRepository
    from clarinet.repositories.record_repository import RecordRepository, RecordSearchCriteria
    from clarinet.services.recordflow.engine import RecordFlowEngine
    from clarinet.types import RecordData


def _filter_in_sandbox(paths: list[Path], sandbox: Path) -> list[Path]:
    """Drop paths whose resolved location escapes the sandbox directory.

    Both ``Path.resolve()`` calls hit the filesystem (symlink chasing), so
    callers should run this through ``Files.in_thread``.
    """
    sandbox_resolved = sandbox.resolve()
    return [p for p in paths if p.resolve().is_relative_to(sandbox_resolved)]


def _render_output_path(
    record: RecordRead, fd: FileDefinitionRead, parent: RecordRead | None
) -> str:
    """Render ``fd.pattern`` for ``record``, translating ``UnsafePathError`` into a 422.

    Shared by the pre-submit validation pass (``RecordService._validate_output_paths``,
    run before the submission is persisted) and the post-scan reconciliation
    (``_missing_output_links``, run after) — both need the identical PHI-safe
    shape: neither the WARNING nor the 422 body carries ``exc.value``. The log
    gets ``str(exc)`` (placeholder key + reason, never the value); the caller
    gets the file definition's name, which is what they can actually act on.

    The value is withheld rather than echoed because the caller did not
    necessarily produce it. The ``{data.*}`` ban applies to *patterns*, not to
    the field dict — ``fields_from`` (``files/_patterns.py``) still exposes a
    ``data`` key — but since no pattern may reference it, nothing the caller
    submitted can reach the rendered value. Every name a pattern can actually
    interpolate is a stored record attribute, and three of them —
    ``patient_id``, ``study_uid``, ``series_uid`` — are exactly what
    ``api/masking.py`` may withhold from a non-superuser once the patient is
    anonymized, while the render here runs against the raw value. This helper
    also serves check-files, where nothing was submitted at all. Today the
    guard only trips on a degenerate value (of those names only
    ``patient_id``'s grammar admits ``.`` or ``..`` at all), so echoing it
    disclosed nothing in practice — but #552 relaxing the
    pattern grammar would turn that into a live disclosure with no test
    standing against it.
    """
    try:
        return Files.render_for(record, fd.pattern, parent=parent)
    except UnsafePathError as exc:
        logger.warning(
            f"unsafe output path rejected for record {record.id}, "
            f"file definition '{fd.name}': {exc}"
        )
        # Endpoint-neutral wording on purpose: this helper also runs from
        # check-files, where nothing was submitted, so "from the submitted
        # data" would be a lie there.
        raise UNPROCESSABLE_ENTITY.with_context(
            f"File '{fd.name}' cannot be safely resolved for this record: {exc}"
        ) from exc


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

    In practice ``Files(...).checksums()`` (called by both of this function's
    callers to build *checksums*) already applies the same value guard to the
    same pattern before this function ever sees the key, so the
    ``_render_output_path`` call below cannot raise for any key that made it
    into *checksums* today. Kept as defence in depth — a future caller that
    doesn't pre-validate its checksums dict this way must not silently accept
    an unsafe path.
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
        missing[name] = collection_file or _render_output_path(record, fd, parent)
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


_MAX_TRANSITION_ATTEMPTS = 3


def _invalidation_note(reason: str | None, source_record_id: int | None) -> str | None:
    """Text an invalidation appends to ``context_info`` (``None`` = nothing)."""
    if reason is None and source_record_id is not None:
        return f"Invalidated by record #{source_record_id}"
    return reason or None


def _appended_note(cmd: Command) -> str | None:
    match cmd:
        case Fail(reason=reason):
            return f"Manually failed: {reason}"
        case Restart(reason=reason, source_record_id=source_record_id):
            return _invalidation_note(reason, source_record_id)
        case _:
            return None


def _changes_nothing(
    effect: Effect, snap: RecordSnapshot, *, data: RecordData | None, reason: str | None
) -> bool:
    """Same status, same owner, no data, no note — and not a hard invalidation (it always fires)."""
    return (
        effect.fire == "status"
        and effect.to_status == snap.status
        and effect.owner == snap.user_id
        and data is None
        and reason is None
    )


class RecordService:
    """Service wrapping record mutations with automatic RecordFlow triggers.

    When *event_repo* is provided, every mutation also appends a
    :class:`RecordEvent` audit row (``actor_id=None`` marks a system actor —
    see ``models/actor.py``).

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
        so an engine failure cannot lose it. Lifecycle commands (via
        ``_transition``) and cascade delete commit the event together with
        the mutation, in the same transaction; record creation commits it
        right after the INSERT, with the file links and any auto-block.
        Non-transition mutations that don't explicitly commit — context
        info updates, soft invalidation, clearing output files — leave the
        event for the next commit on the shared session (request
        teardown); a crash before that loses the event but never the
        mutation for those paths. No-op when auditing is disabled
        (``event_repo is None``).
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

        Only needed for non-transition mutations that commit on their own
        (soft invalidation, context info updates) — the repo write commits
        internally and the matching ``RecordEvent`` commits later at request
        teardown, so the SSE capture cannot pair them per-commit. Setting
        this breadcrumb right before the committing write lets the capture emit
        one enriched record event (``user_id`` = the acting user) and skip the
        drift warning. Lifecycle commands never call this — ``_transition``
        commits the audit row together with the change, so the capture pairs
        them per-commit already. No-op when SSE is off (see
        ``mark_pending_audit``).
        """
        mark_pending_audit(self.repo.session, record_id, actor_id)

    async def _transition(
        self,
        record_id: int,
        cmd: Command,
        actor: Actor,
        *,
        data: RecordData | None = None,
    ) -> tuple[Record, RecordStatus]:
        """Decide and apply one command in one transaction; SSE and RecordFlow after it.

        Owns the transaction boundary: loops ``_decide_one`` → ``_write_one``
        (neither ever commits), committing itself on a miss — it wrote
        nothing, and on SQLite the UPDATE already holds the write lock — and
        re-deciding against the fresh state, at most
        ``_MAX_TRANSITION_ATTEMPTS`` times. On a hit, the fresh re-read and
        the one commit follow; only once that commit has landed, so no
        transaction is open while they run, do SSE and RecordFlow fire.

        Returns:
            (fresh record, status before). A no-op returns the record unchanged.

        Raises:
            AuthorizationError | RecordLifecycleError: from ``decide``.
            RecordOwnerLacksRoleError: 409 — the new owner lacks the type's role.
            UserNotFoundError: 404 — the new owner does not exist.
            RecordUniquePerUserError: the new owner violates ``unique_by``.
            ConcurrentTransitionError: every attempt missed.
        """
        for _ in range(_MAX_TRANSITION_ATTEMPTS):
            record, snap, effect, matched = await self._decide_one(record_id, cmd, actor, data=data)
            if effect is None:
                return record, snap.status
            if await self._write_one(record, snap, effect, matched, cmd, actor, data=data):
                break
            await self.repo.session.commit()  # end the transaction before re-reading
        else:
            raise ConcurrentTransitionError(
                f"Record {record_id} kept changing during '{type(cmd).__name__}'; "
                f"gave up after {_MAX_TRANSITION_ATTEMPTS} attempts.",
                status=snap.status.value,
            )
        updated = await self.repo.get_with_relations(record_id, populate_existing=True)
        await self.repo.session.commit()
        self._emit_record_updated(updated, actor)
        await self._fire(effect, updated, snap.status)
        return updated, snap.status

    async def _decide_one(
        self,
        record_id: int,
        cmd: Command,
        actor: Actor,
        *,
        data: RecordData | None = None,
    ) -> tuple[Record, RecordSnapshot, Effect | None, dict[str, str]]:
        """Snapshot ``record_id`` and decide ``cmd`` against it. Never writes, never commits.

        Snapshot (``populate_existing``) → reads the parent (and may do file
        I/O for the inputs verdict) only when ``needs_inputs`` says so →
        ``decide``. Never writes to the DB, so a bulk caller can run this for
        every record in a batch before writing any of them
        (decide-all-then-write-all).

        Returns:
            (record as read, snapshot, effect, matched). ``effect`` is
            ``None`` for a command that changes nothing (a derived no-op —
            the caller must not call ``_write_one``; the writer itself would
            still bump ``changed_at``). ``matched`` is the input-file match
            set for linking on the way to ``pending``, computed only when
            ``needs_inputs`` says so (``{}`` otherwise).

        Raises:
            AuthorizationError | RecordLifecycleError: from ``decide``.
        """
        record = await self.repo.get_with_relations(record_id, populate_existing=True)
        snap = RecordSnapshot.of(record)
        inputs: InputsVerdict | None = None
        matched: dict[str, str] = {}
        if needs_inputs(cmd, snap):
            inputs, matched = await self._inputs_verdict(record)
        effect = decide(cmd, snap, actor, inputs=inputs)
        if _changes_nothing(effect, snap, data=data, reason=_appended_note(cmd)):
            return record, snap, None, matched
        return record, snap, effect, matched

    async def _write_one(
        self,
        record: Record,
        snap: RecordSnapshot,
        effect: Effect,
        matched: dict[str, str],
        cmd: Command,
        actor: Actor,
        *,
        data: RecordData | None = None,
        via: str | None = None,
    ) -> bool:
        """Conditionally write one decided (non-no-op) command. Never commits.

        New-owner checks, then the conditional write on the status and owner
        ``_decide_one`` saw. On a hit the audit event and matched input files
        join this same transaction before returning. Never commits — a bulk
        caller runs this once per record inside one shared transaction and
        rolls the whole batch back on the first miss (``False``); ``_transition``
        commits itself on a miss and retries. ``via`` is stamped on the audit
        event's ``new_value`` (e.g. ``"bulk"``) — see ``_audit_transition``.

        Returns:
            Whether the write landed. ``False`` means the record changed
            since ``_decide_one`` snapshotted it — nothing was written.

        Raises:
            RecordOwnerLacksRoleError: 409 — the new owner lacks the type's role.
            UserNotFoundError: 404 — the new owner does not exist.
            RecordUniquePerUserError: the new owner violates ``unique_by``.
        """
        await self._check_new_owner(record, effect.owner)
        written = await self.repo.write_transition(
            snap.record_id,
            expected_status=snap.status,
            expected_user_id=snap.user_id,
            to=effect.to_status,
            owner=effect.owner,
            data=data,
            reason=_appended_note(cmd),
        )
        if not written:
            return False
        await self._audit_transition(cmd, snap, effect, actor, data=data, via=via)
        if matched and effect.to_status == RecordStatus.pending:
            await self.repo.set_files(record, matched, commit=False)
        return True

    async def _parent_read(self, record: Record) -> RecordRead | None:
        if record.parent_record_id is None:
            return None
        return RecordRead.model_validate(
            await self.repo.get_with_relations(record.parent_record_id)
        )

    async def _inputs_verdict(self, record: Record) -> tuple[InputsVerdict, dict[str, str]]:
        """Validate input files; matched files come back for linking on the way to pending."""
        result = await validate_record_files(
            RecordRead.model_validate(record), parent=await self._parent_read(record)
        )
        if result is None:
            return "undeclared", {}
        if not result.valid:
            return "invalid", {}
        return "valid", dict(result.matched_files or {})

    async def _ensure_can_own(
        self, user_id: UUID, record_type: RecordType, *, status: RecordStatus | None
    ) -> None:
        """404 for an unknown user; 409 ``OWNER_LACKS_ROLE`` when they cannot access the type."""
        user = await UserRepository(self.repo.session).get_with_roles(user_id)
        if not can_access(record_type.role_name, user.is_superuser, user.role_names):
            raise RecordOwnerLacksRoleError(
                f"User {user_id} cannot own a '{record_type.name}' record: "
                "they hold neither its role nor superuser rights.",
                status=None if status is None else status.value,
            )

    async def _check_new_owner(self, record: Record, owner: UUID | None) -> None:
        """A new owner must exist, be able to access the type and keep ``unique_by``."""
        if owner is None or owner == record.user_id:
            return
        await self._ensure_can_own(owner, record.record_type, status=record.status)
        await self._check_unique_by(owner, record)

    def _emit_record_updated(self, record: Record, actor: Actor) -> None:
        # sse-capture: explicit emit, UoW-invisible (Core UPDATE in write_transition).
        emit_record_events(
            [
                EntityEvent(
                    entity="record",
                    action="updated",
                    id=str(record.id),
                    record_type_name=record.record_type_name,
                    user_id=audit_actor_id(actor),
                )
            ]
        )

    async def _audit_transition(
        self,
        cmd: Command,
        snap: RecordSnapshot,
        effect: Effect,
        actor: Actor,
        *,
        data: RecordData | None,
        via: str | None = None,
    ) -> None:
        """One audit event per command; from/to only when the status changed."""
        new_owner = None if effect.owner == snap.user_id else effect.owner
        new_value: dict[str, Any] = {}
        reason: str | None = None
        kind: RecordEventKind
        match cmd:
            case Claim():
                kind = "assigned"
                new_value = {"user_id": str(effect.owner), "via": "claim"}
            case Assign(user_id=user_id):
                kind = "assigned"
                new_value = {"user_id": str(user_id)}
            case Unassign():
                kind = "unassigned"
            case Submit() | Edit():
                kind = "data_submitted" if isinstance(cmd, Submit) else "data_updated"
                new_value = {"fields": sorted((data or {}).keys())}
                if new_owner is not None:
                    new_value["user_id"] = str(new_owner)
                    new_value["via"] = (
                        "shared_update"
                        if isinstance(cmd, Edit)
                        else "submit"
                        if snap.user_id is None
                        else "shared_submit"
                    )
            case Fail(reason=fail_reason):
                kind = "failed"
                reason = fail_reason
            case Restart(reason=restart_reason, source_record_id=source_record_id):
                kind = "invalidated"
                reason = restart_reason
                new_value = {"mode": "hard", "source_record_id": source_record_id}
            case SetStatus() | Unblock():
                kind = "status_changed"
            case _:
                assert_never(cmd)
        if via is not None:
            new_value["via"] = via
        changed = effect.to_status != snap.status
        await self._record_event(
            record_id=snap.record_id,
            kind=kind,
            actor_id=audit_actor_id(actor),
            from_status=snap.status if changed else None,
            to_status=effect.to_status if changed else None,
            new_value=new_value or None,
            reason=reason,
        )

    async def _fire(self, effect: Effect, record: Record, old_status: RecordStatus) -> None:
        match effect.fire:
            case "invalidation":
                await self._fire_invalidation(record, old_status)
            case "data_update":
                await self._fire_data_update(record)
            case "status":
                if effect.to_status != old_status:
                    await self._fire_status_change(record, old_status)
            case _:
                assert_never(effect.fire)

    # ── Public methods ───────────────────────────────────────────────────

    def precheck(self, record: Record, cmd: Command, actor: Actor) -> None:
        """Fail fast with the refusal ``_transition`` would raise, writing nothing.

        For endpoints that do expensive or destructive work before the write
        (schema validation, ``enforce_output_grids``, the Slicer validator).
        ``_transition`` re-checks against a fresh snapshot anyway.
        """
        decide(cmd, RecordSnapshot.of(record), actor)

    async def create_record(self, record: Record, *, actor: Actor) -> Record:
        """Create a record with file validation, blocking, and RecordFlow trigger.

        The requested status must be pending (or preparing for admins and
        system actors), a non-admin person may name only themselves or
        nobody as the owner — refused before the INSERT (``decide_create``)
        — and an owner, given or inherited from the parent, must hold the
        type's role or be a superuser (409 ``OWNER_LACKS_ROLE``, every
        actor). When ``parent_record_id`` is set, the parent is validated to
        exist (raises ``RecordNotFoundError`` otherwise). ``user_id`` is
        inherited from the parent only when the record's type has
        ``inherit_user_from_parent`` enabled and no explicit ``user_id`` was
        provided.

        Args:
            record: Record ORM instance to persist.
            actor: Who is creating the record.

        Returns:
            Created record with relations loaded.
        """
        record_type = await self.repo.get_record_type(record.record_type_name, with_files=False)
        decide_create(
            Create(status=record.status, owner_id=record.user_id), TypeRules.of(record_type), actor
        )
        # Fetch parent up front: validates existence, drives opt-in user_id
        # inheritance, and feeds fallback file-pattern resolution below.
        parent_read = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)
            if (
                record.user_id is None
                and record_type.inherit_user_from_parent
                and parent.user_id is not None
            ):
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
        if record.user_id is not None:
            await self._ensure_can_own(record.user_id, record_type, status=None)

        record = await self.repo.create_with_relations(record)
        assert record.id is not None  # persisted

        file_result = await validate_record_files(
            RecordRead.model_validate(record), parent=parent_read
        )
        auto_blocked = False
        if file_result is not None:
            if file_result.valid and file_result.matched_files:
                await self.repo.set_files(record, file_result.matched_files, commit=False)
            elif not file_result.valid and record.status != RecordStatus.preparing:
                # A miss means someone moved the new record first; the re-read shows it.
                auto_blocked = await self.repo.write_transition(
                    record.id,
                    expected_status=record.status,
                    expected_user_id=record.user_id,
                    to=RecordStatus.blocked,
                    owner=record.user_id,
                )
        record = await self.repo.get_with_relations(record.id, populate_existing=True)
        await self._record_event(
            record_id=record.id,
            kind="created",
            actor_id=audit_actor_id(actor),
            to_status=record.status,
            new_value={"record_type_name": record.record_type_name},
        )
        await self.repo.session.commit()
        if auto_blocked:
            self._emit_record_updated(record, actor)
        await self._fire_status_change(record, old_status=None)
        return record

    async def update_status(
        self, record_id: int, new_status: RecordStatus, *, actor: Actor
    ) -> tuple[Record, RecordStatus]:
        """Raw status change (``SetStatus``) — admins and system actors only.

        A preparing record may not jump to inwork/finished; ``preparing → pending``
        re-validates input files first and lands in ``blocked`` when they are
        invalid (never observable as pending-with-invalid-files); writing the
        current status again is a no-op — no audit, no flows.

        Returns:
            (fresh record, status before) — the final status may differ from
            ``new_status`` (see above).
        """
        return await self._transition(record_id, SetStatus(new_status), actor)

    async def assign_user(
        self, record_id: int, user_id: UUID, *, actor: Actor
    ) -> tuple[Record, RecordStatus]:
        """Make ``user_id`` the owner — admins and system actors only.

        Only a pending record moves (to inwork, firing ``on_status("inwork")``);
        every other status — finished, blocked and preparing included — stays.

        Raises:
            AuthorizationError: 403 for a non-admin person.
            UserNotFoundError: 404 — no such user.
            RecordOwnerLacksRoleError: 409 — the user cannot access the type.
            RecordUniquePerUserError: ``unique_by`` violated for the new owner.
        """
        return await self._transition(record_id, Assign(user_id), actor)

    async def claim_record(self, record_id: int, *, actor: Actor) -> Record:
        """Take an unassigned (or already own) pending/inwork record for the actor.

        The claimant is the person, or the service account for a system actor.
        pending → inwork fires ``on_status("inwork")``; re-claiming one's own
        inwork record changes nothing.

        Raises:
            AuthorizationError: 403 — someone else's record, or no type role.
            TransitionNotAllowedError: 409 — the record is not pending/inwork.
            RecordUniquePerUserError: ``unique_by`` violated.
        """
        record, _ = await self._transition(record_id, Claim(), actor)
        return record

    async def claim_random_from_pool(
        self, criteria: RecordSearchCriteria, *, actor: Actor
    ) -> Record | None:
        """Claim a random record matching ``criteria`` for the actor; ``None`` for an empty pool.

        ``find_random(for_update=True)`` locks the pick (``FOR UPDATE SKIP LOCKED``
        on PostgreSQL), so concurrent claimers get different records. Without row
        locks (SQLite) two claimers can pick the same one: the loser's claim is
        refused on the fresh state, and the next pick skips that record — at most
        ``_MAX_TRANSITION_ATTEMPTS`` picks.

        Raises:
            ConcurrentTransitionError: 409 — every pick was taken first.
            RecordUniquePerUserError: ``unique_by`` violated.
        """
        taken: set[int] = set()
        for _ in range(_MAX_TRANSITION_ATTEMPTS):
            pick = await self.repo.find_random(
                replace(criteria, exclude_ids=taken), for_update=True
            )
            if pick is None:
                return None
            assert pick.id is not None  # find_random returns a persisted record
            try:
                return await self.claim_record(pick.id, actor=actor)
            except (AuthorizationError, TransitionNotAllowedError) as exc:
                logger.info(
                    f"claim-next: record {pick.id} was taken first ({exc}); picking another"
                )
                taken = taken | {pick.id}
        raise ConcurrentTransitionError(
            "Every record picked from the pool was taken first; try again."
        )

    async def unassign_user(self, record_id: int, *, actor: Actor) -> tuple[Record, RecordStatus]:
        """Clear the owner; inwork falls back to pending (``on_status("pending")``).

        Admins and system actors on any status; the owner of a pending/inwork
        record whose type is ``releasable``.

        Raises:
            AuthorizationError: 403 — anyone else.
            TransitionNotAllowedError: 409 — the owner releasing another status.
        """
        return await self._transition(record_id, Unassign(), actor)

    async def submit_data(
        self, record_id: int, data: RecordData, new_status: RecordStatus, *, actor: Actor
    ) -> tuple[Record, RecordStatus]:
        """Submit data (``Submit``): pending/inwork/failed/pause → finished, or failed.

        An unassigned record becomes the submitter's — the service account for a
        system actor; on a ``shared_editing`` type the person submitting a
        colleague's record becomes its owner. The single ``data_submitted`` event
        records that owner change too.

        Raises:
            TransitionNotAllowedError: 409 — blocked, preparing or already finished
                (verbatim texts), or a target other than finished/failed.
            AuthorizationError: 403 — a person without mutation rights.
            RecordUniquePerUserError: ``unique_by`` violated by the new owner.
            CustomHTTPException: 422 if an OUTPUT pattern cannot be safely resolved
                (see ``_validate_output_paths``) — raised before anything is persisted.
        """
        if new_status == RecordStatus.finished:
            await self._validate_output_paths(record_id)
        record, old_status = await self._transition(record_id, Submit(new_status), actor, data=data)
        if new_status == RecordStatus.finished:
            await self.sync_output_files(record)
        return record, old_status

    async def _validate_output_paths(self, record_id: int) -> None:
        """Reject an unsafe OUTPUT pattern before ``submit_data`` persists anything.

        ``submit_data`` commits the new data/status via ``_transition`` before
        ``sync_output_files`` ever runs a path-safety check — without this,
        a rejected submission would already be durably stored by the time the
        rejection happens (see ``sync_output_files``'s docstring). Pure
        rendering only (``Files.render_for``, no filesystem I/O — unlike
        ``sync_output_files``'s ``checksums()`` scan), so it is cheap to run
        up front, before the record's current state changes.

        Checked against the record's identity fields (``patient_id`` etc.) as
        currently stored, not the data being submitted: ``{data.*}``
        placeholders are banned from patterns (issue #552), so the submitted
        data cannot itself affect whether an OUTPUT pattern resolves safely.

        Must render exactly the definitions ``Files.checksums()`` would render
        for the same scan (``facade.py``'s ``checksums()``), never more: a
        ``multiple=True`` (collection) definition is globbed there, wildcards
        replacing placeholders, so its pattern's *values* are never rendered;
        a definition whose ``level`` has no working directory for this record
        is skipped there outright. Pre-rejecting either would 422 a record
        whose real checksum scan — and thus real submission — would have
        succeeded.

        ``Files.for_reader`` itself can raise ``AnonPathError``: its fallback
        retry (``facade.py``'s ``for_reader``) sits outside any handler, and
        a rendered segment that is a bare ``.``/``..`` is rejected regardless
        of the fallback flag (``_storage._safe_render``) — reachable on the
        default template for a not-yet-anonymized patient whose raw id is
        ``".."`` or ``"."`` (legal per ``PATIENT_ID_REGEX``). That must not
        turn into a 500 from *this* pre-check: degrade to round 1's
        conservative behavior (validate every non-``multiple`` OUTPUT
        definition, skipping none) rather than propagate.
        """
        record = await self.repo.get_with_relations(record_id)
        output_defs = [
            fd
            for fd in (record.record_type.file_registry or [])
            if fd.role == FileRole.OUTPUT and not fd.multiple
        ]
        if not output_defs:
            return
        record_read = RecordRead.model_validate(record)
        parent_read: RecordRead | None = None
        if record.parent_record_id is not None:
            parent = await self.repo.get_with_relations(record.parent_record_id)
            parent_read = RecordRead.model_validate(parent)
        try:
            working_dirs = Files.for_reader(record_read, parent=parent_read).dirs()
        except AnonPathError:
            working_dirs = None
        default_level = DicomQueryLevel(record_read.record_type.level)
        for fd in output_defs:
            if working_dirs is not None and working_dirs.get(fd.level or default_level) is None:
                continue
            _render_output_path(record_read, fd, parent_read)

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
        self, record_id: int, data: RecordData, *, actor: Actor
    ) -> tuple[Record, RecordStatus]:
        """Edit a finished record's data (``Edit``); the status stays; fires ``on_data_update``.

        On a ``shared_editing`` type the editing person becomes the owner.

        Raises:
            TransitionNotAllowedError: 409 — the record is not finished.
            RecordEditLockedError: 409 — the type locks submitted records for this person.
            AuthorizationError: 403 — a person without mutation rights.
        """
        return await self._transition(record_id, Edit(), actor, data=data)

    async def notify_file_change(self, record: Record) -> None:
        """Fire a file-change trigger for a record.

        Args:
            record: Record whose files changed.
        """
        await self._fire_file_change(record)

    async def bulk_update_status(
        self, record_ids: list[int], new_status: RecordStatus, *, actor: Actor
    ) -> None:
        """Set one status on many records — all of them or none.

        Ids are deduplicated and sorted, so every bulk request locks rows in the
        same order and two of them cannot deadlock. Every record is decided
        (``_decide_one``, read-only) before anything is written, so one refusal
        (403/409) leaves every record untouched; the writes (``_write_one``)
        then share this one transaction — the first miss rolls the whole batch
        back and raises ``ConcurrentTransitionError`` (unlike ``_transition``,
        bulk never retries). SSE and RecordFlow fire per written record only
        after the commit. Unknown ids are skipped.
        """
        cmd = SetStatus(new_status)
        decided: list[tuple[Record, RecordSnapshot, Effect, dict[str, str]]] = []
        for record_id in sorted(set(record_ids)):
            if await self.repo.get_optional(record_id) is None:
                continue
            record, snap, effect, matched = await self._decide_one(record_id, cmd, actor)
            if effect is not None:
                decided.append((record, snap, effect, matched))

        written: list[tuple[RecordSnapshot, Effect]] = []
        for record, snap, effect, matched in decided:
            if not await self._write_one(record, snap, effect, matched, cmd, actor, via="bulk"):
                await self.repo.session.rollback()
                raise ConcurrentTransitionError(
                    f"Record {snap.record_id} changed during the bulk status update; "
                    f"no record was changed.",
                    status=snap.status.value,
                )
            written.append((snap, effect))

        updated = [
            await self.repo.get_with_relations(snap.record_id, populate_existing=True)
            for snap, _ in written
        ]
        await self.repo.session.commit()
        for (snap, effect), record in zip(written, updated, strict=True):
            self._emit_record_updated(record, actor)
            await self._fire(effect, record, snap.status)

    async def invalidate_record(
        self,
        record_id: int,
        mode: str,
        source_record_id: int | None = None,
        reason: str | None = None,
        *,
        actor: Actor,
    ) -> Record:
        """Invalidate a record.

        Hard mode is ``Restart``: any status except preparing returns to pending,
        data and owner kept, the reason appended; it always fires
        ``handle_record_invalidation`` — even pending → pending — so
        ``on_status("pending")`` flows re-run. People need mutation rights and get
        409 on a locked finished record. Soft mode only appends the reason: no
        status change, no flows, no lifecycle check.

        Raises:
            RecordEditLockedError: hard mode, a non-admin on a locked finished record.
            AuthorizationError: hard mode, a person without mutation rights.
        """
        if mode == "hard":
            record, _ = await self._transition(
                record_id, Restart(reason=reason, source_record_id=source_record_id), actor
            )
            return record
        note = _invalidation_note(reason, source_record_id)
        if note is None:
            record = await self.repo.get_with_relations(record_id)
        else:
            self._mark_audit(record_id, audit_actor_id(actor))
            record = await self.repo.append_context_info(record_id, note)
        await self._record_event(
            record_id=record_id,
            kind="invalidated",
            actor_id=audit_actor_id(actor),
            new_value={"mode": "soft", "source_record_id": source_record_id},
            reason=reason,
        )
        return record

    async def fail_record(self, record_id: int, reason: str, *, actor: Actor) -> Record:
        """Mark a pending/inwork record failed (``Fail``), noting ``"Manually failed: <reason>"``.

        Raises:
            TransitionNotAllowedError: 409 — the record is not pending/inwork.
            AuthorizationError: 403 — a person without mutation rights.
        """
        record, _ = await self._transition(record_id, Fail(reason=reason), actor)
        logger.info(f"Record {record_id} manually failed")
        return record

    async def check_files(
        self, record_id: int, *, actor: Actor
    ) -> tuple[list[str], dict[str, str]]:
        """Check file status, auto-unblock if ready, compute & compare checksums.

        A person needs the lifecycle policy's mutation rights (``Unblock``).
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
        self.precheck(record, Unblock(), actor)
        if record.status == RecordStatus.preparing:
            return [], {}
        if record.status == RecordStatus.blocked:
            record, _ = await self._transition(record_id, Unblock(), actor)
            if record.status == RecordStatus.blocked:
                return [], {}
        record_read = RecordRead.model_validate(record)
        parent_read = await self._parent_read(record)

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

    async def delete_record_cascade(self, record_id: int, *, actor: Actor) -> tuple[list[int], int]:
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
                actor_id=audit_actor_id(actor),
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
            # join_within before the probe, not just _filter_in_sandbox after
            # it: the sandbox filter guards what is finally deleted or served,
            # but `.is_file()` on an unchecked join is itself an existence
            # oracle for paths outside target_dir — the same defect fixed at
            # the file-validation probe. This is the last render-then-join
            # site, so the "every join is contained" claim now holds literally.
            file_path = join_within(
                target_dir, Files.render_for(record_read, file_def.pattern, parent=parent_read)
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

    async def clear_output_files(self, record_id: int, *, actor: Actor) -> tuple[list[str], int]:
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
            actor_id=audit_actor_id(actor),
            new_value={"files": deleted_files, "links": deleted_links},
        )

        logger.info(
            f"Cleared output files for record {record_id}: "
            f"{len(deleted_files)} files, {deleted_links} links"
        )
        return deleted_files, deleted_links

    async def update_context_info(
        self, record_id: int, context_info: str | None, *, actor: Actor
    ) -> Record:
        """Replace ``context_info`` on a record with an audit event.

        Args:
            record_id: Record ID.
            context_info: New markdown source (``None`` clears the field).
            actor: Who is making the change.

        Returns:
            Updated record with relations loaded.
        """
        record = await self.repo.get(record_id)
        old_value = record.context_info
        actor_id = audit_actor_id(actor)
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

    async def _check_unique_by(self, user_id: UUID, record: Record) -> None:
        """Check that assigning user_id to record does not violate unique_by.

        Thin wrapper over ``RecordRepository.ensure_unique_by`` — that method
        self-gates on ``record_type.unique_by`` (no-op when ``None``).
        ``exclude_record_id=record.id`` excludes the record itself from the
        match: at assignment time the candidate row already exists, so
        without exclusion it would always match itself.

        Args:
            user_id: User being assigned.
            record: Record with record_type eagerly loaded.

        Raises:
            RecordUniquePerUserError: If another record already exists matching
                every selected unique_by partition for this DICOM context.
        """
        await self.repo.ensure_unique_by(
            record.record_type,
            user_id=user_id,
            parent_record_id=record.parent_record_id,
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=record.series_uid,
            exclude_record_id=record.id,
        )

    async def _register_output_links(
        self,
        record: Record,
        record_read: RecordRead,
        checksums: dict[str, str],
        parent: RecordRead | None = None,
    ) -> None:
        """Create links for OUTPUT files discovered by a checksum scan.

        Propagates whatever ``_missing_output_links`` raises for an unsafe
        pattern (see its docstring) — a path violation must never be silently
        dropped. Past that point, link registration IS best-effort: it is
        bookkeeping on top of the caller's main flow (submit / check-files),
        and a DB write failure there must not fail that flow after the
        record's own data is already committed.
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

    async def sync_output_files(self, record: Record) -> None:
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

        Raises:
            CustomHTTPException: 422 if an OUTPUT pattern cannot be safely
                resolved. ``submit_data`` runs ``_validate_output_paths``
                before persisting, so this should already be unreachable in
                practice — kept as a backstop (e.g. a literal pattern that
                only the join/containment check catches, which the pure-render
                pre-check does not exercise) rather than silently degrading
                into a routine-looking warning (see ``UnsafePathError``'s
                "must never degrade into a fallback" contract).
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
        except UnsafePathError as exc:
            logger.warning(f"unsafe output path rejected for record {record.id}: {exc}")
            # No exc.value here either, for the same reason as
            # _render_output_path — but note the surfaces are NOT identical.
            # That helper is render-only, so str(exc) there names a
            # placeholder key. This one catches from Files.checksums, which
            # reaches join_within, and two of its four messages interpolate
            # the working directory — which under the unanonymized-path
            # fallback can itself be built from a raw patient id. That is the
            # `base`-in-message residual accepted change-wide (see
            # UnsafePathError's docstring), not something this line closes.
            raise UNPROCESSABLE_ENTITY.with_context(
                f"Output files cannot be safely resolved for this record: {exc}"
            ) from exc
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
