"""Event payloads pushed over the SSE stream.

Events are deliberately *thin*: only the entity identity plus the minimal
fields the bus needs for RBAC routing travel the wire. The client re-fetches
the changed data through the normal REST endpoints under its own permissions,
so no maskable data leaks through the push channel.

``to_wire()`` returns the bare JSON payload only — the SSE framing
(``data: ...\\n\\n``) is added by the router when it yields the frame.
"""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class EntityEvent(BaseModel):
    """A create/update/delete of a watched entity."""

    entity: Literal["record", "patient", "study", "series", "record_type", "user"]
    action: Literal["created", "updated", "deleted"]
    id: str
    record_type_name: str | None = None  # record only
    user_id: UUID | None = None  # record only: assigned user (used for RBAC routing)

    def to_wire(self) -> str:
        payload: dict[str, Any] = {
            "type": "entity",
            "entity": self.entity,
            "action": self.action,
            "id": self.id,
        }
        if self.entity == "record":
            payload["record_type_name"] = self.record_type_name
            payload["user_id"] = str(self.user_id) if self.user_id is not None else None
        return json.dumps(payload)


class TaskProgressEvent(BaseModel):
    """Progress of a long-running task (preload, quarto render, pipeline run)."""

    task: Literal["preload", "quarto_render", "pipeline"]
    task_id: str
    payload: dict[str, Any]
    user_id: UUID | None = None  # recipient for RBAC routing; NOT serialized into the frame

    def to_wire(self) -> str:
        return json.dumps(
            {
                "type": "task_progress",
                "task": self.task,
                "task_id": self.task_id,
                "payload": self.payload,
            }
        )


class PresenceEvent(BaseModel):
    """A user coming online (session acquired) or going offline (last valid session gone).

    Admin-only on the bus: presence reveals who is logged in, which is
    admin-scoped data (it feeds the admin-only role matrix).
    """

    user_id: UUID
    online: bool

    def to_wire(self) -> str:
        return json.dumps({"type": "presence", "user_id": str(self.user_id), "online": self.online})


type Event = EntityEvent | TaskProgressEvent | PresenceEvent
