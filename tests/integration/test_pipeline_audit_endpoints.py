"""Integration tests for pipeline task run audit API endpoints."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from clarinet.models import DicomQueryLevel
from tests.utils.factories import make_record_type
from tests.utils.test_helpers import PatientFactory, RecordFactory
from tests.utils.urls import PIPELINE_RUNS, pipeline_run_url, record_runs_url


async def _seed_run(
    client: AsyncClient,
    task_id: str | None = None,
    task_name: str = "test_task",
    record_id: int | None = None,
) -> str:
    tid = task_id or str(uuid.uuid4())
    payload: dict = {
        "id": tid,
        "task_name": task_name,
        "queue": "clarinet.default",
        "started_at": datetime.now(UTC).isoformat(),
    }
    if record_id is not None:
        payload["record_id"] = record_id
    resp = await client.post(PIPELINE_RUNS, json=payload)
    assert resp.status_code == 201, resp.text
    return tid


class TestPipelineRunEndpoints:
    @pytest.mark.asyncio
    async def test_create_returns_running_row(self, client: AsyncClient):
        tid = await _seed_run(client)
        resp = await client.get(pipeline_run_url(tid))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert body["task_name"] == "test_task"
        assert body["finished_at"] is None

    @pytest.mark.asyncio
    async def test_response_timestamps_are_tz_aware(self, client: AsyncClient):
        """SQLite drops tzinfo on read-back; the response must stay RFC 3339.

        Regression for the schemathesis ``format: date-time`` failure: a naive
        ``2026-06-10T08:51:25`` violates the OpenAPI schema. All datetime fields
        — including the input ``started_at`` echoed back — must carry an offset.
        """
        tid = await _seed_run(client)
        resp = await client.patch(
            pipeline_run_url(tid),
            json={"status": "succeeded", "finished_at": datetime.now(UTC).isoformat()},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for field in ("started_at", "finished_at", "created_at", "updated_at"):
            assert datetime.fromisoformat(body[field]).tzinfo is not None, (
                f"{field}={body[field]!r} is not timezone-aware"
            )

    @pytest.mark.asyncio
    async def test_create_is_idempotent(self, client: AsyncClient):
        tid = await _seed_run(client, task_name="original")
        await _seed_run(client, task_id=tid, task_name="duplicate")
        resp = await client.get(pipeline_run_url(tid))
        assert resp.json()["task_name"] == "original"

    @pytest.mark.asyncio
    async def test_create_requires_auth(self, unauthenticated_client: AsyncClient):
        resp = await unauthenticated_client.post(
            PIPELINE_RUNS,
            json={
                "id": str(uuid.uuid4()),
                "task_name": "test_task",
                "queue": "clarinet.default",
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_patch_records_terminal_status(self, client: AsyncClient):
        tid = await _seed_run(client)
        resp = await client.patch(
            pipeline_run_url(tid),
            json={
                "status": "succeeded",
                "finished_at": datetime.now(UTC).isoformat(),
                "execution_time": 2.5,
                "result": {"score": 0.9},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["execution_time"] == pytest.approx(2.5)
        assert body["result"] == {"score": 0.9}

    @pytest.mark.asyncio
    async def test_patch_tolerates_explicit_nulls(self, client: AsyncClient):
        """JSON nulls for optional fields (retry_count etc.) must not 500/409."""
        tid = await _seed_run(client)
        resp = await client.patch(
            pipeline_run_url(tid),
            json={
                "status": "succeeded",
                "finished_at": datetime.now(UTC).isoformat(),
                "execution_time": 1.0,
                "retry_count": None,
                "error_type": None,
                "error_message": None,
                "error_status_code": None,
                "result": None,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_write_forbidden_for_non_admin(self, test_session, test_settings):
        """Regular users must not be able to forge audit rows."""
        from tests.conftest import create_authenticated_client, create_mock_superuser

        user = await create_mock_superuser(test_session, email="runs_nonadmin@test.com")
        user.is_superuser = False
        async for nonadmin in create_authenticated_client(user, test_session, test_settings):
            resp = await nonadmin.post(
                PIPELINE_RUNS,
                json={
                    "id": str(uuid.uuid4()),
                    "task_name": "forged",
                    "queue": "clarinet.default",
                    "started_at": datetime.now(UTC).isoformat(),
                },
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_patch_unknown_id_returns_404(self, client: AsyncClient):
        resp = await client.patch(
            pipeline_run_url("nonexistent"),
            json={
                "status": "failed",
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self, client: AsyncClient):
        done = await _seed_run(client)
        await client.patch(
            pipeline_run_url(done),
            json={"status": "failed", "finished_at": datetime.now(UTC).isoformat()},
        )
        running = await _seed_run(client)

        resp = await client.get(f"{PIPELINE_RUNS}?status=running")
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.json()}
        assert running in ids
        assert done not in ids

    @pytest.mark.asyncio
    async def test_list_rejects_out_of_range_record_id(self, client: AsyncClient):
        # record_id maps to a 32-bit DB column; a value above int32 max must be
        # rejected at the API boundary (422), not forwarded to the DB where
        # PostgreSQL raises NumericValueOutOfRange. 2147483648 == int32 max + 1.
        resp = await client.get(f"{PIPELINE_RUNS}?record_id=2147483648")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_unknown_returns_404(self, client: AsyncClient):
        resp = await client.get(pipeline_run_url("nonexistent"))
        assert resp.status_code == 404


class TestRecordRunsEndpoint:
    @pytest.mark.asyncio
    async def test_lists_runs_for_record(self, client: AsyncClient, test_session):
        patient = await PatientFactory.create_patient(test_session)
        record_type = make_record_type(level=DicomQueryLevel.PATIENT)
        test_session.add(record_type)
        await test_session.commit()
        record = await RecordFactory.create_record_with_relations(
            test_session, patient=patient, record_type=record_type
        )

        tid = await _seed_run(client, record_id=record.id)
        await _seed_run(client)  # unrelated run

        resp = await client.get(record_runs_url(record.id))
        assert resp.status_code == 200
        body = resp.json()
        assert [r["id"] for r in body] == [tid]
        assert body[0]["record_id"] == record.id

    @pytest.mark.asyncio
    async def test_unknown_record_returns_404(self, client: AsyncClient):
        resp = await client.get(record_runs_url(999_999))
        assert resp.status_code == 404
