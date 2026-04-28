"""PG-only locking test for ``RecordRepository.collect_descendants``.

SQLite silently ignores ``SELECT ... FOR UPDATE``, so the locking semantics
of cascade-delete (used by ``RecordService.delete_record_cascade``) are
unobservable on the default SQLite backend. This test runs only when
``CLARINET_TEST_DATABASE_URL`` points at a real PostgreSQL instance,
i.e. inside ``make test-all-stages`` Stage 6 (VM PG).
"""

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from clarinet.models import Record
from clarinet.models.base import RecordStatus
from clarinet.repositories.record_repository import RecordRepository
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    seed_record,
)

pytestmark = [
    pytest.mark.postgres_only,
    pytest.mark.skipif(
        not os.environ.get("CLARINET_TEST_DATABASE_URL"),
        reason="PG-only lock test (requires real PostgreSQL backend)",
    ),
]


@pytest_asyncio.fixture
async def seeded_root(test_session):
    """Seed Patient → Study → Series → RecordType → Record and return root id."""
    pat = make_patient("LOCK_PAT", "Lock Patient")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("LOCK_PAT", "1.2.3.910")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.910", "1.2.3.910.1", 1)
    test_session.add(series)
    await test_session.commit()

    rt = make_record_type("lock-rt")
    test_session.add(rt)
    await test_session.commit()

    root = await seed_record(
        test_session,
        patient_id="LOCK_PAT",
        study_uid="1.2.3.910",
        series_uid="1.2.3.910.1",
        rt_name="lock-rt",
    )
    return root


@pytest.mark.asyncio
async def test_collect_descendants_locks_root(test_engine, seeded_root):
    """``collect_descendants(for_update=True)`` blocks concurrent writers on the root row.

    Two independent sessions on the same engine: tx1 acquires ``FOR UPDATE``
    on the root via ``collect_descendants``; tx2 attempts an ``UPDATE`` of
    the same row and must be blocked until tx1 commits. We assert the block
    by wrapping tx2 in ``asyncio.wait_for(..., timeout=2.0)`` and expecting
    ``TimeoutError`` — without a real lock, tx2 would complete instantly.
    """
    async_session = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as tx1:
        repo = RecordRepository(tx1)
        await repo.collect_descendants(seeded_root.id, for_update=True)

        async def concurrent_update() -> None:
            async with async_session() as tx2:
                await tx2.execute(
                    update(Record)
                    .where(Record.id == seeded_root.id)
                    .values(status=RecordStatus.inwork)
                )
                await tx2.commit()

        update_task = asyncio.create_task(concurrent_update())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(update_task), timeout=2.0)

        await tx1.commit()
        # Once the lock is released, the previously blocked UPDATE completes.
        await update_task
