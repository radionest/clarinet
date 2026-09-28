"""SetStatus and Unblock on the gateway: raw status changes, bulk, check-files."""

from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from clarinet.exceptions.domain import (
    AuthorizationError,
    ConcurrentTransitionError,
    TransitionNotAllowedError,
)
from clarinet.models import RecordStatus as S
from clarinet.models.record_event import RecordEvent
from clarinet.services.file_validation import FileValidationResult
from tests.utils.lifecycle import client_as, human
from tests.utils.urls import ADMIN_RECORDS, RECORDS_BASE, RECORDS_BULK_STATUS


def _inputs(monkeypatch, result):
    monkeypatch.setattr(
        "clarinet.services.record_service.validate_record_files", AsyncMock(return_value=result)
    )


async def _fresh(lc, record_id):
    return await lc.service().repo.get_with_relations(record_id, populate_existing=True)


async def _events(lc, record_id):
    rows = await lc.session.execute(select(RecordEvent).where(RecordEvent.record_id == record_id))
    return list(rows.scalars())


@pytest.mark.asyncio
async def test_system_lock_and_release_round_trip(lc):
    rec = await lc.seed(await lc.record_type("lc-lockrel"), status=S.pending)
    service = lc.service()
    await service.update_status(rec.id, S.blocked, actor=lc.system)
    await service.update_status(rec.id, S.pending, actor=lc.system)
    assert (await _fresh(lc, rec.id)).status == S.pending
    assert lc.engine.calls[-1] == ("status", rec.id, S.pending)


@pytest.mark.asyncio
async def test_finished_pause_finished_refires(lc):
    rec = await lc.seed(await lc.record_type("lc-refire"), status=S.finished)
    service = lc.service()
    await service.update_status(rec.id, S.pause, actor=lc.system)
    updated, _ = await service.update_status(rec.id, S.finished, actor=lc.system)
    assert lc.engine.calls[-1] == ("status", rec.id, S.finished)
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_same_status_is_a_noop(lc):
    rec = await lc.seed(await lc.record_type("lc-same"), status=S.inwork)
    updated, old = await lc.service().update_status(rec.id, S.inwork, actor=lc.system)
    assert updated.status == old == S.inwork
    assert await _events(lc, rec.id) == []
    assert lc.engine.calls == []


@pytest.mark.asyncio
async def test_preparing_cannot_jump_to_finished(lc):
    rec = await lc.seed(await lc.record_type("lc-prepjump"), status=S.preparing)
    with pytest.raises(TransitionNotAllowedError, match="still preparing"):
        await lc.service().update_status(rec.id, S.finished, actor=human(lc.admin))


