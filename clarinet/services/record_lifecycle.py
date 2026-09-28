"""Record lifecycle policy: one pure decision per record command.

``RecordService._transition`` does the I/O — snapshot, file validation, new-owner
checks, the conditional write, audit, RecordFlow; this module only answers "may
this actor run this command on a record in this state, and what does it change?".
The commands are the events of a state machine over ``RecordStatus``: the actor
rules are its guards and ``Effect`` is its output.

Refusals: ``AuthorizationError`` (403) when the actor may not run the command on
this record at all; ``TransitionNotAllowedError`` (409) when the command's
contract refuses the current status; ``RecordEditLockedError`` (409) when the
edit lock refuses a person. Contracts bind every actor. Mutation rights, the lock
and the admin-only commands bind people only — system actors (RecordFlow,
workers, cron, operator scripts) rely on transitions outside the nominal
lifecycle, and a refusal there would fail silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import UUID

from clarinet.exceptions.domain import (
    AuthorizationError,
    RecordEditLockedError,
    RecordLifecycleError,
    TransitionNotAllowedError,
)
from clarinet.models.actor import Actor, HumanActor, can_access, owner_id_for
from clarinet.models.base import RecordStatus
from clarinet.models.record import is_record_editable

if TYPE_CHECKING:
    from clarinet.models.record import Record, RecordType
    from clarinet.types import RecordCommandName

BLOCKED_TEXT = (
    "Record is blocked — prerequisites not met; see check-files or validate-files for details."
)
PREPARING_TEXT = "Record is being prepared — preparation has not finished."
FINISHED_TEXT = "Record already finished. Use PATCH to update the record data."
NOT_FINISHED_TEXT = "Record is not finished yet. Use POST to submit record data."

_SUBMIT_TARGETS = (RecordStatus.finished, RecordStatus.failed)
_CLAIMABLE = (RecordStatus.pending, RecordStatus.inwork)
_FAILABLE = (RecordStatus.pending, RecordStatus.inwork)
_RELEASABLE = (RecordStatus.pending, RecordStatus.inwork)
_PREPARING_EXIT_REFUSED = (RecordStatus.inwork, RecordStatus.finished)


# ── Commands ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Claim:
    """Take an unassigned pending/inwork record for oneself (the service account for the system)."""


@dataclass(frozen=True, slots=True)
class Assign:
    """Set the owner. Only pending moves (to inwork); every other status stays."""

    user_id: UUID


@dataclass(frozen=True, slots=True)
class Unassign:
    """Clear the owner; inwork falls back to pending. The owner may release on a releasable type."""


@dataclass(frozen=True, slots=True)
class Submit:
    """Submit data: finished, or failed via ``?status=failed``."""

    target: RecordStatus


@dataclass(frozen=True, slots=True)
class Edit:
    """Change the data of a finished record; the status stays finished."""


@dataclass(frozen=True, slots=True)
class Fail:
    """Mark a pending/inwork record failed; ``reason`` goes to ``context_info``."""

    reason: str


@dataclass(frozen=True, slots=True)
class Restart:
    """Hard invalidation: back to pending (preparing stays), data and owner kept."""

    reason: str | None = None
    source_record_id: int | None = None


@dataclass(frozen=True, slots=True)
class SetStatus:
    """Raw status change — admins and system actors only."""

    target: RecordStatus


@dataclass(frozen=True, slots=True)
class Unblock:
    """check-files: blocked → pending once the input files are valid."""


type Command = Claim | Assign | Unassign | Submit | Edit | Fail | Restart | SetStatus | Unblock


@dataclass(frozen=True, slots=True)
class Create:
    """A new record's requested initial status and owner (payload values)."""

    status: RecordStatus
    owner_id: UUID | None


# ── State and outcome ────────────────────────────────────────────────────

# Input-file verdict the service computes when ``needs_inputs`` says so.
type InputsVerdict = Literal["valid", "invalid", "undeclared"]
type Fire = Literal["status", "invalidation", "data_update"]


