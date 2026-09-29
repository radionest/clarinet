"""Claim, assign, unassign and release: owner changes and the #629 paths."""

from uuid import uuid4

import pytest
from sqlmodel import select

from clarinet.exceptions.domain import (
    AuthorizationError,
    ConcurrentTransitionError,
    TransitionNotAllowedError,
)
from clarinet.models import RecordStatus as S
from clarinet.models.record_event import RecordEvent
from clarinet.repositories.record_repository import RecordSearchCriteria
from tests.utils.lifecycle import client_as, human
from tests.utils.urls import ADMIN_RECORDS, RECORDS_BASE


async def _fresh(lc, record_id):
    return await lc.service().repo.get_with_relations(record_id, populate_existing=True)


async def _events(lc, record_id):
    rows = await lc.session.execute(select(RecordEvent).where(RecordEvent.record_id == record_id))
    return list(rows.scalars())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [S.finished, S.blocked, S.preparing, S.failed, S.pause])
async def test_assign_changes_the_owner_only(lc, status):
    rec = await lc.seed(
        await lc.record_type(f"lc-own-{status.value}"), status=status, user_id=lc.owner.id
    )
    updated, _ = await lc.service().assign_user(rec.id, lc.colleague.id, actor=human(lc.admin))
    assert (updated.status, updated.user_id) == (status, lc.colleague.id)
    assert lc.engine.calls == []


@pytest.mark.asyncio
async def test_assign_pending_moves_to_inwork(lc):
    rec = await lc.seed(await lc.record_type("lc-assign-pend"), status=S.pending)
    updated, _ = await lc.service().assign_user(rec.id, lc.owner.id, actor=human(lc.admin))
    assert updated.status == S.inwork
    assert updated.started_at is not None
    assert lc.engine.calls == [("status", rec.id, S.inwork)]


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["admin", "superuser"])
@pytest.mark.parametrize("path", ["user", "assign"])
async def test_admin_reassigning_a_finished_record_keeps_it_finished(lc, test_settings, who, path):
    """#629 on every admin path."""
    rec = await lc.seed(
        await lc.record_type(f"lc-629-{who[:4]}-{path}"), status=S.finished, user_id=lc.owner.id
    )
    url = f"{RECORDS_BASE}/{rec.id}/user" if path == "user" else f"{ADMIN_RECORDS}/{rec.id}/assign"
    async with client_as(getattr(lc, who), lc.session, test_settings) as client:
        resp = await client.patch(url, params={"user_id": str(lc.colleague.id)})
    assert resp.status_code == 200
    assert (resp.json()["status"], resp.json()["user_id"]) == ("finished", str(lc.colleague.id))


@pytest.mark.asyncio
async def test_non_admin_claiming_a_finished_record_gets_409(lc, test_settings):
    """#629, non-admin path: the claim contract allows pending/inwork only."""
    rec = await lc.seed(await lc.record_type("lc-629-claim"), status=S.finished)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.patch(
            f"{RECORDS_BASE}/{rec.id}/user", params={"user_id": str(lc.owner.id)}
        )
    assert resp.status_code == 409
    fresh = await _fresh(lc, rec.id)
    assert (fresh.status, fresh.user_id) == (S.finished, None)


