"""Audit-enrichment, drift detection, and pipeline task-progress capture.

The record mutation commits before its ``RecordEvent`` audit row (repo writes
commit internally), so the two never share an ``after_commit``. ``RecordService``
drops a session breadcrumb (``mark_pending_audit``) right before the committing
write; the capture consumes it at that commit to publish one enriched event and
to tell apart audited mutations (no drift) from raw column writes (drift).
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from clarinet.models.base import DicomQueryLevel
from clarinet.models.pipeline_task_run import PipelineTaskRun
from clarinet.repositories.record_event_repository import RecordEventRepository
from clarinet.repositories.record_repository import RecordRepository
from clarinet.services.events.bus import set_event_bus
from clarinet.services.events.capture import (
    drain_orphan_audit_events,
    register_capture_listeners,
)
from clarinet.services.events.models import EntityEvent, TaskProgressEvent
from clarinet.services.record_service import RecordService
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    make_user,
    seed_record,
)


class RecordingBus:
    """Stand-in for EventBus that records published events instead of fanning out."""

    def __init__(self) -> None:
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)

    def publish_threadsafe(self, event) -> None:
        self.events.append(event)


@pytest.fixture
def sse_bus():
    register_capture_listeners()
    bus = RecordingBus()
    set_event_bus(bus)  # type: ignore[arg-type]
    yield bus
    set_event_bus(None)


@pytest.fixture
def sse_strict_bus(monkeypatch):
    """Bus fixture with the drift detector in strict mode.

    Drift is collected into a module list instead of logged; the fixture clears
    it on entry and asserts emptiness on teardown, so any test that does NOT
    expect drift fails loudly if a mutation slips past the audit breadcrumb. A
    test that *does* expect drift drains the list mid-test (leaving it empty).
    """
    monkeypatch.setenv("CLARINET_SSE_AUDIT_STRICT", "1")
    register_capture_listeners()
    drain_orphan_audit_events()
    bus = RecordingBus()
    set_event_bus(bus)  # type: ignore[arg-type]
    yield bus
    set_event_bus(None)
    leftover = drain_orphan_audit_events()
    assert leftover == [], f"unexpected audit drift: {[(e.entity, e.id) for e in leftover]}"


def _entity_events(bus, entity=None, action=None):
    out = []
    for ev in bus.events:
        if not isinstance(ev, EntityEvent):
            continue
        if entity is not None and ev.entity != entity:
            continue
        if action is not None and ev.action != action:
            continue
        out.append(ev)
    return out


@pytest_asyncio.fixture
async def hierarchy(test_session):
    test_session.add(make_patient("AUD_PART", "Audit Part"))
    await test_session.commit()
    test_session.add(make_study("AUD_PART", "1.2.3.800"))
    await test_session.commit()
    test_session.add(make_series("1.2.3.800", "1.2.3.800.1", 1))
    await test_session.commit()
    test_session.add(make_record_type("aud-rt", level=DicomQueryLevel.SERIES))
    await test_session.commit()
    return {
        "patient_id": "AUD_PART",
        "study_uid": "1.2.3.800",
        "series_uid": "1.2.3.800.1",
        "rt": "aud-rt",
    }


async def _seed(test_session, h, **kw):
    return await seed_record(
        test_session,
        patient_id=h["patient_id"],
        study_uid=h["study_uid"],
        series_uid=h["series_uid"],
        rt_name=h["rt"],
        **kw,
    )


def _service(test_session) -> RecordService:
    return RecordService(
        RecordRepository(test_session),
        engine=None,
        event_repo=RecordEventRepository(test_session),
    )


@pytest.mark.asyncio
async def test_audit_fallback_event_arrives(test_session, sse_bus, hierarchy):
    """A: a raw audited-column write (no RecordService) still pushes a record event."""
    rec = await _seed(test_session, hierarchy)
    sse_bus.events.clear()
    await RecordRepository(test_session).update_fields(rec.id, {"context_info": "raw"})
    updated = _entity_events(sse_bus, "record", "updated")
    assert len(updated) == 1
    assert updated[0].id == str(rec.id)


@pytest.mark.asyncio
async def test_audit_drift_detected_strict(test_session, sse_strict_bus, hierarchy):
    """B: the same raw write is flagged as drift under strict mode."""
    rec = await _seed(test_session, hierarchy)
    drain_orphan_audit_events()  # discard create-time noise (there is none)
    sse_strict_bus.events.clear()
    await RecordRepository(test_session).update_fields(rec.id, {"context_info": "raw"})

    orphans = drain_orphan_audit_events()
    assert [o.id for o in orphans] == [str(rec.id)]
    # The fallback event is still delivered so realtime is not lost.
    assert len(_entity_events(sse_strict_bus, "record", "updated")) == 1


@pytest.mark.asyncio
async def test_audit_non_audited_column_no_drift(test_session, sse_strict_bus, hierarchy):
    """A raw write to a non-audited column emits an event but is not drift."""
    rec = await _seed(test_session, hierarchy)
    drain_orphan_audit_events()
    sse_strict_bus.events.clear()
    await RecordRepository(test_session).update_fields(rec.id, {"clarinet_storage_path": "/x"})

    assert len(_entity_events(sse_strict_bus, "record", "updated")) == 1
    assert drain_orphan_audit_events() == []  # not an audited column → no drift


@pytest.mark.asyncio
async def test_audit_dedup_single_enriched_event(test_session, sse_strict_bus, hierarchy):
    """C: a RecordService mutation pushes ONE event, user_id = actor, no drift/dup."""
    actor = make_user()
    test_session.add(actor)
    await test_session.commit()
    rec = await _seed(test_session, hierarchy)
    drain_orphan_audit_events()
    sse_strict_bus.events.clear()

    await _service(test_session).update_context_info(rec.id, "ctx", actor_id=actor.id)
    await test_session.commit()

    records = _entity_events(sse_strict_bus, "record")
    assert len(records) == 1  # the bare UoW duplicate is dropped
    assert records[0].action == "updated"
    assert records[0].id == str(rec.id)
    assert records[0].user_id == actor.id  # enriched with the acting user
    assert records[0].record_type_name == hierarchy["rt"]
    # Teardown asserts no drift was recorded.


@pytest.mark.asyncio
async def test_audit_assign_enriched(test_session, sse_strict_bus, hierarchy):
    """Assigning a user through RecordService enriches user_id to the actor."""
    assignee = make_user()
    actor = make_user()
    test_session.add_all([assignee, actor])
    await test_session.commit()
    rec = await _seed(test_session, hierarchy)
    drain_orphan_audit_events()
    sse_strict_bus.events.clear()

    await _service(test_session).assign_user(rec.id, assignee.id, actor_id=actor.id)
    await test_session.commit()

    records = _entity_events(sse_strict_bus, "record", "updated")
    assert len(records) == 1
    assert records[0].user_id == actor.id  # acting user, not the assignee


@pytest.mark.asyncio
async def test_audit_bulk_status_enriched(test_session, sse_strict_bus, hierarchy):
    """Bulk status change through RecordService enriches every event, no drift."""
    from clarinet.models.base import RecordStatus

    actor = make_user()
    test_session.add(actor)
    await test_session.commit()
    rec_a = await _seed(test_session, hierarchy)
    rec_b = await _seed(test_session, hierarchy)
    drain_orphan_audit_events()
    sse_strict_bus.events.clear()

    await _service(test_session).bulk_update_status(
        [rec_a.id, rec_b.id], RecordStatus.pause, actor_id=actor.id
    )
    await test_session.commit()

    updated = _entity_events(sse_strict_bus, "record", "updated")
    assert {e.id for e in updated} == {str(rec_a.id), str(rec_b.id)}
    assert all(e.user_id == actor.id for e in updated)
    # Teardown asserts no drift was recorded for the bulk path.


@pytest.mark.asyncio
async def test_pipeline_task_progress_published(test_session, sse_bus):
    """PipelineTaskRun insert and update each push a pipeline task_progress event."""
    sse_bus.events.clear()
    run = PipelineTaskRun(
        id="task-abc-123",
        task_name="my.pipeline.task",
        queue="default",
        status="running",
        started_at=datetime.now(UTC),
    )
    test_session.add(run)
    await test_session.commit()

    started = [e for e in sse_bus.events if isinstance(e, TaskProgressEvent)]
    assert len(started) == 1
    assert started[0].task == "pipeline"
    assert started[0].task_id == "task-abc-123"
    assert started[0].user_id is None  # admins only
    assert started[0].payload["status"] == "running"
    assert started[0].payload["task_name"] == "my.pipeline.task"
    assert started[0].to_wire()  # payload must be JSON-serializable

    sse_bus.events.clear()
    run.status = "succeeded"
    run.execution_time = 1.5
    await test_session.commit()

    finished = [e for e in sse_bus.events if isinstance(e, TaskProgressEvent)]
    assert len(finished) == 1
    assert finished[0].payload["status"] == "succeeded"
    assert finished[0].payload["execution_time"] == 1.5