@dataclass(frozen=True, slots=True)
class TypeRules:
    """The record type fields the policy reads."""

    name: str
    role_name: str | None
    editable: bool
    edit_window_days: int | None
    shared_editing: bool
    releasable: bool

    @classmethod
    def of(cls, record_type: RecordType) -> TypeRules:
        return cls(
            name=record_type.name,
            role_name=record_type.role_name,
            editable=record_type.editable,
            edit_window_days=record_type.edit_window_days,
            shared_editing=record_type.shared_editing,
            releasable=record_type.releasable,
        )


@dataclass(frozen=True, slots=True)
class RecordSnapshot:
    """The record state a decision is based on — also the conditional write's expectation."""

    record_id: int
    status: RecordStatus
    user_id: UUID | None
    finished_at: datetime | None
    rules: TypeRules

    @classmethod
    def of(cls, record: Record) -> RecordSnapshot:
        """Snapshot a record loaded with its ``record_type``.

        ``RecordStatus(record.status)`` normalises: SQLModel ``table=True`` classes
        skip pydantic coercion on direct construction (``Record(status="pending", ...)``,
        common in test fixtures), so an identity-mapped ``Record`` can carry a plain
        ``str`` in ``status`` instead of the ``RecordStatus`` enum member — invisible
        everywhere else because ``RecordStatus`` is itself a ``str`` subclass, until
        something calls ``.value`` on it (every 409 message in this module does).
        ``RecordStatus(x)`` is idempotent for an already-correct enum member.
        """
        assert record.id is not None  # persisted record
        return cls(
            record_id=record.id,
            status=RecordStatus(record.status),
            user_id=record.user_id,
            finished_at=record.finished_at,
            rules=TypeRules.of(record.record_type),
        )


@dataclass(frozen=True, slots=True)
class Effect:
    """What an allowed command does.

    ``owner`` is the owner *after* the command (the snapshot's when unchanged).
    ``fire``: ``"status"`` fires ``on_status(to)`` only when the status changes;
    ``"invalidation"`` always fires (hard invalidation re-runs pending flows);
    ``"data_update"`` fires ``on_data_update``. Whether a command changes anything
    at all is the caller's call — it also knows the data and the note it writes.
    """

    to_status: RecordStatus
    owner: UUID | None
    fire: Fire = "status"


# ── Decisions ────────────────────────────────────────────────────────────


def needs_inputs(cmd: Command, snap: RecordSnapshot) -> bool:
    """Whether deciding ``cmd`` needs the input-file verdict (a filesystem check)."""
    match cmd:
        case SetStatus(target=RecordStatus.pending):
            return snap.status == RecordStatus.preparing
        case Unblock():
            return snap.status == RecordStatus.blocked
        case _:
            return False


def decide(
    cmd: Command, snap: RecordSnapshot, actor: Actor, *, inputs: InputsVerdict | None = None
) -> Effect:
    """Decide ``cmd`` for ``actor`` on a record in state ``snap``.

    ``inputs`` is ``None`` when the verdict was not evaluated (``precheck``,
    ``allowed_commands``); the outcome then assumes valid files — only a refusal
    matters there.

    Raises:
        AuthorizationError: 403 — the actor may not run this command on this record.
        TransitionNotAllowedError: 409 — the contract refuses the current status.
        RecordEditLockedError: 409 — the edit lock refuses a person.
    """
    if isinstance(actor, HumanActor):
        _authorize_person(cmd, snap, actor)
    return _apply_contract(cmd, snap, actor, inputs)


