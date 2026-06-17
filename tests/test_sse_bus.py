"""Unit tests for the EventBus RBAC predicate and slow-consumer handling."""

import asyncio
from uuid import uuid4

import pytest

from clarinet.services.events.bus import EventBus, SseConnection, _allow
from clarinet.services.events.models import EntityEvent, PresenceEvent, TaskProgressEvent


def _conn(*, is_admin: bool, user_id, allowed_types: set[str]) -> SseConnection:
    return SseConnection(
        user_id=user_id,
        is_admin=is_admin,
        allowed_types=allowed_types,
        queue=asyncio.Queue(maxsize=4),
    )


def test_allow_rbac_matrix():
    me = uuid4()
    other = uuid4()
    admin = _conn(is_admin=True, user_id=uuid4(), allowed_types=set())
    user = _conn(is_admin=False, user_id=me, allowed_types={"typeA"})

    own_type = EntityEvent(entity="record", action="created", id="1", record_type_name="typeA")
    other_type = EntityEvent(entity="record", action="created", id="2", record_type_name="typeB")
    owned = EntityEvent(
        entity="record", action="updated", id="3", record_type_name="typeB", user_id=me
    )
    patient = EntityEvent(entity="patient", action="created", id="P1")
    rtype = EntityEvent(entity="record_type", action="created", id="typeX")
    my_task = TaskProgressEvent(task="preload", task_id="t1", payload={}, user_id=me)
    others_task = TaskProgressEvent(task="preload", task_id="t2", payload={}, user_id=other)
    quarto = TaskProgressEvent(task="quarto_render", task_id="q1", payload={})

    # Admin gets every entity event plus unaddressed (quarto) task progress.
    for ev in (own_type, other_type, owned, patient, rtype, quarto):
        assert _allow(admin, ev) is True
    # A user-addressed task reaches only that user, even for an admin.
    assert _allow(admin, my_task) is False
    assert _allow(admin, others_task) is False

    # Regular user: own type yes, foreign type no, owned record yes.
    assert _allow(user, own_type) is True
    assert _allow(user, other_type) is False
    assert _allow(user, owned) is True
    # Patient/study/series/user events are admin-only.
    assert _allow(user, patient) is False
    # record_type goes to every authenticated user.
    assert _allow(user, rtype) is True
    # Task progress: only the addressee; quarto (user_id=None) only admins.
    assert _allow(user, my_task) is True
    assert _allow(user, others_task) is False
    assert _allow(user, quarto) is False


def test_allow_presence_admin_only():
    me = uuid4()
    admin = _conn(is_admin=True, user_id=uuid4(), allowed_types=set())
    user = _conn(is_admin=False, user_id=me, allowed_types=set())
    ev = PresenceEvent(user_id=me, online=True)
    # Admins get presence; a non-admin gets none — even about themselves.
    assert _allow(admin, ev) is True
    assert _allow(user, ev) is False


@pytest.mark.asyncio
async def test_slow_consumer_gets_sentinel():
    bus = EventBus(asyncio.get_running_loop())
    conn = _conn(is_admin=True, user_id=uuid4(), allowed_types=set())
    # Shrink the queue so the third publish overflows.
    conn.queue = asyncio.Queue(maxsize=2)
    bus.register(conn)

    for i in range(3):
        bus.publish(EntityEvent(entity="record_type", action="created", id=f"t{i}"))

    # Overflow drains the queue, enqueues the close sentinel, and drops the conn.
    assert conn.queue.get_nowait() is None
    assert conn not in bus._conns
