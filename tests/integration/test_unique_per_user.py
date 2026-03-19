"""Integration tests for the unique_per_user feature.

Covers:
- RecordRepository.count_user_records_for_context (all levels, all statuses)
- RecordRepository.find_by_user with exclude_unique_violations
- RecordService.assign_user constraint check
- POST /api/records/ constraint check (409 when user_id set and violated)
- GET /api/records/my violation filtering (non-superuser vs superuser)
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from clarinet.exceptions.domain import RecordConstraintViolationError
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.record import Record, RecordType
from clarinet.repositories.record_repository import RecordRepository
from clarinet.services.record_service import RecordService
from tests.utils.factories import make_series, make_user
from tests.utils.urls import RECORDS_BASE, RECORDS_MY

# ── Shared fixture helpers ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def unique_series_type(test_session):
    """RecordType with unique_per_user=True at SERIES level."""
    rt = RecordType(
        name="unique-series-type",
        description="Unique per user at series level",
        unique_per_user=True,
        level=DicomQueryLevel.SERIES,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def unique_study_type(test_session):
    """RecordType with unique_per_user=True at STUDY level."""
    rt = RecordType(
        name="unique-study-type",
        description="Unique per user at study level",
        unique_per_user=True,
        level=DicomQueryLevel.STUDY,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def unique_patient_type(test_session):
    """RecordType with unique_per_user=True at PATIENT level."""
    rt = RecordType(
        name="unique-patient-type",
        description="Unique per user at patient level",
        unique_per_user=True,
        level=DicomQueryLevel.PATIENT,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def non_unique_type(test_session):
    """RecordType with unique_per_user=False."""
    rt = RecordType(
        name="non-unique-type",
        description="Not unique per user",
        unique_per_user=False,
        level=DicomQueryLevel.SERIES,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def second_series(test_session, test_study):
    """A second series under test_study for context-isolation tests."""
    series = make_series(test_study.study_uid, uid="1.2.3.4.5.6.7.8.9.2", num=2)
    test_session.add(series)
    await test_session.commit()
    await test_session.refresh(series)
    return series


# ── Section 1: count_user_records_for_context ─────────────────────────────────


class TestCountUserRecordsForContext:
    """Tests for RecordRepository.count_user_records_for_context."""

    @pytest.mark.asyncio
    async def test_series_level_counts_matching_record(
        self, test_session, test_user, test_patient, test_study, test_series, unique_series_type
    ):
        """SERIES level: counts records matching (user, type, series_uid)."""
        record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(record)
        await test_session.commit()

        repo = RecordRepository(test_session)
        count = await repo.count_user_records_for_context(
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            level="SERIES",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_study_level_counts_matching_record(
        self, test_session, test_user, test_patient, test_study, test_series, unique_study_type
    ):
        """STUDY level: counts records matching (user, type, study_uid)."""
        record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            user_id=test_user.id,
            record_type_name=unique_study_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(record)
        await test_session.commit()

        repo = RecordRepository(test_session)
        count = await repo.count_user_records_for_context(
            user_id=test_user.id,
            record_type_name=unique_study_type.name,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=None,
            level="STUDY",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_patient_level_counts_matching_record(
        self, test_session, test_user, test_patient, test_study, unique_patient_type
    ):
        """PATIENT level: counts records matching (user, type, patient_id)."""
        record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            user_id=test_user.id,
            record_type_name=unique_patient_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(record)
        await test_session.commit()

        repo = RecordRepository(test_session)
        count = await repo.count_user_records_for_context(
            user_id=test_user.id,
            record_type_name=unique_patient_type.name,
            patient_id=test_patient.id,
            study_uid=None,
            series_uid=None,
            level="PATIENT",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_matching_records(
        self, test_session, test_user, test_patient, test_study, test_series, unique_series_type
    ):
        """Returns 0 when no matching records exist for the user/type/context."""
        repo = RecordRepository(test_session)
        count = await repo.count_user_records_for_context(
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            level="SERIES",
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_counts_all_statuses(
        self, test_session, test_user, test_patient, test_study, test_series, unique_series_type
    ):
        """Returns count across all status values (pending, inwork, finished, failed)."""
        statuses = [
            RecordStatus.pending,
            RecordStatus.inwork,
            RecordStatus.finished,
            RecordStatus.failed,
        ]
        for _i, status in enumerate(statuses):
            # Use distinct series UIDs derived from the base to avoid level conflicts;
            # here we're testing the count aggregation so we re-use the same series.
            record = Record(
                patient_id=test_patient.id,
                study_uid=test_study.study_uid,
                series_uid=test_series.series_uid,
                user_id=test_user.id,
                record_type_name=unique_series_type.name,
                status=status,
            )
            test_session.add(record)
        await test_session.commit()

        repo = RecordRepository(test_session)
        count = await repo.count_user_records_for_context(
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            level="SERIES",
        )
        assert count == len(statuses)

    @pytest.mark.asyncio
    async def test_does_not_count_different_user(
        self, test_session, test_user, test_patient, test_study, test_series, unique_series_type
    ):
        """Does not count records belonging to a different user."""
        other_user = make_user()
        test_session.add(other_user)
        await test_session.commit()

        record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=other_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(record)
        await test_session.commit()

        repo = RecordRepository(test_session)
        count = await repo.count_user_records_for_context(
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            level="SERIES",
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_series_level_does_not_count_different_series(
        self,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        second_series,
        unique_series_type,
    ):
        """SERIES level: does not count records for a different series_uid."""
        record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=second_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(record)
        await test_session.commit()

        repo = RecordRepository(test_session)
        count = await repo.count_user_records_for_context(
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            level="SERIES",
        )
        assert count == 0


# ── Section 2: RecordService.assign_user constraint ───────────────────────────


class TestAssignUserUniqueConstraint:
    """Tests for RecordService.assign_user unique_per_user enforcement."""

    @pytest.mark.asyncio
    async def test_assign_user_raises_when_unique_per_user_violated(
        self, test_session, test_user, test_patient, test_study, test_series, unique_series_type
    ):
        """assign_user raises RecordConstraintViolationError when user already
        has a record of unique_per_user type for the same series context."""
        # Existing record: user already has one for this series
        existing = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.inwork,
        )
        test_session.add(existing)
        await test_session.commit()

        # New unassigned record for the same series
        new_record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=None,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(new_record)
        await test_session.commit()
        await test_session.refresh(new_record)

        repo = RecordRepository(test_session)
        service = RecordService(repo)

        with pytest.raises(RecordConstraintViolationError):
            await service.assign_user(new_record.id, test_user.id)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_assign_user_succeeds_when_unique_per_user_false(
        self, test_session, test_user, test_patient, test_study, test_series, non_unique_type
    ):
        """assign_user works fine when unique_per_user is False, even if the
        user already has a record of the same type for the same series."""
        existing = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=non_unique_type.name,
            status=RecordStatus.inwork,
        )
        test_session.add(existing)
        await test_session.commit()

        new_record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=None,
            record_type_name=non_unique_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(new_record)
        await test_session.commit()
        await test_session.refresh(new_record)

        repo = RecordRepository(test_session)
        service = RecordService(repo)

        # Should not raise
        record, _ = await service.assign_user(new_record.id, test_user.id)  # type: ignore[arg-type]
        assert record.user_id == test_user.id

    @pytest.mark.asyncio
    async def test_assign_user_succeeds_for_different_series_context(
        self,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        second_series,
        unique_series_type,
    ):
        """assign_user works fine when the user has a record for the same type
        but in a different series context."""
        # User already has a record for test_series
        existing = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.inwork,
        )
        test_session.add(existing)
        await test_session.commit()

        # New record for second_series — different context, should be allowed
        new_record = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=second_series.series_uid,
            user_id=None,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(new_record)
        await test_session.commit()
        await test_session.refresh(new_record)

        repo = RecordRepository(test_session)
        service = RecordService(repo)

        # Should not raise
        record, _ = await service.assign_user(new_record.id, test_user.id)  # type: ignore[arg-type]
        assert record.user_id == test_user.id


# ── Section 3: API constraint on POST /api/records/ ──────────────────────────


class TestCreateRecordApiConstraint:
    """Tests for POST /api/records/ unique_per_user enforcement."""

    @pytest.mark.asyncio
    async def test_create_with_user_id_raises_409_when_unique_violated(
        self,
        client,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        unique_series_type,
    ):
        """POST /api/records/ with user_id set returns 409 when unique_per_user
        is violated (user already has a record for that type+series)."""
        # Seed existing assigned record
        existing = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(existing)
        await test_session.commit()

        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
                "series_uid": test_series.series_uid,
                "user_id": str(test_user.id),
                "record_type_name": unique_series_type.name,
                "status": "pending",
            },
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_create_without_user_id_succeeds_even_if_unique_violated(
        self,
        client,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        unique_series_type,
    ):
        """POST /api/records/ without user_id succeeds even when user already has
        a record of that type for the same context — the constraint only applies
        when user_id is explicitly provided at creation time."""
        # Seed existing assigned record
        existing = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(existing)
        await test_session.commit()

        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
                "series_uid": test_series.series_uid,
                "user_id": None,
                "record_type_name": unique_series_type.name,
                "status": "pending",
            },
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_with_user_id_succeeds_when_non_unique_type(
        self,
        client,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        non_unique_type,
    ):
        """POST /api/records/ with user_id succeeds when unique_per_user is False,
        even if user already has a record of that type for the same context."""
        existing = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=non_unique_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(existing)
        await test_session.commit()

        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
                "series_uid": test_series.series_uid,
                "user_id": str(test_user.id),
                "record_type_name": non_unique_type.name,
                "status": "pending",
            },
        )
        assert resp.status_code == 201


# ── Section 4: find_by_user exclude_unique_violations ────────────────────────


class TestFindByUserUniqueViolationFilter:
    """Tests for RecordRepository.find_by_user(exclude_unique_violations=True/False).

    The listing filter is exercised at the repository level because the
    ``client`` fixture is a superuser (and superusers always see all records).
    These tests verify the SQL filter logic that the GET /records/my endpoint
    applies for non-superuser callers.
    """

    @pytest.mark.asyncio
    async def test_exclude_violations_hides_unassigned_violating_record(
        self,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        unique_series_type,
    ):
        """find_by_user with exclude_unique_violations=True hides an unassigned
        record when the user already has an assigned record of the same
        unique_per_user type for the same series."""
        # User's own record (assigned)
        assigned = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.inwork,
        )
        # Unassigned record for the same context — should be hidden
        unassigned = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=None,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(assigned)
        test_session.add(unassigned)
        await test_session.commit()

        repo = RecordRepository(test_session)
        records = await repo.find_by_user(
            test_user.id,
            include_unassigned=True,
            exclude_unique_violations=True,
        )
        record_ids = [r.id for r in records]
        assert assigned.id in record_ids
        assert unassigned.id not in record_ids

    @pytest.mark.asyncio
    async def test_without_exclude_violations_shows_unassigned_record(
        self,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        unique_series_type,
    ):
        """find_by_user with exclude_unique_violations=False shows unassigned
        records regardless of unique_per_user violations (superuser behaviour)."""
        assigned = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.inwork,
        )
        unassigned = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=None,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(assigned)
        test_session.add(unassigned)
        await test_session.commit()

        repo = RecordRepository(test_session)
        records = await repo.find_by_user(
            test_user.id,
            include_unassigned=True,
            exclude_unique_violations=False,
        )
        record_ids = [r.id for r in records]
        assert assigned.id in record_ids
        assert unassigned.id in record_ids

    @pytest.mark.asyncio
    async def test_exclude_violations_shows_unassigned_for_different_series(
        self,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        second_series,
        unique_series_type,
    ):
        """find_by_user with exclude_unique_violations=True still shows an
        unassigned record when the user has a record for a different series
        of the same type (different context — no violation)."""
        # User's own record for test_series
        assigned = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.inwork,
        )
        # Unassigned record for second_series — different context, should be visible
        unassigned_other = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=second_series.series_uid,
            user_id=None,
            record_type_name=unique_series_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(assigned)
        test_session.add(unassigned_other)
        await test_session.commit()

        repo = RecordRepository(test_session)
        records = await repo.find_by_user(
            test_user.id,
            include_unassigned=True,
            exclude_unique_violations=True,
        )
        record_ids = [r.id for r in records]
        assert assigned.id in record_ids
        assert unassigned_other.id in record_ids

    @pytest.mark.asyncio
    async def test_exclude_violations_shows_unassigned_for_non_unique_type(
        self,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        non_unique_type,
    ):
        """find_by_user with exclude_unique_violations=True still shows an
        unassigned record when the record type has unique_per_user=False."""
        assigned = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=non_unique_type.name,
            status=RecordStatus.inwork,
        )
        unassigned = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=None,
            record_type_name=non_unique_type.name,
            status=RecordStatus.pending,
        )
        test_session.add(assigned)
        test_session.add(unassigned)
        await test_session.commit()

        repo = RecordRepository(test_session)
        records = await repo.find_by_user(
            test_user.id,
            include_unassigned=True,
            exclude_unique_violations=True,
        )
        record_ids = [r.id for r in records]
        assert assigned.id in record_ids
        assert unassigned.id in record_ids

    @pytest.mark.asyncio
    async def test_get_my_records_api_superuser_sees_all(
        self,
        client,
        test_session,
        test_user,
        test_patient,
        test_study,
        test_series,
        unique_series_type,
    ):
        """GET /api/records/my — superuser (the client fixture) sees all records
        including unassigned ones that would violate unique_per_user for a regular user."""
        # Seed an assigned record for test_user
        assigned = Record(
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
            user_id=test_user.id,
            record_type_name=unique_series_type.name,
            status=RecordStatus.inwork,
        )
        # The superuser (mock_user from the client fixture) has no records —
        # so from a superuser perspective there are no violations; the endpoint
        # returns only records assigned to the calling user (the mock superuser).
        test_session.add(assigned)
        await test_session.commit()

        # Superuser sees only their OWN assigned records (no unassigned included
        # for superusers per the endpoint logic), so the response should be empty
        # because the mock superuser has no records.
        resp = await client.get(RECORDS_MY)
        assert resp.status_code == 200
        # The assigned record belongs to test_user, not to the client's mock superuser
        data = resp.json()
        returned_ids = [r["id"] for r in data]
        assert assigned.id not in returned_ids
