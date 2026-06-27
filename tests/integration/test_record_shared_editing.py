"""Shared-record editing: column default, authz bypass, ownership transfer.

``RecordType.shared_editing`` lets any role-holder edit any record of the type
(not only owner/unassigned); each real-user data write reassigns ownership to
the editor. See docs/superpowers/specs/2026-06-26-shared-record-editing-design.md.
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.models import Record, RecordStatus, RecordType, User, UserRole, UserRolesLink
from clarinet.utils.auth import get_password_hash
from tests.conftest import create_authenticated_client
from tests.utils.urls import RECORDS_BASE

ROLE_NAME = "shared-edit-role"


class TestSharedEditingColumn:
    """The additive boolean column defaults off and carries a server_default."""

    def test_defaults_false(self) -> None:
        rt = RecordType(name="shared-col-default", level="SERIES")
        assert rt.shared_editing is False

    def test_has_server_default(self) -> None:
        # Required for a safe ALTER TABLE on populated PostgreSQL.
        col = RecordType.__table__.c.shared_editing
        assert col.server_default is not None


@pytest_asyncio.fixture
async def shared_role(test_session):
    test_session.add(UserRole(name=ROLE_NAME))
    await test_session.commit()


async def _make_role_user(test_session, email: str) -> User:
    uid = uuid4()
    user = User(
        id=uid,
        email=email,
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()
    test_session.add(UserRolesLink(user_id=uid, role_name=ROLE_NAME))
    await test_session.commit()
    stmt = select(User).where(User.id == uid).options(selectinload(User.roles))
    return (await test_session.execute(stmt)).scalars().first()


@pytest_asyncio.fixture
async def owner_user(shared_role, test_session) -> User:
    """The original filler of the record."""
    return await _make_role_user(test_session, "owner@test.com")


@pytest_asyncio.fixture
async def editor_user(shared_role, test_session) -> User:
    """A different role-holder who edits the owner's record."""
    return await _make_role_user(test_session, "editor@test.com")


@pytest_asyncio.fixture
async def editor_client(editor_user, test_session, test_settings):
    async for ac in create_authenticated_client(editor_user, test_session, test_settings):
        yield ac


async def _seed_type(test_session, name: str, **kw) -> RecordType:
    rt = RecordType(name=name, level="SERIES", role_name=ROLE_NAME, **kw)
    test_session.add(rt)
    await test_session.commit()
    return rt


async def _seed_record(test_session, patient, study, series, rt, user) -> Record:
    record = Record(
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=series.series_uid,
        record_type_name=rt.name,
        status=RecordStatus.finished,
        user_id=user.id if user is not None else None,
        data={"answer": "initial"},
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)
    return record


class TestSharedEditingAuthz:
    """authorize_mutable_record_access bypasses the owner check for shared types."""

    @pytest.mark.asyncio
    async def test_non_owner_can_patch_when_shared(
        self, editor_client, owner_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(
            test_session, "shared-yes", shared_editing=True, unique_per_user=False
        )
        rec = await _seed_record(test_session, test_patient, test_study, test_series, rt, owner_user)
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "edited-by-other"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == {"answer": "edited-by-other"}

    @pytest.mark.asyncio
    async def test_non_owner_blocked_when_not_shared(
        self, editor_client, owner_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "shared-no", shared_editing=False)
        rec = await _seed_record(test_session, test_patient, test_study, test_series, rt, owner_user)
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "edited-by-other"}
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unassigned_still_editable_when_not_shared(
        self, editor_client, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(test_session, "shared-unassigned", shared_editing=False)
        rec = await _seed_record(test_session, test_patient, test_study, test_series, rt, None)
        resp = await editor_client.patch(
            f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "claimed"}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_superuser_unaffected(
        self, client, owner_user, test_session, test_patient, test_study, test_series
    ):
        rt = await _seed_type(
            test_session, "shared-superuser", shared_editing=True, unique_per_user=False
        )
        rec = await _seed_record(test_session, test_patient, test_study, test_series, rt, owner_user)
        resp = await client.patch(f"{RECORDS_BASE}/{rec.id}/data", json={"answer": "by-admin"})
        assert resp.status_code == 200
