"""Integration tests for admin record status change and user unassignment."""

import pytest
import pytest_asyncio

from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    make_user,
    seed_record,
)
from tests.utils.urls import ADMIN_RECORD_STATUS, ADMIN_RECORD_USER


@pytest_asyncio.fixture
async def record_env(test_session):
    """Seed patient → study → series → record_type → user → record."""
    pat = make_patient("ADMIN_PAT", "Admin Patient")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("ADMIN_PAT", "1.2.3.900")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.900", "1.2.3.900.1", 1)
    test_session.add(series)
    await test_session.commit()

    rt = make_record_type("admin_test_rt")
    test_session.add(rt)
    await test_session.commit()

    user = make_user()
    test_session.add(user)
    await test_session.commit()
    await test_session.refresh(user)

    rec = await seed_record(
        test_session,
        patient_id="ADMIN_PAT",
        study_uid="1.2.3.900",
        series_uid="1.2.3.900.1",
        rt_name="admin_test_rt",
        user_id=user.id,
    )
    return {"record": rec, "user": user}


# ── Status change tests ─────────────────────────────────────────────


class TestAdminUpdateStatus:
    """Tests for PATCH /api/admin/records/{id}/status."""

    @pytest.mark.asyncio
    async def test_admin_update_status_success(self, client, record_env):
        record_id = record_env["record"].id
        resp = await client.patch(
            f"{ADMIN_RECORD_STATUS}/{record_id}/status",
            params={"record_status": "finished"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "finished"

    @pytest.mark.asyncio
    async def test_admin_update_status_nonexistent(self, client):
        resp = await client.patch(
            f"{ADMIN_RECORD_STATUS}/999999/status",
            params={"record_status": "finished"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_update_status_timestamps(self, client, record_env):
        record_id = record_env["record"].id

        # pending → inwork should set started_at
        resp = await client.patch(
            f"{ADMIN_RECORD_STATUS}/{record_id}/status",
            params={"record_status": "inwork"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "inwork"

        # inwork → finished should set finished_at
        resp = await client.patch(
            f"{ADMIN_RECORD_STATUS}/{record_id}/status",
            params={"record_status": "finished"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "finished"


# ── Unassign tests ───────────────────────────────────────────────────


class TestAdminUnassignUser:
    """Tests for DELETE /api/admin/records/{id}/user."""

    @pytest.mark.asyncio
    async def test_admin_unassign_user_from_inwork(self, client, record_env):
        record_id = record_env["record"].id

        # Set to inwork first
        await client.patch(
            f"{ADMIN_RECORD_STATUS}/{record_id}/status",
            params={"record_status": "inwork"},
        )

        resp = await client.delete(f"{ADMIN_RECORD_USER}/{record_id}/user")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] is None
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_admin_unassign_user_from_finished(self, client, record_env):
        record_id = record_env["record"].id

        # Set to finished first
        await client.patch(
            f"{ADMIN_RECORD_STATUS}/{record_id}/status",
            params={"record_status": "finished"},
        )

        resp = await client.delete(f"{ADMIN_RECORD_USER}/{record_id}/user")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] is None
        assert data["status"] == "finished"

    @pytest.mark.asyncio
    async def test_admin_unassign_already_null(self, client, record_env):
        record_id = record_env["record"].id

        # Unassign twice — second call should still succeed
        await client.delete(f"{ADMIN_RECORD_USER}/{record_id}/user")
        resp = await client.delete(f"{ADMIN_RECORD_USER}/{record_id}/user")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] is None

    @pytest.mark.asyncio
    async def test_admin_unassign_nonexistent(self, client):
        resp = await client.delete(f"{ADMIN_RECORD_USER}/999999/user")
        assert resp.status_code == 404
