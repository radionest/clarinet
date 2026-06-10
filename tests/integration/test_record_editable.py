"""Integration tests for post-submit edit locking.

``RecordType.editable`` / ``RecordType.edit_window_days`` lock finished
records for non-superusers on every API path that can change a submitted
answer: PATCH /data, PATCH /submit, PATCH /status, PATCH /bulk/status, and
hard POST /invalidate. Superusers bypass the lock. ``RecordRead.is_editable``
exposes the verdict to the frontend.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.models import (
    Record,
    RecordStatus,
    RecordType,
    User,
    UserRole,
    UserRolesLink,
)
from clarinet.utils.auth import get_password_hash
from tests.conftest import create_authenticated_client
from tests.utils.urls import RECORDS_BASE, RECORDS_BULK_STATUS

ROLE_NAME = "editable-test-role"


@pytest_asyncio.fixture
async def editor_user(test_session):
    """Non-superuser with the test role: passes RBAC, subject to the lock."""
    test_session.add(UserRole(name=ROLE_NAME))
    user_id = uuid4()
    user = User(
        id=user_id,
        email="editor@test.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()

    test_session.add(UserRolesLink(user_id=user_id, role_name=ROLE_NAME))
    await test_session.commit()

    stmt = select(User).where(User.id == user_id).options(selectinload(User.roles))
    return (await test_session.execute(stmt)).scalars().first()


@pytest_asyncio.fixture
async def editor_client(editor_user, test_session, test_settings):
    """Authenticated client for the non-superuser editor."""
    async for ac in create_authenticated_client(editor_user, test_session, test_settings):
        yield ac


async def _seed_type(test_session, name: str, **kw) -> RecordType:
    rt = RecordType(name=name, level="SERIES", role_name=ROLE_NAME, **kw)
    test_session.add(rt)
    await test_session.commit()
    return rt


async def _seed_finished_record(
    test_session,
    patient,
    study,
    series,
    rt: RecordType,
    user: User,
    *,
    finished_days_ago: float = 0.0,
) -> Record:
    """Create a finished record and pin ``finished_at`` deterministically.

    The status event listener sets ``finished_at`` to "now" during
    construction; the explicit assignment below overrides it.
    """
    record = Record(
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=series.series_uid,
        record_type_name=rt.name,
        status=RecordStatus.finished,
        user_id=user.id,
        data={"answer": "initial"},
    )
    record.finished_at = datetime.now(UTC) - timedelta(days=finished_days_ago)
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)
    return record


class TestEditableFalse:
    """editable=False locks every mutation path for non-superusers."""

    @pytest.mark.asyncio
    async def test_patch_data_locked(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "locked-rt", editable=False)
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "changed"}
        )
        assert resp.status_code == 409
        assert "does not allow changing submitted records" in resp.text

    @pytest.mark.asyncio
    async def test_patch_submit_locked(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "locked-submit-rt", editable=False)
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/submit", json={"answer": "changed"}
        )
        assert resp.status_code == 409
        assert "does not allow changing submitted records" in resp.text

    @pytest.mark.asyncio
    async def test_status_change_off_finished_locked(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "locked-status-rt", editable=False)
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.patch(f"{RECORDS_BASE}/{rec.id}/status?record_status=pending")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_bulk_status_locked(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "locked-bulk-rt", editable=False)
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.patch(f"{RECORDS_BULK_STATUS}?new_status=pending", json=[rec.id])
        assert resp.status_code == 409
        # The error names the blocking record so multi-id calls are debuggable
        assert f"Record {rec.id}" in resp.text

    @pytest.mark.asyncio
    async def test_invalidate_hard_locked_soft_allowed(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "locked-inval-rt", editable=False)
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.post(
            f"{RECORDS_BASE}/{rec.id}/invalidate", json={"mode": "hard"}
        )
        assert resp.status_code == 409

        resp = await editor_client.post(
            f"{RECORDS_BASE}/{rec.id}/invalidate",
            json={"mode": "soft", "reason": "upstream changed"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_superuser_bypasses_lock(
        self, client, test_session, test_patient, test_study, test_series, editor_user
    ):
        rt = await _seed_type(test_session, "locked-admin-rt", editable=False)
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await client.patch(f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "fixed"})
        assert resp.status_code == 200
        assert resp.json()["data"] == {"answer": "fixed"}


class TestEditWindow:
    """edit_window_days bounds re-editing to N days after finished_at."""

    @pytest.mark.asyncio
    async def test_expired_window_locks_patch_data(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "window-expired-rt", edit_window_days=3)
        rec = await _seed_finished_record(
            test_session,
            test_patient,
            test_study,
            test_series,
            rt,
            editor_user,
            finished_days_ago=10,
        )
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "changed"}
        )
        assert resp.status_code == 409
        assert "editing window of 3 days" in resp.text

    @pytest.mark.asyncio
    async def test_active_window_allows_patch_data(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "window-active-rt", edit_window_days=30)
        rec = await _seed_finished_record(
            test_session,
            test_patient,
            test_study,
            test_series,
            rt,
            editor_user,
            finished_days_ago=10,
        )
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "changed"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == {"answer": "changed"}

    @pytest.mark.asyncio
    async def test_zero_window_locks_immediately(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "window-zero-rt", edit_window_days=0)
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "changed"}
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_expired_window_locks_status_change(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "window-status-rt", edit_window_days=1)
        rec = await _seed_finished_record(
            test_session,
            test_patient,
            test_study,
            test_series,
            rt,
            editor_user,
            finished_days_ago=2,
        )
        resp = await editor_client.patch(f"{RECORDS_BASE}/{rec.id}/status?record_status=pending")
        assert resp.status_code == 409


class TestDefaultsUnaffected:
    """Default RecordType (editable=True, no window) keeps current behavior."""

    @pytest.mark.asyncio
    async def test_patch_data_allowed(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "default-rt")
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "changed"}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_status_change_off_finished_allowed(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "default-status-rt")
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.patch(f"{RECORDS_BASE}/{rec.id}/status?record_status=pending")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"


class TestIsEditableComputed:
    """RecordRead.is_editable reflects the lock for the frontend."""

    @pytest.mark.asyncio
    async def test_false_when_type_locked(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "computed-locked-rt", editable=False)
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.get(f"{RECORDS_BASE}/{rec.id}")
        assert resp.status_code == 200
        assert resp.json()["is_editable"] is False

    @pytest.mark.asyncio
    async def test_false_when_window_expired(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "computed-window-rt", edit_window_days=3)
        rec = await _seed_finished_record(
            test_session,
            test_patient,
            test_study,
            test_series,
            rt,
            editor_user,
            finished_days_ago=10,
        )
        resp = await editor_client.get(f"{RECORDS_BASE}/{rec.id}")
        assert resp.status_code == 200
        assert resp.json()["is_editable"] is False

    @pytest.mark.asyncio
    async def test_true_by_default(
        self, editor_client, editor_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "computed-default-rt")
        rec = await _seed_finished_record(
            test_session, test_patient, test_study, test_series, rt, editor_user
        )
        resp = await editor_client.get(f"{RECORDS_BASE}/{rec.id}")
        assert resp.status_code == 200
        assert resp.json()["is_editable"] is True
