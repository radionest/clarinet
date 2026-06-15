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
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from clarinet.models.patient import Patient
from clarinet.models.record import Record, RecordType
from clarinet.models.study import Series, Study
from clarinet.models.user import User
from clarinet.services.events.bus import get_event_bus
from clarinet.services.events.models import EntityEvent
from clarinet.utils.logger import logger

_INFO_KEY = "clarinet_sse_events"
_ACTION_PRIORITY = {"deleted": 3, "created": 2, "updated": 1}
_registered = False


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


def _on_begin(session: Session, transaction: Any, _connection: Any) -> None:
    # Reset the buffer only for the root transaction, not for savepoints.
    if getattr(transaction, "nested", False):
        return
    session.info.pop(_INFO_KEY, None)


def _on_flush(session: Session, _flush_context: Any) -> None:
    buffer: list[EntityEvent] = session.info.setdefault(_INFO_KEY, [])
    for obj in session.new:
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
        ev = _entity_event(obj, "updated")
        if ev is not None:
            buffer.append(ev)


def _on_commit(session: Session) -> None:
    # Always clear the buffer; never let a publish failure break the commit.
    buffer: list[EntityEvent] | None = session.info.pop(_INFO_KEY, None)
    if not buffer:
        return
    try:
        bus = get_event_bus()
        if bus is None:
            return
        for ev in _dedup(buffer):
            bus.publish(ev)
    except Exception as exc:  # the commit already succeeded; never re-raise here
        logger.warning(f"SSE capture publish failed: {exc}")


def _on_rollback(session: Session) -> None:
    session.info.pop(_INFO_KEY, None)


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


def emit_entity(entity: str, action: str, ids: Iterable[str]) -> None:
    """Explicit publish for UoW-invisible mutations (Core bulk DML, DB cascade).

    No-op when no bus is registered (e.g. a TaskIQ worker process).
    """
    bus = get_event_bus()
    if bus is None:
        return
    for ident in ids:
        bus.publish(EntityEvent(entity=entity, action=action, id=ident))
