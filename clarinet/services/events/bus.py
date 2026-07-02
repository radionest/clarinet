"""In-process event bus that fans events out to open SSE connections.

The whole API runs in a single uvicorn process (``cli/main.py`` calls
``uvicorn.run`` without ``workers=``), so an in-memory bus reaches every
connected client. A multi-worker deployment would need an external fan-out
(e.g. RabbitMQ) — that is out of scope here.

Note on connection limits: HTTP/1.1 caps a browser at ~6 connections per host
and each open SSE stream holds a slot for its lifetime; serve the app over
HTTP/2 (nginx) to lift that cap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from clarinet.services.events.models import Event, PresenceEvent, TaskProgressEvent
from clarinet.utils.logger import logger


@dataclass(eq=False)
class SseConnection:
    """One open SSE stream and the RBAC context used to filter its events.

    ``eq=False`` keeps identity-based equality and hashing so connections can
    live in a ``set`` while their fields (``allowed_types``) stay mutable.
    """

    user_id: UUID
    is_admin: bool  # is_superuser OR membership in the built-in "admin" role
    allowed_types: set[str]  # RecordType.name visible by the user's roles
    queue: asyncio.Queue[str | None]  # wire-JSON frames; None = sentinel "close the stream"


def _allow(conn: SseConnection, event: Event) -> bool:
    """Return True if this connection is permitted to receive ``event``."""
    if isinstance(event, TaskProgressEvent):
        if event.user_id is not None:
            return conn.user_id == event.user_id
        return conn.is_admin  # quarto renders: admins only
    if isinstance(event, PresenceEvent):
        return conn.is_admin  # presence is admin-only data
    # EntityEvent
    if conn.is_admin:
        return True
    if event.entity in {"patient", "study", "series", "user"}:
        return False  # admin-only (admins already returned True above)
    if event.entity == "record_type":
        return True  # any authenticated user
    # entity == "record"
    rtn = event.record_type_name
    if rtn is not None and rtn in conn.allowed_types:
        return True
    return event.user_id is not None and event.user_id == conn.user_id


class EventBus:
    """Holds the set of live connections and pushes events to allowed ones."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._conns: set[SseConnection] = set()

    def register(self, conn: SseConnection) -> None:
        self._conns.add(conn)

    def unregister(self, conn: SseConnection) -> None:
        self._conns.discard(conn)

    def publish(self, event: Event) -> None:
        """Fan out an event to every allowed connection. Call from the event loop."""
        frame = event.to_wire()  # serialize once
        for conn in list(self._conns):
            if not _allow(conn, event):
                continue
            try:
                conn.queue.put_nowait(frame)
            except asyncio.QueueFull:
                # Slow consumer: drain, signal close, and drop the connection.
                _drain(conn.queue)
                conn.queue.put_nowait(None)
                self._conns.discard(conn)
                logger.warning(f"SSE slow consumer dropped (overflow): user {conn.user_id}")

    def publish_threadsafe(self, event: Event) -> None:
        """Schedule ``publish`` on the event loop from a non-loop thread."""
        self._loop.call_soon_threadsafe(self.publish, event)

    def shutdown(self) -> None:
        """Signal every connection to close and forget them (re-creatable)."""
        for conn in list(self._conns):
            try:
                conn.queue.put_nowait(None)
            except asyncio.QueueFull:
                _drain(conn.queue)
                conn.queue.put_nowait(None)
        self._conns.clear()


def _drain(queue: asyncio.Queue[str | None]) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break


# Module-level handle. In a worker process the bus is never set, so every
# accessor returns None and all publish paths become no-ops.
_current_bus: EventBus | None = None


def set_event_bus(bus: EventBus | None) -> None:
    global _current_bus
    _current_bus = bus


def get_event_bus() -> EventBus | None:
    return _current_bus
