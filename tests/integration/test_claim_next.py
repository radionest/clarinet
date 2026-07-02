"""Integration tests for POST /api/records/claim-next (take a task from the pool).

Covers the "take a task" dashboard action: a regular user claims a random
unassigned ``pending`` record of a chosen type, respecting role scope and
``unique_per_user`` filtering. The claimed record is assigned to the caller and
moved to ``inwork``; an empty / non-claimable pool yields 404.
"""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, sessionmaker
from sqlmodel import select

from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.record import Record, RecordType
from clarinet.models.user import User, UserRole, UserRolesLink
from clarinet.repositories.record_repository import RecordRepository, RecordSearchCriteria
from clarinet.services.record_service import RecordService
from clarinet.utils.auth import get_password_hash
from tests.conftest import create_authenticated_client
from tests.utils.urls import RECORDS_CLAIM_NEXT


@pytest_asyncio.fixture
async def claim_role(test_session):
    """Role granting access to the claimable record types below."""
    role = UserRole(name="claimer")
    test_session.add(role)
    await test_session.commit()
    await test_session.refresh(role)
    return role


@pytest_asyncio.fixture
async def claim_user(test_session, claim_role):
    """Non-superuser with the claimer role (roles eager-loaded for RBAC)."""
    user_id = uuid4()
    user = User(
        id=user_id,
        email="claimer@test.com",
        hashed_password=get_password_hash("x"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()
    test_session.add(UserRolesLink(user_id=user_id, role_name=claim_role.name))
    await test_session.commit()
    stmt = select(User).where(User.id == user_id).options(selectinload(User.roles))
    result = await test_session.execute(stmt)
    return result.scalar_one()


@pytest_asyncio.fixture
async def claim_type(test_session, claim_role):
    """Non-unique SERIES-level RecordType claimable by the claimer role."""
    rt = RecordType(
        name="claimable-type",
        description="Claimable from the pool",
        unique_per_user=False,
        level=DicomQueryLevel.SERIES,
        role_name=claim_role.name,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


async def _seed_record(
    test_session,
    patient,
    study,
    series,
    record_type,
    *,
    status=RecordStatus.pending,
    user_id=None,
):
    """Persist a SERIES-level record in the given status / assignment."""
    record = Record(
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=series.series_uid,
        user_id=user_id,
        record_type_name=record_type.name,
        status=status,
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)
    return record


class TestClaimNextEndpoint:
    """Router-level tests for ``POST /api/records/claim-next``."""

    @pytest.mark.asyncio
    async def test_claims_pool_record_assigns_and_sets_inwork(
        self,
        test_session,
        test_settings,
        test_patient,
        test_study,
        test_series,
        claim_type,
        claim_user,
    ):
        """A pending unassigned record is claimed → inwork + assigned to the caller."""
        pool = await _seed_record(test_session, test_patient, test_study, test_series, claim_type)
        async for ac in create_authenticated_client(claim_user, test_session, test_settings):
            resp = await ac.post(RECORDS_CLAIM_NEXT, params={"record_type_name": claim_type.name})
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == pool.id
        assert body["status"] == RecordStatus.inwork.value
        assert body["user_id"] == str(claim_user.id)

    @pytest.mark.asyncio
    async def test_empty_pool_returns_404(
        self, test_session, test_settings, claim_type, claim_user
    ):
        """No claimable record of this type → 404."""
        async for ac in create_authenticated_client(claim_user, test_session, test_settings):
            resp = await ac.post(RECORDS_CLAIM_NEXT, params={"record_type_name": claim_type.name})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_role_mismatch_not_claimable_returns_404(
        self,
        test_session,
        test_settings,
        test_patient,
        test_study,
        test_series,
        claim_user,
    ):
        """A pending record of a type the caller's role does not allow → 404."""
        other_role = UserRole(name="other-role")
        test_session.add(other_role)
        await test_session.commit()
        other_type = RecordType(
            name="other-role-type",
            unique_per_user=False,
            level=DicomQueryLevel.SERIES,
            role_name=other_role.name,
        )
        test_session.add(other_type)
        await test_session.commit()
        await _seed_record(test_session, test_patient, test_study, test_series, other_type)

        async for ac in create_authenticated_client(claim_user, test_session, test_settings):
            resp = await ac.post(RECORDS_CLAIM_NEXT, params={"record_type_name": other_type.name})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_already_assigned_pending_not_in_pool_returns_404(
        self,
        test_session,
        test_settings,
        test_patient,
        test_study,
        test_series,
        claim_type,
        claim_user,
    ):
        """A pending record already assigned to another user is not in the pool → 404."""
        other_id = uuid4()
        other = User(
            id=other_id,
            email="other@test.com",
            hashed_password=get_password_hash("x"),
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        test_session.add(other)
        await test_session.commit()
        await _seed_record(
            test_session, test_patient, test_study, test_series, claim_type, user_id=other_id
        )

        async for ac in create_authenticated_client(claim_user, test_session, test_settings):
            resp = await ac.post(RECORDS_CLAIM_NEXT, params={"record_type_name": claim_type.name})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unique_violation_excluded_from_pool_returns_404(
        self,
        test_session,
        test_settings,
        test_patient,
        test_study,
        test_series,
        claim_user,
        claim_role,
    ):
        """A unique_per_user pool record the caller already satisfied is filtered
        out of the pool → 404 (the duplicate never reaches the claim step)."""
        unique_type = RecordType(
            name="claim-unique-type",
            unique_per_user=True,
            level=DicomQueryLevel.SERIES,
            role_name=claim_role.name,
        )
        test_session.add(unique_type)
        await test_session.commit()
        # Caller already finished one for this series context...
        await _seed_record(
            test_session,
            test_patient,
            test_study,
            test_series,
            unique_type,
            status=RecordStatus.finished,
            user_id=claim_user.id,
        )
        # ...and an unassigned pending duplicate exists (would violate uniqueness).
        await _seed_record(test_session, test_patient, test_study, test_series, unique_type)

        async for ac in create_authenticated_client(claim_user, test_session, test_settings):
            resp = await ac.post(RECORDS_CLAIM_NEXT, params={"record_type_name": unique_type.name})
        assert resp.status_code == 404


class TestClaimRecordTrigger:
    """claim_record mirrors assign_user: claiming a pending record moves it to
    inwork and fires the RecordFlow status-change trigger, so taking a task from
    the pool runs the same automation as an admin assignment."""

    @pytest.mark.asyncio
    async def test_claim_fires_status_change_trigger(
        self,
        test_session,
        test_patient,
        test_study,
        test_series,
        claim_type,
        claim_user,
    ):
        """Claiming awaits the engine status-change handler once, with the old
        (pending) status."""
        record = await _seed_record(test_session, test_patient, test_study, test_series, claim_type)
        engine = AsyncMock()
        service = RecordService(RecordRepository(test_session), engine=engine)

        await service.claim_record(record.id, claim_user.id)  # type: ignore[arg-type]

        engine.handle_record_status_change.assert_awaited_once_with(ANY, RecordStatus.pending)


class TestClaimConcurrency:
    """The select-then-claim from the pool must be atomic so two users never win
    the same record. Verified on PostgreSQL where FOR UPDATE SKIP LOCKED is real;
    skipped on the in-memory SQLite runner (SQLAlchemy omits the clause there)."""

    @pytest.mark.asyncio
    async def test_locked_pool_record_is_skipped_by_concurrent_finder(
        self,
        test_engine,
        test_session,
        test_patient,
        test_study,
        test_series,
        claim_type,
    ):
        """While one session holds the FOR UPDATE lock on the only claimable
        record, a second session's locking find_random skips it (sees an empty
        pool) instead of selecting the same row."""
        if test_engine.dialect.name != "postgresql":
            pytest.skip("FOR UPDATE SKIP LOCKED is a no-op on SQLite")

        record = await _seed_record(test_session, test_patient, test_study, test_series, claim_type)
        criteria = RecordSearchCriteria(
            record_type_name=claim_type.name,
            record_status=RecordStatus.pending,
            wo_user=True,
        )
        factory = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s1, factory() as s2:
            first = await RecordRepository(s1).find_random(criteria, for_update=True)
            assert first is not None
            assert first.id == record.id
            second = await RecordRepository(s2).find_random(criteria, for_update=True)
            assert second is None
            await s1.commit()
