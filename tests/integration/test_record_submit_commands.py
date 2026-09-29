"""Submit and edit: verbatim 409s with codes, owners, one audit event, the lock, fail-fast."""

from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from clarinet.exceptions.domain import AuthorizationError
from clarinet.models import RecordStatus as S
from clarinet.models.record_event import RecordEvent
from clarinet.services.record_lifecycle import BLOCKED_TEXT, FINISHED_TEXT, NOT_FINISHED_TEXT
from tests.utils.lifecycle import client_as, human
from tests.utils.urls import record_data_url


async def _fresh(lc, record_id):
    return await lc.service().repo.get_with_relations(record_id, populate_existing=True)


async def _events(lc, record_id):
    rows = await lc.session.execute(select(RecordEvent).where(RecordEvent.record_id == record_id))
    return list(rows.scalars())


@pytest.mark.asyncio
async def test_system_submit_on_finished_keeps_the_load_bearing_text(
    lc, test_settings, service_token
):
    rec = await lc.seed(await lc.record_type("lc-sub-fin"), status=S.finished)
    async with client_as(
        lc.service_user, lc.session, test_settings, service_token=service_token
    ) as client:
        resp = await client.post(record_data_url(rec.id), json={"a": 1})
    assert resp.status_code == 409
    assert resp.json() == {
        "detail": FINISHED_TEXT,
        "code": "TRANSITION_NOT_ALLOWED",
        "metadata": {"status": "finished"},
    }


@pytest.mark.asyncio
async def test_blocked_submit_refused_before_output_enforcement(lc, test_settings, monkeypatch):
    enforce = AsyncMock(return_value=[])
    monkeypatch.setattr("clarinet.api.routers.record.enforce_output_grids", enforce)
    rec = await lc.seed(await lc.record_type("lc-sub-blk"), status=S.blocked)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.post(record_data_url(rec.id), json={"a": 1})
    assert resp.status_code == 409
    assert resp.json()["detail"] == BLOCKED_TEXT
    enforce.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_submits_a_paused_record(lc):
    rec = await lc.seed(await lc.record_type("lc-sub-pause"), status=S.pause, user_id=lc.owner.id)
    updated, _ = await lc.service().submit_data(rec.id, {"a": 1}, S.finished, actor=human(lc.owner))
    assert updated.status == S.finished
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_system_submit_of_unassigned_record_is_owned_by_service_account(lc):
    rec = await lc.seed(await lc.record_type("lc-sub-sys"), status=S.pending)
    updated, _ = await lc.service().submit_data(rec.id, {"a": 1}, S.finished, actor=lc.system)
    assert updated.user_id == lc.service_user.id


@pytest.mark.asyncio
async def test_ownerless_submit_writes_one_event(lc):
    rec = await lc.seed(await lc.record_type("lc-sub-evt"), status=S.pending)
    await lc.service().submit_data(rec.id, {"score": 1}, S.finished, actor=human(lc.owner))
    (event,) = await _events(lc, rec.id)
    assert event.kind == "data_submitted"
    assert event.new_value == {"fields": ["score"], "user_id": str(lc.owner.id), "via": "submit"}
    assert (event.from_status, event.to_status) == ("pending", "finished")


@pytest.mark.asyncio
async def test_another_users_record_is_403(lc):
    rec = await lc.seed(await lc.record_type("lc-sub-403"), status=S.inwork, user_id=lc.owner.id)
    with pytest.raises(AuthorizationError):
        await lc.service().submit_data(rec.id, {"a": 1}, S.finished, actor=human(lc.colleague))
    assert (await _fresh(lc, rec.id)).status == S.inwork


@pytest.mark.asyncio
async def test_admin_role_without_type_role_is_403(lc):
    rec = await lc.seed(await lc.record_type("lc-sub-adm"), status=S.pending)
    with pytest.raises(AuthorizationError):
        await lc.service().submit_data(rec.id, {"a": 1}, S.finished, actor=human(lc.admin_only))


@pytest.mark.asyncio
async def test_shared_editing_edit_transfers_the_owner(lc):
    rt = await lc.record_type("lc-edit-shared", shared_editing=True)
    rec = await lc.seed(rt, status=S.finished, user_id=lc.owner.id)
    updated, _ = await lc.service().update_data(rec.id, {"a": 2}, actor=human(lc.colleague))
    assert (updated.user_id, updated.data) == (lc.colleague.id, {"a": 2})
    assert lc.engine.calls == [("data_update", rec.id, S.finished)]
    (event,) = await _events(lc, rec.id)
    assert event.kind == "data_updated"
    assert event.new_value == {
        "fields": ["a"],
        "user_id": str(lc.colleague.id),
        "via": "shared_update",
    }


@pytest.mark.asyncio
async def test_shared_editing_edit_of_unassigned_record_is_labelled_shared_update(lc):
    rt = await lc.record_type("lc-edit-shared-free", shared_editing=True)
    rec = await lc.seed(rt, status=S.finished)
    await lc.service().update_data(rec.id, {"a": 2}, actor=human(lc.colleague))
    (event,) = await _events(lc, rec.id)
    assert event.new_value == {
        "fields": ["a"],
        "user_id": str(lc.colleague.id),
        "via": "shared_update",
    }


@pytest.mark.asyncio
async def test_edit_of_a_pending_record_is_409(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-edit-pend"), status=S.pending, user_id=lc.owner.id)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.patch(record_data_url(rec.id), json={"a": 1})
    assert resp.status_code == 409
    assert resp.json()["detail"] == NOT_FINISHED_TEXT


@pytest.mark.asyncio
async def test_owner_editing_a_locked_record_is_409_with_the_lock_code(lc, test_settings):
    rt = await lc.record_type("lc-edit-lock-own", editable=False)
    rec = await lc.seed(rt, status=S.finished, user_id=lc.owner.id)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.patch(record_data_url(rec.id), json={"a": 1})
    assert resp.status_code == 409
    assert resp.json()["code"] == "RECORD_EDIT_LOCKED"


@pytest.mark.asyncio
async def test_admin_role_bypasses_the_edit_lock(lc, test_settings):
    rt = await lc.record_type("lc-edit-lock", editable=False)
    rec = await lc.seed(rt, status=S.finished, user_id=lc.owner.id)
    async with client_as(lc.admin, lc.session, test_settings) as client:
        resp = await client.patch(record_data_url(rec.id), json={"a": 1})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"a": 1}


@pytest.mark.asyncio
async def test_failed_resubmitted_as_failed_fires_nothing(lc):
    rec = await lc.seed(await lc.record_type("lc-sub-refail"), status=S.failed)
    updated, _ = await lc.service().submit_data(rec.id, {"error": "x"}, S.failed, actor=lc.system)
    assert updated.status == S.failed
    assert lc.engine.calls == []
