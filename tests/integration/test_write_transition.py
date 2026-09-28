"""RecordRepository.write_transition: the one conditional status write."""

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from clarinet.models import RecordStatus
from clarinet.repositories.record_repository import RecordRepository
from tests.utils.factories import make_record_type, make_user, seed_record


@pytest_asyncio.fixture
async def seeded(test_session, test_patient, test_study, test_series):
    test_session.add(make_record_type("wt-type", unique_by=None))
    user = make_user()
    test_session.add(user)
    await test_session.commit()

    async def seed(**kw):
        return await seed_record(
            test_session,
            test_patient.id,
            test_study.study_uid,
            test_series.series_uid,
            "wt-type",
            **kw,
        )

    return seed, user


async def _fresh(repo: RecordRepository, record_id: int):
    return await repo.get_with_relations(record_id, populate_existing=True)


@pytest.mark.asyncio
async def test_hit_writes_status_owner_and_data(test_session, seeded):
    seed, user = seeded
    rec = await seed(status=RecordStatus.pending)
    repo = RecordRepository(test_session)
    ok = await repo.write_transition(
        rec.id,
        expected_status=RecordStatus.pending,
        expected_user_id=None,
        to=RecordStatus.inwork,
        owner=user.id,
        data={"a": 1},
    )
    assert ok
    fresh = await _fresh(repo, rec.id)
    assert (fresh.status, fresh.user_id, fresh.data) == (RecordStatus.inwork, user.id, {"a": 1})
    assert fresh.started_at is not None
    assert fresh.finished_at is None


@pytest.mark.asyncio
async def test_status_miss_writes_nothing(test_session, seeded):
    seed, _ = seeded
    rec = await seed(status=RecordStatus.finished)
    repo = RecordRepository(test_session)
    ok = await repo.write_transition(
        rec.id,
        expected_status=RecordStatus.pending,
        expected_user_id=None,
        to=RecordStatus.inwork,
        owner=None,
    )
    assert not ok
    assert (await _fresh(repo, rec.id)).status == RecordStatus.finished


@pytest.mark.asyncio
async def test_owner_miss_writes_nothing(test_session, seeded):
    seed, user = seeded
    rec = await seed(status=RecordStatus.inwork, user_id=user.id)
    repo = RecordRepository(test_session)
    assert not await repo.write_transition(
        rec.id,
        expected_status=RecordStatus.inwork,
        expected_user_id=None,
        to=RecordStatus.inwork,
        owner=uuid4(),
    )
    assert not await repo.write_transition(
        rec.id,
        expected_status=RecordStatus.inwork,
        expected_user_id=uuid4(),
        to=RecordStatus.pending,
        owner=None,
    )
    assert (await _fresh(repo, rec.id)).user_id == user.id


@pytest.mark.asyncio
async def test_same_owner_and_cleared_owner(test_session, seeded):
    seed, user = seeded
    rec = await seed(status=RecordStatus.inwork, user_id=user.id)
    repo = RecordRepository(test_session)
    await repo.write_transition(
        rec.id,
        expected_status=RecordStatus.inwork,
        expected_user_id=user.id,
        to=RecordStatus.pause,
        owner=user.id,
    )
    assert (await _fresh(repo, rec.id)).user_id == user.id
    await repo.write_transition(
        rec.id,
        expected_status=RecordStatus.pause,
        expected_user_id=user.id,
        to=RecordStatus.pause,
        owner=None,
    )
    assert (await _fresh(repo, rec.id)).user_id is None


@pytest.mark.asyncio
async def test_reasons_append_without_loss(test_session, seeded):
    seed, _ = seeded
    rec = await seed(status=RecordStatus.finished)
    repo = RecordRepository(test_session)
    for reason in ("first", "second"):
        await repo.write_transition(
            rec.id,
            expected_status=RecordStatus.finished,
            expected_user_id=None,
            to=RecordStatus.finished,
            owner=None,
            reason=reason,
        )
    assert (await _fresh(repo, rec.id)).context_info == "first\nsecond"


