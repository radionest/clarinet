"""Create: initial status, owner rules, auto-block, one on_status flow."""

from unittest.mock import AsyncMock

import pytest
from sqlmodel import func, select

from clarinet.exceptions.domain import RecordOwnerLacksRoleError, TransitionNotAllowedError
from clarinet.models import Record
from clarinet.models import RecordStatus as S
from clarinet.models.record_event import RecordEvent
from clarinet.services.file_validation import FileValidationResult
from tests.utils.factories import make_record_type
from tests.utils.lifecycle import OTHER_ROLE, client_as, human
from tests.utils.urls import RECORDS_BASE


def _payload(lc, rt, **kw):
    return {
        "patient_id": lc.patient_id,
        "study_uid": lc.study_uid,
        "series_uid": lc.series_uid,
        "record_type_name": rt,
        **kw,
    }


def _record(lc, rt, **kw):
    return Record(
        patient_id=lc.patient_id,
        study_uid=lc.study_uid,
        series_uid=lc.series_uid,
        record_type_name=rt,
        **kw,
    )


async def _count(lc, rt):
    rows = await lc.session.execute(
        select(func.count()).select_from(Record).where(Record.record_type_name == rt)
    )
    return rows.scalar_one()


@pytest.mark.asyncio
async def test_creating_a_finished_record_is_refused(lc, test_settings):
    rt = await lc.record_type("lc-cr-fin")
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.post(
            f"{RECORDS_BASE}/", json=_payload(lc, rt, status="finished", data={"a": 1})
        )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "A new record must start as 'pending', not 'finished'."
    assert await _count(lc, rt) == 0


@pytest.mark.asyncio
async def test_creating_for_a_colleague_is_refused(lc, test_settings):
    rt = await lc.record_type("lc-cr-coll")
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.post(
            f"{RECORDS_BASE}/", json=_payload(lc, rt, user_id=str(lc.colleague.id))
        )
    assert resp.status_code == 403
    assert await _count(lc, rt) == 0


@pytest.mark.asyncio
async def test_creating_for_oneself_is_fine(lc, test_settings):
    rt = await lc.record_type("lc-cr-self")
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.post(
            f"{RECORDS_BASE}/", json=_payload(lc, rt, user_id=str(lc.owner.id))
        )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_system_creating_for_a_user_without_the_types_role_is_refused(
    lc, test_settings, service_token
):
    rt = await lc.record_type("lc-cr-norole")
    async with client_as(
        lc.service_user, lc.session, test_settings, service_token=service_token
    ) as client:
        resp = await client.post(
            f"{RECORDS_BASE}/", json=_payload(lc, rt, user_id=str(lc.outsider.id))
        )
    assert resp.status_code == 409
    assert resp.json()["code"] == "OWNER_LACKS_ROLE"
    assert "metadata" not in resp.json()  # no record exists yet
    assert await _count(lc, rt) == 0


@pytest.mark.asyncio
async def test_inherited_owner_without_the_child_types_role_is_refused(lc):
    lc.session.add(make_record_type("lc-cr-parent", role_name=OTHER_ROLE, unique_by=None))
    await lc.session.commit()
    parent = await lc.seed("lc-cr-parent", status=S.finished, user_id=lc.outsider.id)
    child = await lc.record_type("lc-cr-child", inherit_user_from_parent=True)
    with pytest.raises(RecordOwnerLacksRoleError):
        await lc.service().create_record(
            _record(lc, child, parent_record_id=parent.id), actor=lc.system
        )
    assert await _count(lc, child) == 0


@pytest.mark.asyncio
async def test_non_admin_may_not_create_preparing(lc):
    rt = await lc.record_type("lc-cr-prep403")
    with pytest.raises(TransitionNotAllowedError):
        await lc.service().create_record(_record(lc, rt, status=S.preparing), actor=human(lc.owner))


@pytest.mark.asyncio
async def test_system_creates_preparing_without_auto_block(lc, monkeypatch):
    monkeypatch.setattr(
        "clarinet.services.record_service.validate_record_files",
        AsyncMock(return_value=FileValidationResult(valid=False)),
    )
    rt = await lc.record_type("lc-cr-prep")
    record = await lc.service().create_record(_record(lc, rt, status=S.preparing), actor=lc.system)
    assert record.status == S.preparing


@pytest.mark.asyncio
async def test_missing_inputs_block_and_fire_once(lc, monkeypatch):
    monkeypatch.setattr(
        "clarinet.services.record_service.validate_record_files",
        AsyncMock(return_value=FileValidationResult(valid=False)),
    )
    rt = await lc.record_type("lc-cr-block")
    record = await lc.service().create_record(_record(lc, rt), actor=human(lc.owner))
    assert record.status == S.blocked
    assert lc.engine.calls == [("status", record.id, S.blocked)]
    rows = await lc.session.execute(select(RecordEvent).where(RecordEvent.record_id == record.id))
    (event,) = rows.scalars()
    assert (event.kind, event.to_status) == ("created", "blocked")
