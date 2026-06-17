"""SQLAlchemy session listeners that turn ORM mutations into SSE events.

Listeners attach to the ``Session`` class (the sync Session that lives inside
every AsyncSession). During ``after_flush`` they collect thin
``{entity, action, id}`` events into ``session.info`` — reading **column
attributes only**, because touching a relationship here raises
``MissingGreenlet``. On ``after_commit`` the buffer is de-duplicated and
published to the bus; a full rollback discards it.

This captures every ORM unit-of-work mutation in one place regardless of who
issued it (service, repository, or router). Mutations that bypass the ORM
(Core bulk DML, DB-level cascades) are invisible here — those call
``emit_entity`` explicitly at the data-access layer (marked with a
``# sse-capture:`` comment at the call site).

Audit enrichment (phase 5). ``RecordService`` writes a ``RecordEvent`` audit
row *after* the mutation has already committed (the repo write commits
internally; the audit row commits in a later transaction), so the audit row
and the record mutation never share an ``after_commit``. To still deliver one
enriched event (``user_id`` = the acting user, not the record owner) the
service calls ``mark_pending_audit`` **before** the committing repo write — a
session-scoped breadcrumb the capture consumes at the mutation's own commit.
A record ``updated`` event that changes an audited column with **no** such
breadcrumb is a drift signal (some path mutated state outside the audited
service): logged as a warning, or collected for tests under
``CLARINET_SSE_AUDIT_STRICT=1``.

``PipelineTaskRun`` rows (written by ``AuditMiddleware`` over the HTTP API in
this same process) are observed directly as ``task_progress`` events for
admins — there is no separate audit row, so no breadcrumb is needed.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from clarinet.models.patient import Patient
from clarinet.models.pipeline_task_run import PipelineTaskRun
from clarinet.models.record import Record, RecordType
from clarinet.models.study import Series, Study
from clarinet.models.user import User
from clarinet.services.events.bus import get_event_bus
from clarinet.services.events.models import EntityEvent, PresenceEvent, TaskProgressEvent
from clarinet.utils.logger import logger

_INFO_KEY = "clarinet_sse_events"  # per-transaction list[EntityEvent]
_PIPELINE_KEY = "clarinet_sse_pipeline"  # per-transaction dict[task_id, payload]
_AUDITED_KEY = "clarinet_sse_audited_record_ids"  # per-transaction set[str]
_PENDING_AUDIT_KEY = "clarinet_sse_pending_audit"  # session-scoped dict[str, UUID|None]

_ACTION_PRIORITY = {"deleted": 3, "created": 2, "updated": 1}

# Record columns whose change RecordService is expected to audit. An ``updated``
# event touching one of these with no pending-audit breadcrumb is drift. The
# anonymization / file-checksum / timestamp columns are deliberately excluded:
# they change through paths that never write a RecordEvent and are not drift.
_AUDITED_COLUMNS = ("status", "user_id", "data", "context_info")

# Column attributes copied into a pipeline ``task_progress`` payload. Read from
# ``__dict__`` only (never a lazy load), and limited to non-datetime columns so
# the payload stays JSON-serializable for ``to_wire()``.
_PIPELINE_PAYLOAD_COLS = (
    "id",
    "task_name",
    "queue",
    "status",
    "record_id",
    "pipeline_id",
    "step_index",
    "patient_id",
    "study_uid",
    "series_uid",
    "execution_time",
    "retry_count",
    "error_type",
    "error_message",
    "error_status_code",
    "result",
)

_registered = False

# Drift events collected under strict mode (tests assert on this; the fixture
# turns strict mode on and asserts emptiness on teardown).
_orphan_audit_events: list[EntityEvent] = []


def _entity_event(obj: object, action: str) -> EntityEvent | None:
    """Build a thin event for a watched model instance (column attrs only)."""
    if isinstance(obj, Record):
        if obj.id is None:
            return None
        return EntityEvent(
            entity="record",
            action=action,
            id=str(obj.id),
            record_type_name=obj.record_type_name,
            user_id=obj.user_id,
        )
    if isinstance(obj, RecordType):
        return EntityEvent(entity="record_type", action=action, id=obj.name)
    if isinstance(obj, Patient):
        return EntityEvent(entity="patient", action=action, id=str(obj.id))
    if isinstance(obj, Series):
        return EntityEvent(entity="series", action=action, id=str(obj.series_uid))
    if isinstance(obj, Study):
        return EntityEvent(entity="study", action=action, id=str(obj.study_uid))
    if isinstance(obj, User):
        return EntityEvent(entity="user", action=action, id=str(obj.id))
    return None


def _dedup(events: list[EntityEvent]) -> list[EntityEvent]:
    """Collapse repeats of the same (entity, id); deleted > created > updated."""
    best: dict[tuple[str, str], EntityEvent] = {}
    for ev in events:
        key = (ev.entity, ev.id)
        cur = best.get(key)
        if cur is None or _ACTION_PRIORITY[ev.action] > _ACTION_PRIORITY[cur.action]:
            best[key] = ev
    return list(best.values())


def _record_audited_change(record: Record) -> bool:
    """True if this flush changed an audited Record column (column attrs only).

    History is read in ``after_flush`` where SQLAlchemy still keeps pre-flush
    attribute history; only the columns in ``_AUDITED_COLUMNS`` are inspected,
    so no relationship is touched (``MissingGreenlet``-safe).
    """
    state = sa_inspect(record)
    assert state is not None  # an ORM-mapped instance always has inspection state
    return any(getattr(state.attrs, name).history.has_changes() for name in _AUDITED_COLUMNS)


def _pipeline_payload(run: PipelineTaskRun) -> dict[str, Any]:
    """JSON-safe progress payload from a pipeline run's loaded columns.

    Reads ``__dict__`` directly so a freshly-inserted row's expired
    server-default columns (``created_at``/``updated_at``) are never accessed —
    that would trigger a lazy refresh and raise ``MissingGreenlet``.
    """
    loaded = run.__dict__
    return {name: loaded[name] for name in _PIPELINE_PAYLOAD_COLS if name in loaded}


def _buffer_pipeline(pipeline_buf: dict[str, dict[str, Any]], run: PipelineTaskRun) -> None:
    payload = _pipeline_payload(run)
    task_id = payload.get("id")
    if isinstance(task_id, str):
        pipeline_buf[task_id] = payload  # last write in the txn wins


def _audit_strict() -> bool:
    return os.environ.get("CLARINET_SSE_AUDIT_STRICT") == "1"


def _on_begin(session: Session, transaction: Any, _connection: Any) -> None:
    # Reset the per-transaction buffers only for the root transaction, not for
    # savepoints. The pending-audit breadcrumb is session-scoped (set after the
    # read that opened this transaction, consumed at the mutation's commit) and
    # is deliberately NOT reset here.
    if getattr(transaction, "nested", False):
        return
    session.info.pop(_INFO_KEY, None)
    session.info.pop(_PIPELINE_KEY, None)
    session.info.pop(_AUDITED_KEY, None)


def _on_flush(session: Session, _flush_context: Any) -> None:
    buffer: list[EntityEvent] = session.info.setdefault(_INFO_KEY, [])
    pipeline_buf: dict[str, dict[str, Any]] = session.info.setdefault(_PIPELINE_KEY, {})
    for obj in session.new:
        if isinstance(obj, PipelineTaskRun):
            _buffer_pipeline(pipeline_buf, obj)
            continue
        ev = _entity_event(obj, "created")
        if ev is not None:
            buffer.append(ev)
    for obj in session.deleted:
        ev = _entity_event(obj, "deleted")
        if ev is not None:
            buffer.append(ev)
    for obj in session.dirty:
        if not session.is_modified(obj, include_collections=False):
            continue
        if isinstance(obj, PipelineTaskRun):
            _buffer_pipeline(pipeline_buf, obj)
            continue
        ev = _entity_event(obj, "updated")
        if ev is None:
            continue
        buffer.append(ev)
        if isinstance(obj, Record) and _record_audited_change(obj):
            session.info.setdefault(_AUDITED_KEY, set()).add(ev.id)


def _check_drift(ev: EntityEvent, audited_ids: set[str]) -> None:
    """Flag a record event that changed an audited column with no audit pair.

    Only ``updated`` events qualify — creates and deletes legitimately reach
    the bus from non-audited paths (ORM cascade, Core bulk DML, study service),
    so flagging them would be noise.
    """
    if ev.action != "updated" or ev.id not in audited_ids:
        return
    if _audit_strict():
        _orphan_audit_events.append(ev)
    else:
        logger.warning(
            f"SSE audit drift: record {ev.id} changed an audited column outside "
            f"RecordService (no audit breadcrumb paired the mutation)"
        )


def _build_events(
    buffer: list[EntityEvent],
    pending: dict[str, Any],
    audited_ids: set[str],
) -> list[EntityEvent]:
    """De-duplicate buffered events and enrich record events from breadcrumbs.

    A record event with a pending-audit breadcrumb is rewritten so ``user_id``
    is the acting user (richer for RBAC than the record owner); one without is
    published as-is and run through the drift detector.
    """
    out: list[EntityEvent] = []
    for ev in _dedup(buffer):
        if ev.entity != "record":
            out.append(ev)
            continue
        if ev.id in pending:
            out.append(
                EntityEvent(
                    entity="record",
                    action=ev.action,
                    id=ev.id,
                    record_type_name=ev.record_type_name,
                    user_id=pending[ev.id],
                )
            )
        else:
            out.append(ev)
            _check_drift(ev, audited_ids)
    return out


def _on_commit(session: Session) -> None:
    # Always clear the buffers; never let a publish failure break the commit.
    buffer: list[EntityEvent] | None = session.info.pop(_INFO_KEY, None)
    pipeline_buf: dict[str, dict[str, Any]] | None = session.info.pop(_PIPELINE_KEY, None)
    audited_ids: set[str] = session.info.pop(_AUDITED_KEY, None) or set()
    pending: dict[str, Any] = session.info.pop(_PENDING_AUDIT_KEY, None) or {}
    if not buffer and not pipeline_buf:
        return
    try:
        bus = get_event_bus()
        if bus is None:
            return
        for ev in _build_events(buffer or [], pending, audited_ids):
            bus.publish(ev)
        for payload in (pipeline_buf or {}).values():
            bus.publish(
                TaskProgressEvent(
                    task="pipeline",
                    task_id=payload["id"],
                    payload=payload,
                    user_id=None,  # admins only
                )
            )
    except Exception as exc:  # the commit already succeeded; never re-raise here
        logger.warning(f"SSE capture publish failed: {exc}")


def _on_rollback(session: Session) -> None:
    session.info.pop(_INFO_KEY, None)
    session.info.pop(_PIPELINE_KEY, None)
    session.info.pop(_AUDITED_KEY, None)
    session.info.pop(_PENDING_AUDIT_KEY, None)


def register_capture_listeners() -> None:
    """Attach the session listeners once (idempotent via a module flag)."""
    global _registered
    if _registered:
        return
    event.listen(Session, "after_begin", _on_begin)
    event.listen(Session, "after_flush", _on_flush)
    event.listen(Session, "after_commit", _on_commit)
    event.listen(Session, "after_rollback", _on_rollback)
    _registered = True


def mark_pending_audit(
    session: AsyncSession | Session, record_id: int | None, actor_id: UUID | None
) -> None:
    """Announce an imminent audited record mutation to the SSE capture.

    Called by ``RecordService`` **before** a committing repo write so the
    capture can publish one enriched record event (``user_id`` = ``actor_id``)
    at that commit and skip the drift warning. The matching ``RecordEvent``
    audit row commits in a *later* transaction, so without this breadcrumb the
    two could not be correlated per-commit. No-op when no event bus is
    registered (non-SSE deployments / worker processes) or ``record_id`` is
    unset.
    """
    if record_id is None or get_event_bus() is None:
        return
    sync = session.sync_session if isinstance(session, AsyncSession) else session
    pending: dict[str, Any] = sync.info.setdefault(_PENDING_AUDIT_KEY, {})
    pending[str(record_id)] = actor_id


def drain_orphan_audit_events() -> list[EntityEvent]:
    """Test hook: return and clear drift events collected under strict mode."""
    events = list(_orphan_audit_events)
    _orphan_audit_events.clear()
    return events


def emit_entity(entity: str, action: str, ids: Iterable[str]) -> None:
    """Explicit publish for UoW-invisible mutations (Core bulk DML, DB cascade).

    No-op when no bus is registered (e.g. a TaskIQ worker process).
    """
    bus = get_event_bus()
    if bus is None:
        return
    for ident in ids:
        bus.publish(EntityEvent(entity=entity, action=action, id=ident))


def emit_record_events(events: Iterable[EntityEvent]) -> None:
    """Explicit publish of pre-built record events (Core bulk DML).

    Used for enriched cascade deletes where the caller still has the record's
    ``record_type_name``/``user_id`` snapshot, so the RBAC filter can deliver
    the delete to the owning non-admin user (a bare ``emit_entity`` carries
    neither field and would reach admins only). No-op without a bus.
    """
    bus = get_event_bus()
    if bus is None:
        return
    for ev in events:
        bus.publish(ev)


def emit_presence(user_id: UUID, online: bool) -> None:
    """Publish a user's online/offline transition.

    Sessions (``AccessToken``) are not ORM-captured here, so the session
    lifecycle layer (login / logout / revoke / cleanup) calls this explicitly,
    marked ``# sse-capture:`` at the call site. No-op without a bus.
    """
    bus = get_event_bus()
    if bus is None:
        return
    bus.publish(PresenceEvent(user_id=user_id, online=online))
