"""Fail and restart (hard invalidation); soft invalidation stays outside the gateway."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from clarinet.models import RecordStatus as S
from clarinet.models.record_event import RecordEvent
from tests.utils.lifecycle import client_as, human
from tests.utils.urls import RECORDS_BASE


async def _fresh(lc, record_id):
    return await lc.service().repo.get_with_relations(record_id, populate_existing=True)


async def _events(lc, record_id):
    rows = await lc.session.execute(
        select(RecordEvent).where(RecordEvent.record_id == record_id).order_by(RecordEvent.id)
    )
    return list(rows.scalars())


@pytest.mark.asyncio
async def test_owner_restart_of_a_locked_record_is_409(lc, test_settings):
    rt = await lc.record_type("lc-rst-lock", editable=False)
    rec = await lc.seed(rt, status=S.finished, user_id=lc.owner.id)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.post(f"{RECORDS_BASE}/{rec.id}/invalidate", json={"mode": "hard"})
    assert resp.status_code == 409
    assert resp.json() == {
        "detail": (
            f"Record {rec.id}: record type 'lc-rst-lock' does not allow changing submitted records."
        ),
        "code": "RECORD_EDIT_LOCKED",
        "metadata": {"status": "finished"},
    }
    assert (await _fresh(lc, rec.id)).status == S.finished


@pytest.mark.asyncio
async def test_system_restart_of_a_locked_record(lc):
    rt = await lc.record_type("lc-rst-sys", editable=False)
    rec = await lc.seed(rt, status=S.finished, user_id=lc.owner.id, data={"a": 1})
    updated = await lc.service().invalidate_record(rec.id, "hard", actor=lc.system)
    assert (updated.status, updated.user_id, updated.data) == (S.pending, lc.owner.id, {"a": 1})
    assert lc.engine.calls == [("invalidation", rec.id, S.pending)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [S.pending, S.blocked])
async def test_restart_always_fires(lc, status):
    rec = await lc.seed(await lc.record_type(f"lc-rst-{status.value}"), status=status)
    await lc.service().invalidate_record(rec.id, "hard", actor=lc.system)
    await lc.service().invalidate_record(rec.id, "hard", actor=lc.system)
    assert (await _fresh(lc, rec.id)).status == S.pending
    assert lc.engine.calls == [("invalidation", rec.id, S.pending)] * 2


@pytest.mark.asyncio
async def test_restart_leaves_preparing_alone_but_notes_it(lc):
    rec = await lc.seed(await lc.record_type("lc-rst-prep"), status=S.preparing)
    updated = await lc.service().invalidate_record(rec.id, "hard", reason="stale", actor=lc.system)
    assert (updated.status, updated.context_info) == (S.preparing, "stale")


@pytest.mark.asyncio
async def test_human_restart_of_preparing_record(lc):
    """A person (owner, holding the type's role) may hard-invalidate a preparing
    record too — it stays preparing (preparation owns the exit) and still fires,
    same as the system-actor case; the preparing-exit guard is SetStatus-only."""
    rt = await lc.record_type("lc-rst-prep-human")
    rec = await lc.seed(rt, status=S.preparing, user_id=lc.owner.id)
    updated = await lc.service().invalidate_record(rec.id, "hard", actor=human(lc.owner))
    assert updated.status == S.preparing
    assert lc.engine.calls == [("invalidation", rec.id, S.preparing)]


@pytest.mark.asyncio
async def test_restart_notes(lc):
    rt = await lc.record_type("lc-rst-note")
    rec = await lc.seed(rt, status=S.finished)
    service = lc.service()
    await service.invalidate_record(rec.id, "hard", source_record_id=7, actor=lc.system)
    await service.invalidate_record(rec.id, "hard", reason="", actor=lc.system)
    assert (await _fresh(lc, rec.id)).context_info == "Invalidated by record #7"
    first, second = await _events(lc, rec.id)
    assert (first.kind, first.from_status, first.to_status) == (
        "invalidated",
        "finished",
        "pending",
    )
    assert first.new_value == {"mode": "hard", "source_record_id": 7}
    assert (second.from_status, second.to_status) == (None, None)


@pytest.mark.asyncio
async def test_soft_invalidation_changes_no_status(lc):
    rec = await lc.seed(await lc.record_type("lc-soft"), status=S.finished)
    updated = await lc.service().invalidate_record(
        rec.id, "soft", reason="upstream", actor=human(lc.owner)
    )
    assert (updated.status, updated.context_info) == (S.finished, "upstream")
    assert lc.engine.calls == []
    (event,) = await _events(lc, rec.id)
    assert (event.new_value, event.from_status) == (
        {"mode": "soft", "source_record_id": None},
        None,
    )


@pytest.mark.asyncio
async def test_submit_after_restart_restamps_finished_at(lc):
    old = datetime(2020, 1, 1, tzinfo=UTC)
    rec = await lc.seed(await lc.record_type("lc-rst-resub"), status=S.finished, finished_at=old)
    service = lc.service()
    await service.invalidate_record(rec.id, "hard", actor=lc.system)
    updated, _ = await service.submit_data(rec.id, {"a": 1}, S.finished, actor=lc.system)
    finished_at = updated.finished_at
    if finished_at.tzinfo is None:  # SQLite returns naive UTC
        finished_at = finished_at.replace(tzinfo=UTC)
    assert finished_at > old


@pytest.mark.asyncio
async def test_owner_failing_a_finished_record_is_409(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-fail-fin"), status=S.finished, user_id=lc.owner.id)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.post(f"{RECORDS_BASE}/{rec.id}/fail", json={"reason": "x"})
    assert resp.status_code == 409
    assert resp.json() == {
        "detail": "Cannot fail record in 'finished' status. Allowed: pending, inwork.",
        "code": "TRANSITION_NOT_ALLOWED",
        "metadata": {"status": "finished"},
    }
    assert (await _fresh(lc, rec.id)).status == S.finished


@pytest.mark.asyncio
async def test_fail_pending(lc):
    rec = await lc.seed(await lc.record_type("lc-fail-pend"), status=S.pending)
    updated = await lc.service().fail_record(rec.id, "broken series", actor=human(lc.owner))
    assert (updated.status, updated.context_info) == (S.failed, "Manually failed: broken series")
    (event,) = await _events(lc, rec.id)
    assert (event.kind, event.reason, event.to_status) == ("failed", "broken series", "failed")
    assert lc.engine.calls == [("status", rec.id, S.failed)]