def decide_create(cmd: Create, rules: TypeRules, actor: Actor) -> None:
    """Refuse a record this actor may not create.

    Raises:
        AuthorizationError: 403 — a person without the type's role (admins
            excepted), or a non-admin naming another user as the owner.
        TransitionNotAllowedError: 409 — an initial status other than
            ``pending`` (or ``preparing`` for admins and system actors).
    """
    privileged = True
    if isinstance(actor, HumanActor):
        privileged = actor.is_admin
        if not (privileged or rules.role_name in actor.role_names):
            raise AuthorizationError("Insufficient permissions to create records of this type")
        if not privileged and cmd.owner_id not in (None, actor.user_id):
            raise AuthorizationError("Only an admin can create a record for another user")
    allowed = (
        (RecordStatus.pending, RecordStatus.preparing) if privileged else (RecordStatus.pending,)
    )
    if cmd.status not in allowed:
        names = " or ".join(f"'{s.value}'" for s in allowed)
        raise TransitionNotAllowedError(
            f"A new record must start as {names}, not '{cmd.status.value}'."
        )


# Representative argument per command for ``allowed_commands``: the policy never
# checks who an assignee is, and a raw change to pending is refused only by the
# admin-only rule.
_ANY_USER = UUID(int=0)
_PROBES: tuple[tuple[RecordCommandName, Command], ...] = (
    ("claim", Claim()),
    ("assign", Assign(user_id=_ANY_USER)),
    ("unassign", Unassign()),
    ("submit", Submit(target=RecordStatus.finished)),
    ("edit", Edit()),
    ("fail", Fail(reason="")),
    ("restart", Restart()),
    ("set_status", SetStatus(target=RecordStatus.pending)),
    ("unblock", Unblock()),
)


def allowed_commands(snap: RecordSnapshot, actor: Actor) -> list[RecordCommandName]:
    """The commands ``actor`` may run on the record now — ``RecordRead.allowed_commands``.

    Checks that need I/O — ``unique_by``, the new owner's roles, input files — are
    not reflected; the endpoint still refuses those.
    """
    allowed: list[RecordCommandName] = []
    for name, cmd in _PROBES:
        try:
            decide(cmd, snap, actor)
        except (AuthorizationError, RecordLifecycleError):
            continue  # a refusal is the answer here, not an error
        allowed.append(name)
    return allowed


def _authorize_person(cmd: Command, snap: RecordSnapshot, person: HumanActor) -> None:
    if not can_access(snap.rules.role_name, person.is_superuser, person.role_names):
        raise AuthorizationError("Insufficient permissions to access this record")
    if person.is_admin:
        return
    match cmd:
        case SetStatus():
            raise AuthorizationError("Only an admin can set a record's status directly")
        case Assign():
            raise AuthorizationError("Only an admin can assign a record to a user")
        case Unassign():
            _authorize_release(snap, person)
        case _:
            pass
    if snap.user_id not in (None, person.user_id) and not snap.rules.shared_editing:
        raise AuthorizationError("Insufficient permissions to modify this record")
    if isinstance(cmd, Edit | Restart) and not is_record_editable(
        snap.status, snap.finished_at, snap.rules
    ):
        raise _edit_locked(snap)


def _authorize_release(snap: RecordSnapshot, person: HumanActor) -> None:
    """A person unassigns only their own pending/inwork record of a ``releasable`` type."""
    if not (snap.rules.releasable and snap.user_id == person.user_id):
        raise AuthorizationError("Only an admin can unassign a record")
    if snap.status not in _RELEASABLE:
        raise TransitionNotAllowedError(
            f"Cannot release a record in '{snap.status.value}' status. Allowed: pending, inwork.",
            status=snap.status.value,
        )


