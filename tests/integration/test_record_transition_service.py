"""RecordService._transition: one transaction, ordering, re-evaluation, audit, SSE, precheck."""

from uuid import uuid4

import pytest
from sqlmodel import select

from clarinet.exceptions.domain import (
    AuthorizationError,
    ConcurrentTransitionError,
    RecordOwnerLacksRoleError,
    TransitionNotAllowedError,
    UserNotFoundError,
)
from clarinet.models import RecordStatus
from clarinet.models.record_event import RecordEvent
from clarinet.services.events.bus import set_event_bus
from clarinet.services.events.capture import register_capture_listeners
from clarinet.services.events.models import EntityEvent
from clarinet.services.record_lifecycle import Assign, Claim, SetStatus
from tests.utils.lifecycle import human


async def _events(session, record_id):
    rows = await session.execute(select(RecordEvent).where(RecordEvent.record_id == record_id))
    return list(rows.scalars())


async def _fresh(lc, record_id):
    return await lc.service().repo.get_with_relations(record_id, populate_existing=True)


@pytest.mark.asyncio
async def test_commits_once_then_fires_with_no_transaction_open(lc, monkeypatch):
    rt = await lc.record_type("lc-order")
    rec = await lc.seed(rt, status=RecordStatus.pending)
    order: list[str] = []
    real_commit = lc.session.commit

    async def spy_commit():
        order.append("commit")
        await real_commit()

    monkeypatch.setattr(lc.session, "commit", spy_commit)
    real_fire = lc.engine.handle_record_status_change

    async def spy_fire(record, old_status=None):
        order.append("fire")
        assert not lc.session.in_transaction()  # nothing held while flows run
        await real_fire(record, old_status)

    monkeypatch.setattr(lc.engine, "handle_record_status_change", spy_fire)
    updated, old = await lc.service()._transition(rec.id, SetStatus(RecordStatus.pause), lc.system)
    assert (updated.status, old) == (RecordStatus.pause, RecordStatus.pending)
    assert order == ["commit", "fire"]
    (event,) = await _events(lc.session, rec.id)
    assert (event.kind, event.from_status, event.to_status, event.actor_id) == (
        "status_changed",
        "pending",
        "pause",
        None,
    )


@pytest.mark.asyncio
async def test_a_failed_audit_insert_rolls_the_transition_back(lc, monkeypatch):
    rt = await lc.record_type("lc-audit-fail")
    rec = await lc.seed(rt, status=RecordStatus.pending)
    record_id = rec.id  # captured before rollback() expires rec (incl. .id)
    service = lc.service()

    async def boom(event):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(service.event_repo, "add", boom)
    with pytest.raises(RuntimeError, match="audit store down"):
        await service._transition(record_id, SetStatus(RecordStatus.pause), lc.system)
    await lc.session.rollback()  # what the request teardown does
    assert (await _fresh(lc, record_id)).status == RecordStatus.pending
    assert lc.engine.calls == []


@pytest.mark.asyncio
async def test_same_status_writes_audits_and_fires_nothing(lc):
    rt = await lc.record_type("lc-noop")
    rec = await lc.seed(rt, status=RecordStatus.inwork)
    updated, old = await lc.service()._transition(rec.id, SetStatus(RecordStatus.inwork), lc.system)
    assert updated.status == old == RecordStatus.inwork
    assert await _events(lc.session, rec.id) == []
    assert lc.engine.calls == []


@pytest.mark.asyncio
async def test_reclaiming_ones_own_record_is_a_noop(lc):
    rt = await lc.record_type("lc-reclaim-noop")
    rec = await lc.seed(rt, status=RecordStatus.inwork, user_id=lc.owner.id)
    updated, _ = await lc.service()._transition(rec.id, Claim(), human(lc.owner))
    assert (updated.status, updated.user_id) == (RecordStatus.inwork, lc.owner.id)
    assert await _events(lc.session, rec.id) == []
    assert lc.engine.calls == []


@pytest.mark.asyncio
async def test_miss_is_re_decided_on_the_new_state(lc, monkeypatch):
    """Spec: a record moves to preparing between decision and write → preparing-exit 409."""
    rt = await lc.record_type("lc-race")
    rec = await lc.seed(rt, status=RecordStatus.pending)
    service = lc.service()
    real_write = service.repo.write_transition
    calls = 0

    async def racing_write(record_id, **kw):
        nonlocal calls
        calls += 1
        if calls == 1:  # someone else moves the record first
            await real_write(
                record_id,
                expected_status=RecordStatus.pending,
                expected_user_id=None,
                to=RecordStatus.preparing,
                owner=None,
            )
        return await real_write(record_id, **kw)

    monkeypatch.setattr(service.repo, "write_transition", racing_write)
    with pytest.raises(TransitionNotAllowedError, match="still preparing"):
        await service._transition(rec.id, SetStatus(RecordStatus.finished), lc.system)


