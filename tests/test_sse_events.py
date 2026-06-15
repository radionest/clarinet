"""Capture-listener tests: ORM mutations become SSE events via a RecordingBus.

The session listeners are process-global once registered; they no-op while the
bus is None, so the fixture sets a ``RecordingBus`` for the duration of a test
and clears it on teardown.
"""

import pytest
import pytest_asyncio

from clarinet.models.base import DicomQueryLevel
from clarinet.repositories.patient_repository import PatientRepository
from clarinet.repositories.record_repository import RecordRepository
from clarinet.services.events.bus import set_event_bus
from clarinet.services.events.capture import register_capture_listeners
from clarinet.services.events.models import EntityEvent
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
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
    """Persist patient/study/series/record_type (no records yet)."""
    test_session.add(make_patient("SSE_PAT", "SSE Patient"))
    await test_session.commit()
    test_session.add(make_study("SSE_PAT", "1.2.3.700"))
    await test_session.commit()
    test_session.add(make_series("1.2.3.700", "1.2.3.700.1", 1))
    await test_session.commit()
    test_session.add(make_record_type("sse-rt", level=DicomQueryLevel.SERIES))
    await test_session.commit()
    return {
        "patient_id": "SSE_PAT",
        "study_uid": "1.2.3.700",
        "series_uid": "1.2.3.700.1",
        "rt": "sse-rt",
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


@pytest.mark.asyncio
async def test_record_insert_emits_created(test_session, sse_bus, hierarchy):
    sse_bus.events.clear()
    rec = await _seed(test_session, hierarchy)
    created = _entity_events(sse_bus, "record", "created")
    assert len(created) == 1
    ev = created[0]
    assert ev.id == str(rec.id)
    assert ev.record_type_name == "sse-rt"
    assert ev.user_id is None


@pytest.mark.asyncio
async def test_record_update_emits_updated(test_session, sse_bus, hierarchy):
    rec = await _seed(test_session, hierarchy)
    sse_bus.events.clear()
    await RecordRepository(test_session).update_fields(rec.id, {"context_info": "x"})
    updated = _entity_events(sse_bus, "record", "updated")
    assert len(updated) == 1
    assert updated[0].id == str(rec.id)


@pytest.mark.asyncio
async def test_rollback_emits_nothing(test_session, sse_bus, hierarchy):
    from clarinet.models import Record

    sse_bus.events.clear()
    rec = Record(
        patient_id=hierarchy["patient_id"],
        study_uid=hierarchy["study_uid"],
        series_uid=hierarchy["series_uid"],
        record_type_name=hierarchy["rt"],
    )
    test_session.add(rec)
    await test_session.flush()  # populates the capture buffer
    await test_session.rollback()  # full rollback discards it
    assert sse_bus.events == []


@pytest.mark.asyncio
async def test_dedup_created_then_updated_single_event(test_session, sse_bus, hierarchy):
    from clarinet.models import Record

    sse_bus.events.clear()
    rec = Record(
        patient_id=hierarchy["patient_id"],
        study_uid=hierarchy["study_uid"],
        series_uid=hierarchy["series_uid"],
        record_type_name=hierarchy["rt"],
    )
    test_session.add(rec)
    await test_session.flush()  # created buffered
    rec.context_info = "y"
    await test_session.flush()  # updated buffered
    await test_session.commit()
    created = _entity_events(sse_bus, "record", "created")
    updated = _entity_events(sse_bus, "record", "updated")
    assert len(created) == 1  # create wins over the later update
    assert updated == []


@pytest.mark.asyncio
async def test_savepoint_retry_emits_single_patient_created(test_session, sse_bus):
    from clarinet.models.patient import Patient

    repo = PatientRepository(test_session)
    # auto_id=None drives the begin_nested() retry path. make_patient always
    # pre-assigns auto_id, so construct Patient directly here.
    first = await repo.create(Patient(id="SP_A", name="A"))
    await test_session.commit()
    # Occupy the very next auto_id with a direct insert so the next
    # PatientRepository.create() collides once and retries in a savepoint.
    occupied = make_patient("SP_B", "B", auto_id=first.auto_id + 1)
    test_session.add(occupied)
    await test_session.commit()

    sse_bus.events.clear()
    created_patient = await repo.create(Patient(id="SP_C", name="C"))
    await test_session.commit()

    events = _entity_events(sse_bus, "patient", "created")
    assert len(events) == 1
    assert events[0].id == "SP_C"
    assert created_patient.auto_id == first.auto_id + 2  # skipped the occupied id


@pytest.mark.asyncio
async def test_delete_records_bulk_emits_deleted(test_session, sse_bus, hierarchy):
    rec_a = await _seed(test_session, hierarchy)
    rec_b = await _seed(test_session, hierarchy)
    sse_bus.events.clear()
    await RecordRepository(test_session).delete_records([rec_a.id, rec_b.id])
    deleted = _entity_events(sse_bus, "record", "deleted")
    assert {ev.id for ev in deleted} == {str(rec_a.id), str(rec_b.id)}


@pytest.mark.asyncio
async def test_delete_patient_emits_child_deleted(test_session, sse_bus, hierarchy):
    """ORM cascade_delete loads children into session.deleted, so the UoW
    listener captures them without an explicit emit (fork in plan 2.7.3)."""
    await _seed(test_session, hierarchy)
    sse_bus.events.clear()
    repo = PatientRepository(test_session)
    patient = await repo.get(hierarchy["patient_id"])
    await repo.delete(patient)  # commits internally
    deleted_entities = {ev.entity for ev in _entity_events(sse_bus, action="deleted")}
    assert {"patient", "study", "series", "record"} <= deleted_entities