def _apply_contract(
    cmd: Command, snap: RecordSnapshot, actor: Actor, inputs: InputsVerdict | None
) -> Effect:
    match cmd:
        case Claim():
            claimant = owner_id_for(actor)
            if snap.user_id not in (None, claimant):
                raise AuthorizationError("Record is assigned to another user")
            if snap.status not in _CLAIMABLE:
                raise TransitionNotAllowedError(
                    f"Cannot claim a record in '{snap.status.value}' status. "
                    f"Allowed: pending, inwork.",
                    status=snap.status.value,
                )
            return Effect(RecordStatus.inwork, claimant)
        case Assign(user_id=user_id):
            assign_to: RecordStatus = (
                RecordStatus.inwork if snap.status == RecordStatus.pending else snap.status
            )
            return Effect(assign_to, user_id)
        case Unassign():
            unassign_to: RecordStatus = (
                RecordStatus.pending if snap.status == RecordStatus.inwork else snap.status
            )
            return Effect(unassign_to, None)
        case Submit(target=target):
            _ensure_submittable(snap, target)
            owner = owner_id_for(actor) if snap.user_id is None else _shared_owner(snap, actor)
            return Effect(target, owner)
        case Edit():
            if snap.status != RecordStatus.finished:
                raise TransitionNotAllowedError(NOT_FINISHED_TEXT, status=snap.status.value)
            return Effect(RecordStatus.finished, _shared_owner(snap, actor), fire="data_update")
        case Fail():
            if snap.status not in _FAILABLE:
                raise TransitionNotAllowedError(
                    f"Cannot fail record in '{snap.status.value}' status. "
                    f"Allowed: pending, inwork.",
                    status=snap.status.value,
                )
            return Effect(RecordStatus.failed, snap.user_id)
        case Restart():
            to = snap.status if snap.status == RecordStatus.preparing else RecordStatus.pending
            return Effect(to, snap.user_id, fire="invalidation")
        case SetStatus(target=target):
            return _set_status(snap, target, inputs)
        case Unblock():
            if snap.status == RecordStatus.blocked and inputs in ("valid", None):
                return Effect(RecordStatus.pending, snap.user_id)
            return Effect(snap.status, snap.user_id)
        case _:
            assert_never(cmd)


def _ensure_submittable(snap: RecordSnapshot, target: RecordStatus) -> None:
    if target not in _SUBMIT_TARGETS:
        raise TransitionNotAllowedError(
            f"Invalid submit status '{target.value}'. Allowed: finished, failed.",
            status=snap.status.value,
        )
    match snap.status:
        case RecordStatus.blocked:
            raise TransitionNotAllowedError(BLOCKED_TEXT, status=snap.status.value)
        case RecordStatus.preparing:
            raise TransitionNotAllowedError(PREPARING_TEXT, status=snap.status.value)
        case RecordStatus.finished:
            raise TransitionNotAllowedError(FINISHED_TEXT, status=snap.status.value)
        case _:
            return


def _shared_owner(snap: RecordSnapshot, actor: Actor) -> UUID | None:
    """Shared editing: the person who changes a colleague's record becomes its owner."""
    if (
        isinstance(actor, HumanActor)
        and snap.rules.shared_editing
        and snap.user_id != actor.user_id
    ):
        return actor.user_id
    return snap.user_id


def _set_status(snap: RecordSnapshot, target: RecordStatus, inputs: InputsVerdict | None) -> Effect:
    if snap.status == RecordStatus.preparing and target in _PREPARING_EXIT_REFUSED:
        raise TransitionNotAllowedError(
            f"Record {snap.record_id} is still preparing — it must leave via "
            f"'pending' (with file re-validation) before '{target.value}'.",
            status=snap.status.value,
        )
    if snap.status == RecordStatus.preparing and target == RecordStatus.pending:
        # Linearize the waits: preparation → file wait → ready.
        to = RecordStatus.blocked if inputs == "invalid" else RecordStatus.pending
        return Effect(to, snap.user_id)
    return Effect(target, snap.user_id)


def _edit_locked(snap: RecordSnapshot) -> RecordEditLockedError:
    if not snap.rules.editable:
        return RecordEditLockedError(
            f"Record {snap.record_id}: record type '{snap.rules.name}' "
            f"does not allow changing submitted records.",
            status=snap.status.value,
        )
    return RecordEditLockedError(
        f"Record {snap.record_id}: editing window of "
        f"{snap.rules.edit_window_days} days after submission has passed.",
        status=snap.status.value,
    )