@pytest.mark.asyncio
async def test_preparing_jump_over_http_carries_code_and_status(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-prepjump-http"), status=S.preparing)
    async with client_as(lc.admin, lc.session, test_settings) as client:
        resp = await client.patch(f"{ADMIN_RECORDS}/{rec.id}/status?record_status=finished")
    assert resp.status_code == 409
    body = resp.json()
    assert (body["code"], body["metadata"]) == ("TRANSITION_NOT_ALLOWED", {"status": "preparing"})
    assert "still preparing" in body["detail"]


@pytest.mark.asyncio
async def test_preparing_exit_with_invalid_inputs_blocks(lc, monkeypatch):
    rec = await lc.seed(await lc.record_type("lc-prepblock"), status=S.preparing)
    _inputs(monkeypatch, FileValidationResult(valid=False))
    updated, _ = await lc.service().update_status(rec.id, S.pending, actor=lc.system)
    assert updated.status == S.blocked


@pytest.mark.asyncio
async def test_preparing_exit_with_valid_inputs_links_them_in_the_same_transaction(lc, monkeypatch):
    rec = await lc.seed(await lc.record_type("lc-preplink"), status=S.preparing)
    _inputs(monkeypatch, FileValidationResult(valid=True, matched_files={"ct": "ct.nrrd"}))
    service = lc.service()
    set_files = AsyncMock()
    monkeypatch.setattr(service.repo, "set_files", set_files)
    updated, _ = await service.update_status(rec.id, S.pending, actor=lc.system)
    assert updated.status == S.pending
    assert set_files.await_args.args[1] == {"ct": "ct.nrrd"}
    assert set_files.await_args.kwargs == {"commit": False}


@pytest.mark.asyncio
async def test_service_call_refuses_a_non_admin(lc):
    """Spec: endpoint wiring cannot bypass the policy."""
    rec = await lc.seed(await lc.record_type("lc-direct"), status=S.pending, user_id=lc.owner.id)
    with pytest.raises(AuthorizationError):
        await lc.service().update_status(rec.id, S.finished, actor=human(lc.owner))
    assert (await _fresh(lc, rec.id)).status == S.pending


@pytest.mark.asyncio
async def test_owner_gets_403(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-http-own"), status=S.pending, user_id=lc.owner.id)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.patch(f"{RECORDS_BASE}/{rec.id}/status?record_status=finished")
    assert resp.status_code == 403
    assert (await _fresh(lc, rec.id)).status == S.pending


@pytest.mark.asyncio
async def test_superuser_without_admin_role_may_set_status(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-http-root"), status=S.pending)
    async with client_as(lc.superuser, lc.session, test_settings) as client:
        resp = await client.patch(f"{RECORDS_BASE}/{rec.id}/status?record_status=pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pause"
    (event,) = await _events(lc, rec.id)
    assert event.actor_id == lc.superuser.id


@pytest.mark.asyncio
async def test_admin_endpoint_response_reflects_the_write(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-http-adm"), status=S.inwork)
    async with client_as(lc.admin, lc.session, test_settings) as client:
        resp = await client.patch(f"{ADMIN_RECORDS}/{rec.id}/status?record_status=finished")
    assert resp.status_code == 200
    assert resp.json()["status"] == "finished"
    assert resp.json()["finished_at"] is not None


@pytest.mark.asyncio
async def test_admin_role_without_type_role_gets_403(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-http-admonly"), status=S.pending)
    async with client_as(lc.admin_only, lc.session, test_settings) as client:
        resp = await client.patch(f"{ADMIN_RECORDS}/{rec.id}/status?record_status=pause")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_service_token_is_a_system_actor(lc, test_settings, service_token):
    rec = await lc.seed(await lc.record_type("lc-http-sys"), status=S.pending)
    async with client_as(
        lc.service_user, lc.session, test_settings, service_token=service_token
    ) as client:
        first = await client.patch(f"{RECORDS_BASE}/{rec.id}/status?record_status=inwork")
        again = await client.patch(f"{RECORDS_BASE}/{rec.id}/status?record_status=inwork")
    assert (first.status_code, again.status_code) == (200, 200)
    (event,) = await _events(lc, rec.id)  # the repeat wrote no second event
    assert event.actor_id is None


class TestBulk:
    @pytest.mark.asyncio
    async def test_one_refusal_changes_nothing(self, lc):
        """A decide-phase refusal (raised before any write) leaves everything untouched."""
        rt = await lc.record_type("lc-bulk-refuse")
        ok = await lc.seed(rt, status=S.pending)
        preparing = await lc.seed(rt, status=S.preparing)
        with pytest.raises(TransitionNotAllowedError):
            await lc.service().bulk_update_status(
                [ok.id, preparing.id], S.finished, actor=lc.system
            )
        assert (await _fresh(lc, ok.id)).status == S.pending
        assert await _events(lc, ok.id) == []
        assert lc.engine.calls == []

    @pytest.mark.asyncio
    async def test_write_miss_rolls_back_everything(self, lc, monkeypatch):
        """F7: the first write miss rolls back the whole batch — no retry, all-or-nothing.

        Unlike ``test_one_refusal_changes_nothing`` (a decide-phase refusal, raised
        before any write), this forces a miss inside ``_write_one`` itself, after an
        earlier record in the same batch already wrote (and its audit event flushed).
        The rollback must undo that earlier write too — nothing committed, nothing
        emitted or fired for either record.
        """
        rt = await lc.record_type("lc-bulk-miss")
        first_id = (await lc.seed(rt, status=S.pending)).id
        second_id = (await lc.seed(rt, status=S.pending)).id
        assert first_id < second_id  # bulk processes ascending ids — real write, then the miss
        service = lc.service()
        real_write = service.repo.write_transition

        async def spy(record_id, **kw):
            if record_id == second_id:
                return False
            return await real_write(record_id, **kw)

        monkeypatch.setattr(service.repo, "write_transition", spy)
        with pytest.raises(ConcurrentTransitionError) as exc_info:
            await service.bulk_update_status([first_id, second_id], S.pause, actor=lc.system)
        assert exc_info.value.status == "pending"
        # session.rollback() expires every ORM object in it — re-fetch by the
        # ids captured above, never through the now-expired `first`/`second`.
        assert (await _fresh(lc, first_id)).status == S.pending
        assert (await _fresh(lc, second_id)).status == S.pending
        assert await _events(lc, first_id) == []
        assert await _events(lc, second_id) == []
        assert lc.engine.calls == []

    @pytest.mark.asyncio
    async def test_non_admin_is_refused(self, lc):
        rec = await lc.seed(await lc.record_type("lc-bulk-own"), user_id=lc.owner.id)
        with pytest.raises(AuthorizationError):
            await lc.service().bulk_update_status([rec.id], S.pause, actor=human(lc.owner))

    @pytest.mark.asyncio
    async def test_repeated_and_unknown_ids(self, lc):
        rec = await lc.seed(await lc.record_type("lc-bulk-dupe"), status=S.pending)
        await lc.service().bulk_update_status([rec.id, rec.id, 999_999], S.pause, actor=lc.system)
        assert (await _fresh(lc, rec.id)).status == S.pause
        (event,) = await _events(lc, rec.id)
        assert (event.kind, event.new_value) == ("status_changed", {"via": "bulk"})
        assert lc.engine.calls == [("status", rec.id, S.pause)]

    @pytest.mark.asyncio
    async def test_writes_in_ascending_id_order(self, lc, monkeypatch):
        """One lock order for every bulk request — two of them cannot deadlock."""
        rt = await lc.record_type("lc-bulk-order")
        first = await lc.seed(rt, status=S.pending)
        second = await lc.seed(rt, status=S.pending)
        service = lc.service()
        real_write = service.repo.write_transition
        written: list[int] = []

        async def spy(record_id, **kw):
            written.append(record_id)
            return await real_write(record_id, **kw)

        monkeypatch.setattr(service.repo, "write_transition", spy)
        await service.bulk_update_status([second.id, first.id], S.pause, actor=lc.system)
        assert written == sorted([first.id, second.id])

    @pytest.mark.asyncio
    async def test_owner_gets_403_over_http(self, lc, test_settings):
        rec = await lc.seed(await lc.record_type("lc-bulk-http"), user_id=lc.owner.id)
        async with client_as(lc.owner, lc.session, test_settings) as client:
            resp = await client.patch(f"{RECORDS_BULK_STATUS}?new_status=pause", json=[rec.id])
        assert resp.status_code == 403


class TestUnblock:
    @pytest.mark.asyncio
    async def test_inputs_appear(self, lc, monkeypatch):
        rec = await lc.seed(await lc.record_type("lc-unblock"), status=S.blocked)
        _inputs(monkeypatch, FileValidationResult(valid=True))
        await lc.service().check_files(rec.id, actor=human(lc.owner))
        assert (await _fresh(lc, rec.id)).status == S.pending
        assert lc.engine.calls[0] == ("status", rec.id, S.pending)

    @pytest.mark.asyncio
    async def test_no_declared_inputs_stays_blocked(self, lc, monkeypatch):
        """A system lock (blocked via set-status) must not be released by check-files."""
        rec = await lc.seed(await lc.record_type("lc-unblock-no"), status=S.blocked)
        _inputs(monkeypatch, None)
        assert await lc.service().check_files(rec.id, actor=lc.system) == ([], {})
        assert (await _fresh(lc, rec.id)).status == S.blocked

    @pytest.mark.asyncio
    async def test_person_without_rights_is_refused(self, lc):
        rec = await lc.seed(
            await lc.record_type("lc-unblock-403"), status=S.blocked, user_id=lc.owner.id
        )
        with pytest.raises(AuthorizationError):
            await lc.service().check_files(rec.id, actor=human(lc.colleague))

    @pytest.mark.asyncio
    async def test_preparing_is_left_alone(self, lc):
        rec = await lc.seed(await lc.record_type("lc-unblock-prep"), status=S.preparing)
        assert await lc.service().check_files(rec.id, actor=lc.system) == ([], {})
        assert await _events(lc, rec.id) == []
