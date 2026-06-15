"""Integration tests for the record audit trail endpoints."""

import pytest
from httpx import AsyncClient

from clarinet.models import DicomQueryLevel
from tests.utils.factories import make_record_type
from tests.utils.test_helpers import PatientFactory, RecordFactory
from tests.utils.urls import (
    ADMIN_DELETED_RECORD_EVENTS,
    ADMIN_RECORD_EVENTS,
    ADMIN_RECORDS,
    RECORDS_BASE,
    record_events_url,
)


async def _seed_record(session):
    patient = await PatientFactory.create_patient(session)
    record_type = make_record_type(level=DicomQueryLevel.PATIENT)
    session.add(record_type)
    await session.commit()
    return await RecordFactory.create_record_with_relations(
        session, patient=patient, record_type=record_type
    )


class TestRecordEventsEndpoint:
    @pytest.mark.asyncio
    async def test_status_change_is_audited_with_actor(self, client: AsyncClient, test_session):
        record = await _seed_record(test_session)

        resp = await client.patch(f"{RECORDS_BASE}/{record.id}/status?record_status=inwork")
        assert resp.status_code == 200, resp.text

        events = (await client.get(record_events_url(record.id))).json()
        status_events = [e for e in events if e["kind"] == "status_changed"]
        assert len(status_events) == 1
        event = status_events[0]
        assert event["from_status"] == "pending"
        assert event["to_status"] == "inwork"
        assert event["actor_id"] is not None  # browser user, not system
        assert event["actor_name"] is not None  # admin sees the actor email
        assert event["record_id"] == record.id
        assert event["record_key"] == record.id  # survives deletion, unlike record_id

    @pytest.mark.asyncio
    async def test_context_info_update_is_audited(self, client: AsyncClient, test_session):
        record = await _seed_record(test_session)

        resp = await client.patch(
            f"{RECORDS_BASE}/{record.id}/context-info",
            json={"context_info": "fresh notes"},
        )
        assert resp.status_code == 200, resp.text

        events = (await client.get(record_events_url(record.id))).json()
        ctx_events = [e for e in events if e["kind"] == "context_info_updated"]
        assert len(ctx_events) == 1
        assert ctx_events[0]["new_value"] == {"context_info": "fresh notes"}
        assert ctx_events[0]["old_value"] == {"context_info": None}

    @pytest.mark.asyncio
    async def test_fail_is_audited_with_reason(self, client: AsyncClient, test_session):
        record = await _seed_record(test_session)

        resp = await client.post(
            f"{RECORDS_BASE}/{record.id}/fail",
            json={"reason": "broken acquisition"},
        )
        assert resp.status_code == 200, resp.text

        events = (await client.get(record_events_url(record.id))).json()
        fail_events = [e for e in events if e["kind"] == "failed"]
        assert len(fail_events) == 1
        assert fail_events[0]["reason"] == "broken acquisition"
        assert fail_events[0]["to_status"] == "failed"

    @pytest.mark.asyncio
    async def test_events_are_oldest_first(self, client: AsyncClient, test_session):
        record = await _seed_record(test_session)

        await client.patch(f"{RECORDS_BASE}/{record.id}/status?record_status=inwork")
        await client.patch(f"{RECORDS_BASE}/{record.id}/status?record_status=pending")

        events = (await client.get(record_events_url(record.id))).json()
        kinds = [(e["from_status"], e["to_status"]) for e in events]
        assert kinds == [("pending", "inwork"), ("inwork", "pending")]

    @pytest.mark.asyncio
    async def test_unknown_record_returns_404(self, client: AsyncClient):
        resp = await client.get(record_events_url(999_999))
        assert resp.status_code == 404


class TestDeletedRecordEvents:
    @pytest.mark.asyncio
    async def test_cascade_delete_keeps_snapshot(self, client: AsyncClient, test_session):
        record = await _seed_record(test_session)

        resp = await client.delete(f"{ADMIN_RECORDS}/{record.id}")
        assert resp.status_code == 200, resp.text

        events = (await client.get(ADMIN_DELETED_RECORD_EVENTS)).json()
        snapshots = [e for e in events if e["old_value"]["record_id"] == record.id]
        assert len(snapshots) == 1
        snapshot = snapshots[0]["old_value"]
        assert snapshot["record_type_name"] == record.record_type_name
        assert snapshot["patient_id"] == record.patient_id
        assert snapshots[0]["kind"] == "deleted"


class TestGlobalRecordEvents:
    @pytest.mark.asyncio
    async def test_feed_lists_events_newest_first_with_actor(
        self, client: AsyncClient, test_session
    ):
        record = await _seed_record(test_session)
        await client.patch(f"{RECORDS_BASE}/{record.id}/status?record_status=inwork")
        await client.patch(f"{RECORDS_BASE}/{record.id}/status?record_status=pending")

        resp = await client.get(ADMIN_RECORD_EVENTS)
        assert resp.status_code == 200, resp.text
        events = resp.json()
        status_events = [e for e in events if e["kind"] == "status_changed"]
        assert len(status_events) >= 2
        # Global feed is newest-first (the per-record feed is oldest-first); ids
        # are monotonic so they double as the secondary sort key check.
        ids = [e["id"] for e in events]
        assert ids == sorted(ids, reverse=True)
        # A browser mutation resolves to the acting user's email.
        assert status_events[0]["actor_name"] is not None
        assert "@" in status_events[0]["actor_name"]

    @pytest.mark.asyncio
    async def test_feed_filters_by_kind(self, client: AsyncClient, test_session):
        record = await _seed_record(test_session)
        await client.patch(f"{RECORDS_BASE}/{record.id}/status?record_status=inwork")

        resp = await client.get(ADMIN_RECORD_EVENTS, params={"kind": "status_changed"})
        assert resp.status_code == 200, resp.text
        events = resp.json()
        assert len(events) >= 1
        assert all(e["kind"] == "status_changed" for e in events)

    @pytest.mark.asyncio
    async def test_feed_filters_by_patient(self, client: AsyncClient, test_session):
        # Two records under different patients; distinct record-type names avoid
        # the recordtype.name UNIQUE collision from seeding twice.
        patient_a = await PatientFactory.create_patient(test_session)
        patient_b = await PatientFactory.create_patient(test_session)
        rt_a = make_record_type(name="audit-rt-a", level=DicomQueryLevel.PATIENT)
        rt_b = make_record_type(name="audit-rt-b", level=DicomQueryLevel.PATIENT)
        test_session.add(rt_a)
        test_session.add(rt_b)
        await test_session.commit()
        record_a = await RecordFactory.create_record_with_relations(
            test_session, patient=patient_a, record_type=rt_a
        )
        record_b = await RecordFactory.create_record_with_relations(
            test_session, patient=patient_b, record_type=rt_b
        )
        await client.patch(f"{RECORDS_BASE}/{record_a.id}/status?record_status=inwork")
        await client.patch(f"{RECORDS_BASE}/{record_b.id}/status?record_status=inwork")

        resp = await client.get(ADMIN_RECORD_EVENTS, params={"patient_id": record_a.patient_id})
        assert resp.status_code == 200, resp.text
        record_ids = {e["record_id"] for e in resp.json()}
        assert record_a.id in record_ids
        assert record_b.id not in record_ids

    @pytest.mark.asyncio
    async def test_feed_requires_auth(self, unauthenticated_client: AsyncClient):
        resp = await unauthenticated_client.get(ADMIN_RECORD_EVENTS)
        assert resp.status_code == 401