@pytest.mark.asyncio
async def test_non_admin_assigning_another_user_gets_403(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-assign-403"), status=S.pending)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.patch(
            f"{RECORDS_BASE}/{rec.id}/user", params={"user_id": str(lc.colleague.id)}
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reclaiming_ones_own_record_is_a_silent_200(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-reclaim"), status=S.inwork, user_id=lc.owner.id)
    async with client_as(lc.owner, lc.session, test_settings) as client:
        resp = await client.patch(
            f"{RECORDS_BASE}/{rec.id}/user", params={"user_id": str(lc.owner.id)}
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "inwork"
    assert await _events(lc, rec.id) == []


@pytest.mark.asyncio
async def test_assigning_an_unknown_user_is_404(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-assign-404"), status=S.pending)
    async with client_as(lc.admin, lc.session, test_settings) as client:
        resp = await client.patch(
            f"{ADMIN_RECORDS}/{rec.id}/assign", params={"user_id": str(uuid4())}
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assignee", ["outsider", "admin_only"]
)  # the admin role alone is not enough
async def test_assigning_a_user_without_the_types_role_is_409(lc, test_settings, assignee):
    rt = await lc.record_type(f"lc-assign-norole-{assignee.replace('_', '-')}")  # slug names
    rec = await lc.seed(rt, status=S.pending)
    async with client_as(lc.admin, lc.session, test_settings) as client:
        resp = await client.patch(
            f"{ADMIN_RECORDS}/{rec.id}/assign", params={"user_id": str(getattr(lc, assignee).id)}
        )
    assert resp.status_code == 409
    assert (resp.json()["code"], resp.json()["metadata"]) == (
        "OWNER_LACKS_ROLE",
        {"status": "pending"},
    )
    assert (await _fresh(lc, rec.id)).user_id is None


@pytest.mark.asyncio
async def test_assigning_a_superuser_without_the_types_role_is_ok(lc, test_settings):
    rec = await lc.seed(await lc.record_type("lc-assign-root"), status=S.pending)
    async with client_as(lc.admin, lc.session, test_settings) as client:
        resp = await client.patch(
            f"{ADMIN_RECORDS}/{rec.id}/assign", params={"user_id": str(lc.superuser.id)}
        )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == str(lc.superuser.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["assign", "unassign"])
async def test_admin_role_without_type_role_gets_403(lc, test_settings, method):
    rec = await lc.seed(
        await lc.record_type(f"lc-admonly-{method}"), status=S.inwork, user_id=lc.owner.id
    )
    async with client_as(lc.admin_only, lc.session, test_settings) as client:
        if method == "assign":
            resp = await client.patch(
                f"{ADMIN_RECORDS}/{rec.id}/assign", params={"user_id": str(lc.colleague.id)}
            )
        else:
            resp = await client.delete(f"{ADMIN_RECORDS}/{rec.id}/user")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_claim_pending(lc):
    rec = await lc.seed(await lc.record_type("lc-claim"), status=S.pending)
    claimed = await lc.service().claim_record(rec.id, actor=human(lc.owner))
    assert (claimed.status, claimed.user_id) == (S.inwork, lc.owner.id)
    assert claimed.started_at is not None
    assert lc.engine.calls == [("status", rec.id, S.inwork)]
    (event,) = await _events(lc, rec.id)
    assert (event.kind, event.from_status, event.to_status) == ("assigned", "pending", "inwork")
    assert event.new_value == {"user_id": str(lc.owner.id), "via": "claim"}


@pytest.mark.asyncio
async def test_claim_someone_elses_record_is_403(lc):
    rec = await lc.seed(
        await lc.record_type("lc-claim-403"), status=S.pending, user_id=lc.colleague.id
    )
    with pytest.raises(AuthorizationError):
        await lc.service().claim_record(rec.id, actor=human(lc.owner))


@pytest.mark.asyncio
async def test_claim_blocked_is_409(lc):
    rec = await lc.seed(await lc.record_type("lc-claim-409"), status=S.blocked)
    with pytest.raises(TransitionNotAllowedError):
        await lc.service().claim_record(rec.id, actor=human(lc.owner))


class TestClaimNext:
    @pytest.mark.asyncio
    async def test_a_lost_race_picks_another_record(self, lc, monkeypatch):
        rt = await lc.record_type("lc-pool-race")
        taken = await lc.seed(rt, status=S.pending)
        free = await lc.seed(rt, status=S.pending)
        service = lc.service()
        real_find = service.repo.find_random
        seen: list[set[int]] = []

        async def racing_find(criteria, **kw):
            seen.append(set(criteria.exclude_ids))
            if len(seen) == 1:  # both claimers picked `taken`; the colleague wins it
                await service.repo.write_transition(
                    taken.id,
                    expected_status=S.pending,
                    expected_user_id=None,
                    to=S.inwork,
                    owner=lc.colleague.id,
                )
                await lc.session.commit()
                return await service.repo.get_with_relations(taken.id, populate_existing=True)
            return await real_find(criteria, **kw)

        monkeypatch.setattr(service.repo, "find_random", racing_find)
        criteria = RecordSearchCriteria(record_type_name=rt, record_status=S.pending, wo_user=True)
        claimed = await service.claim_random_from_pool(criteria, actor=human(lc.owner))
        assert (claimed.id, claimed.user_id) == (free.id, lc.owner.id)
        assert seen == [set(), {taken.id}]

    @pytest.mark.asyncio
    async def test_empty_pool_is_none(self, lc):
        rt = await lc.record_type("lc-pool-empty")
        criteria = RecordSearchCriteria(record_type_name=rt, record_status=S.pending, wo_user=True)
        assert await lc.service().claim_random_from_pool(criteria, actor=human(lc.owner)) is None

    @pytest.mark.asyncio
    async def test_gives_up_after_three_lost_races(self, lc, monkeypatch):
        rt = await lc.record_type("lc-pool-giveup")
        rec = await lc.seed(rt, status=S.pending)
        service = lc.service()

        async def always_taken(record_id, *, actor):
            raise AuthorizationError("Record is assigned to another user")

        monkeypatch.setattr(service, "claim_record", always_taken)
        criteria = RecordSearchCriteria(record_type_name=rt, record_status=S.pending, wo_user=True)
        monkeypatch.setattr(service.repo, "find_random", lambda c, **kw: _returning(rec))
        with pytest.raises(ConcurrentTransitionError, match="taken first"):
            await service.claim_random_from_pool(criteria, actor=human(lc.owner))


async def _returning(value):
    return value


class TestUnassignAndRelease:
    @pytest.mark.asyncio
    async def test_admin_unassigns_inwork(self, lc):
        rec = await lc.seed(
            await lc.record_type("lc-unassign"), status=S.inwork, user_id=lc.owner.id
        )
        updated, _ = await lc.service().unassign_user(rec.id, actor=human(lc.admin))
        assert (updated.status, updated.user_id) == (S.pending, None)
        assert lc.engine.calls == [("status", rec.id, S.pending)]

    @pytest.mark.asyncio
    async def test_owner_releases_a_releasable_record(self, lc, test_settings):
        rt = await lc.record_type("lc-release", releasable=True)
        rec = await lc.seed(rt, status=S.inwork, user_id=lc.owner.id)
        async with client_as(lc.owner, lc.session, test_settings) as client:
            resp = await client.delete(f"{RECORDS_BASE}/{rec.id}/user")
        assert resp.status_code == 200
        assert (resp.json()["status"], resp.json()["user_id"]) == ("pending", None)

    @pytest.mark.asyncio
    async def test_owner_release_is_off_by_default(self, lc, test_settings):
        rec = await lc.seed(
            await lc.record_type("lc-release-off"), status=S.inwork, user_id=lc.owner.id
        )
        async with client_as(lc.owner, lc.session, test_settings) as client:
            resp = await client.delete(f"{RECORDS_BASE}/{rec.id}/user")
        assert resp.status_code == 403
        assert (await _fresh(lc, rec.id)).user_id == lc.owner.id

    @pytest.mark.asyncio
    async def test_a_finished_record_cannot_be_released(self, lc, test_settings):
        rt = await lc.record_type("lc-release-fin", releasable=True)
        rec = await lc.seed(rt, status=S.finished, user_id=lc.owner.id)
        async with client_as(lc.owner, lc.session, test_settings) as client:
            resp = await client.delete(f"{RECORDS_BASE}/{rec.id}/user")
        assert resp.status_code == 409
        assert resp.json()["code"] == "TRANSITION_NOT_ALLOWED"
        assert (await _fresh(lc, rec.id)).user_id == lc.owner.id

    @pytest.mark.asyncio
    async def test_admin_unassigns_through_the_record_endpoint(self, lc, test_settings):
        rec = await lc.seed(
            await lc.record_type("lc-release-adm"), status=S.finished, user_id=lc.owner.id
        )
        async with client_as(lc.admin, lc.session, test_settings) as client:
            resp = await client.delete(f"{RECORDS_BASE}/{rec.id}/user")
        assert resp.status_code == 200
        assert (resp.json()["status"], resp.json()["user_id"]) == ("finished", None)