@pytest.mark.asyncio
async def test_concurrent_claim_second_claimer_refused(lc, monkeypatch):
    rt = await lc.record_type("lc-claim-race")
    rec = await lc.seed(rt, status=RecordStatus.inwork)
    service = lc.service()
    real_write = service.repo.write_transition
    calls = 0

    async def racing_write(record_id, **kw):
        nonlocal calls
        calls += 1
        if calls == 1:  # the colleague's claim lands first
            await real_write(
                record_id,
                expected_status=RecordStatus.inwork,
                expected_user_id=None,
                to=RecordStatus.inwork,
                owner=lc.colleague.id,
            )
        return await real_write(record_id, **kw)

    monkeypatch.setattr(service.repo, "write_transition", racing_write)
    with pytest.raises(AuthorizationError):
        await service._transition(rec.id, Claim(), human(lc.owner))
    assert (await _fresh(lc, rec.id)).user_id == lc.colleague.id


@pytest.mark.asyncio
async def test_gives_up_after_three_misses(lc, monkeypatch):
    rt = await lc.record_type("lc-giveup")
    rec = await lc.seed(rt, status=RecordStatus.pending)
    service = lc.service()
    attempts = 0

    async def always_miss(record_id, **kw):
        nonlocal attempts
        attempts += 1
        return False

    monkeypatch.setattr(service.repo, "write_transition", always_miss)
    with pytest.raises(ConcurrentTransitionError) as exc:
        await service._transition(rec.id, SetStatus(RecordStatus.pause), lc.system)
    assert attempts == 3
    # Preflight F8: every lifecycle 409 carries metadata.status when the record exists.
    assert exc.value.metadata() == {"status": "pending"}


@pytest.mark.asyncio
async def test_owner_only_change_is_audited_without_statuses(lc):
    rt = await lc.record_type("lc-owner-only")
    rec = await lc.seed(rt, status=RecordStatus.finished, user_id=lc.owner.id)
    await lc.service()._transition(rec.id, Assign(lc.colleague.id), human(lc.admin))
    (event,) = await _events(lc.session, rec.id)
    assert (event.kind, event.from_status, event.to_status) == ("assigned", None, None)
    assert event.new_value == {"user_id": str(lc.colleague.id)}
    assert event.actor_id == lc.admin.id
    assert lc.engine.calls == []


@pytest.mark.asyncio
async def test_new_owner_without_the_types_role_is_refused(lc):
    rt = await lc.record_type("lc-owner-role")
    rec = await lc.seed(rt, status=RecordStatus.pending)
    with pytest.raises(RecordOwnerLacksRoleError) as exc:
        await lc.service()._transition(rec.id, Assign(lc.outsider.id), lc.system)
    assert exc.value.metadata() == {"status": "pending"}
    fresh = await _fresh(lc, rec.id)
    assert (fresh.status, fresh.user_id) == (RecordStatus.pending, None)
    assert await _events(lc.session, rec.id) == []


@pytest.mark.asyncio
async def test_superuser_without_the_types_role_may_own(lc):
    rt = await lc.record_type("lc-owner-root")
    rec = await lc.seed(rt, status=RecordStatus.pending)
    updated, _ = await lc.service()._transition(rec.id, Assign(lc.superuser.id), lc.system)
    assert updated.user_id == lc.superuser.id


@pytest.mark.asyncio
async def test_unknown_new_owner_is_404(lc):
    rt = await lc.record_type("lc-owner-404")
    rec = await lc.seed(rt, status=RecordStatus.pending)
    with pytest.raises(UserNotFoundError):
        await lc.service()._transition(rec.id, Assign(uuid4()), lc.system)


@pytest.mark.asyncio
async def test_sse_gets_one_enriched_event(lc):
    register_capture_listeners()
    published: list = []

    class Bus:
        def publish(self, event):
            published.append(event)

        def publish_threadsafe(self, event):
            published.append(event)

    set_event_bus(Bus())  # type: ignore[arg-type]
    try:
        rt = await lc.record_type("lc-sse")
        rec = await lc.seed(rt, status=RecordStatus.pending)
        published.clear()
        await lc.service()._transition(rec.id, SetStatus(RecordStatus.pause), human(lc.admin))
        await lc.service()._transition(rec.id, SetStatus(RecordStatus.pause), human(lc.admin))
    finally:
        set_event_bus(None)
    records = [e for e in published if isinstance(e, EntityEvent) and e.entity == "record"]
    assert len(records) == 1  # the no-op second call emits nothing
    assert (records[0].action, records[0].id, records[0].user_id) == (
        "updated",
        str(rec.id),
        lc.admin.id,
    )


@pytest.mark.asyncio
async def test_precheck_refuses_without_writing(lc):
    rt = await lc.record_type("lc-precheck")
    rec = await lc.seed(rt, status=RecordStatus.pending, user_id=lc.owner.id)
    service = lc.service()
    record = await service.repo.get_with_relations(rec.id)
    with pytest.raises(AuthorizationError):
        service.precheck(record, SetStatus(RecordStatus.pause), human(lc.owner))
    service.precheck(record, SetStatus(RecordStatus.pause), lc.system)  # allowed, no write
    fresh = await _fresh(lc, rec.id)
    assert fresh.status == RecordStatus.pending
    assert await _events(lc.session, rec.id) == []