@pytest.mark.asyncio
async def test_finished_at_moves_only_on_entering_finished(test_session, seeded):
    seed, _ = seeded
    rec = await seed(status=RecordStatus.inwork)
    repo = RecordRepository(test_session)
    kw = {"expected_user_id": None, "owner": None}
    await repo.write_transition(
        rec.id, expected_status=RecordStatus.inwork, to=RecordStatus.finished, **kw
    )
    first = (await _fresh(repo, rec.id)).finished_at
    assert first is not None
    await repo.write_transition(
        rec.id,
        expected_status=RecordStatus.finished,
        to=RecordStatus.finished,
        data={"edited": True},
        **kw,
    )
    assert (await _fresh(repo, rec.id)).finished_at == first
    await repo.write_transition(
        rec.id, expected_status=RecordStatus.finished, to=RecordStatus.pending, **kw
    )
    await asyncio.sleep(0.01)
    await repo.write_transition(
        rec.id, expected_status=RecordStatus.pending, to=RecordStatus.finished, **kw
    )
    assert (await _fresh(repo, rec.id)).finished_at > first


@pytest.mark.asyncio
async def test_never_commits(test_session, seeded):
    seed, _ = seeded
    rec = await seed(status=RecordStatus.pending)
    record_id = rec.id
    repo = RecordRepository(test_session)
    assert await repo.write_transition(
        record_id,
        expected_status=RecordStatus.pending,
        expected_user_id=None,
        to=RecordStatus.pause,
        owner=None,
    )
    await test_session.rollback()
    # rec.id itself is unreadable here: rollback() unconditionally expires every
    # object in the identity map, and re-reading an expired attribute triggers a
    # synchronous lazy-load that raises MissingGreenlet outside an awaited
    # context — an AsyncSession footgun unrelated to write_transition, so the id
    # is captured above, before the rollback.
    assert (await _fresh(repo, record_id)).status == RecordStatus.pending


@pytest.mark.asyncio
async def test_populate_existing_replaces_a_stale_identity_map_copy(test_session, seeded):
    seed, _ = seeded
    rec = await seed(status=RecordStatus.pending)
    repo = RecordRepository(test_session)
    stale = await repo.get_with_relations(rec.id)
    await repo.write_transition(
        rec.id,
        expected_status=RecordStatus.pending,
        expected_user_id=None,
        to=RecordStatus.pause,
        owner=None,
    )
    assert stale.status == RecordStatus.pending  # the UPDATE bypassed the identity map
    assert (await repo.get_with_relations(rec.id, populate_existing=True)).status == (
        RecordStatus.pause
    )


@pytest.mark.asyncio
async def test_sequential_writers_second_misses(test_session, seeded):
    seed, user = seeded
    rec = await seed(status=RecordStatus.inwork)
    repo = RecordRepository(test_session)
    kw = {
        "expected_status": RecordStatus.inwork,
        "expected_user_id": None,
        "to": RecordStatus.inwork,
    }
    assert await repo.write_transition(rec.id, owner=user.id, **kw)
    assert not await repo.write_transition(rec.id, owner=uuid4(), **kw)


@pytest.mark.skipif(
    not os.environ.get("CLARINET_TEST_DATABASE_URL"), reason="needs two real connections (PG)"
)
@pytest.mark.asyncio
async def test_concurrent_writers_exactly_one_wins(test_engine, test_session, seeded):
    seed, user = seeded
    other = make_user()
    test_session.add(other)
    await test_session.commit()
    rec = await seed(status=RecordStatus.inwork)
    factory = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def claim(owner):
        async with factory() as session:
            won = await RecordRepository(session).write_transition(
                rec.id,
                expected_status=RecordStatus.inwork,
                expected_user_id=None,
                to=RecordStatus.inwork,
                owner=owner,
            )
            await session.commit()
            return won

    results = await asyncio.gather(claim(user.id), claim(other.id))
    assert sorted(results) == [False, True]
