"""Integration tests for the ``parent_required`` constraint on RecordType.

Covers:
- ``RecordRepository.check_constraints`` raises ``RecordParentRequiredError``
  when the RecordType has ``parent_required=True`` and the caller did not
  pass a ``parent_record_id``.
- ``check_constraints`` accepts the call when a ``parent_record_id`` is
  provided, regardless of whether the referenced record actually exists
  (existence check lives in ``validate_parent_record`` at the router).
- ``check_constraints`` is a no-op for the flag when ``parent_required=False``.
- ``POST /api/records/`` returns 409 with ``code="PARENT_REQUIRED"`` when the
  body omits ``parent_record_id`` for a ``parent_required`` type.
- ``POST /api/records/`` succeeds (201) when the body supplies
  ``parent_record_id``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from clarinet.exceptions.domain import RecordParentRequiredError
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.record import Record, RecordType
from clarinet.repositories.record_repository import RecordRepository
from tests.utils.urls import RECORDS_BASE


@pytest_asyncio.fixture
async def parent_required_type(test_session):
    """RecordType with ``parent_required=True`` at SERIES level."""
    rt = RecordType(
        name="parent-required-type",
        description="Requires a parent record",
        parent_required=True,
        unique_per_user=False,
        level=DicomQueryLevel.SERIES,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def parent_optional_type(test_session):
    """RecordType with ``parent_required=False`` (default) at SERIES level."""
    rt = RecordType(
        name="parent-optional-type",
        description="Parent not required",
        parent_required=False,
        unique_per_user=False,
        level=DicomQueryLevel.SERIES,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def existing_parent_record(
    test_session, test_patient, test_study, test_series, parent_optional_type
):
    """A persisted Record (of an unrelated type) usable as parent_record_id."""
    parent = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        record_type_name=parent_optional_type.name,
        status=RecordStatus.pending,
    )
    test_session.add(parent)
    await test_session.commit()
    await test_session.refresh(parent)
    return parent


class TestCheckConstraintsParentRequired:
    """Unit-style coverage of the new ``parent_record_id`` branch."""

    @pytest.mark.asyncio
    async def test_raises_when_parent_required_and_missing(
        self, test_session, test_study, test_series, parent_required_type
    ):
        repo = RecordRepository(test_session)
        with pytest.raises(RecordParentRequiredError) as exc_info:
            await repo.check_constraints(
                parent_required_type.name,
                series_uid=test_series.series_uid,
                study_uid=test_study.study_uid,
                parent_record_id=None,
            )
        assert exc_info.value.error_code == "PARENT_REQUIRED"
        assert parent_required_type.name in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_accepts_when_parent_required_and_provided(
        self, test_session, test_study, test_series, parent_required_type
    ):
        repo = RecordRepository(test_session)
        # check_constraints does not load the parent — existence is enforced
        # later by ``validate_parent_record``. Any non-None id passes here.
        await repo.check_constraints(
            parent_required_type.name,
            series_uid=test_series.series_uid,
            study_uid=test_study.study_uid,
            parent_record_id=999,
        )

    @pytest.mark.asyncio
    async def test_no_op_when_parent_required_false(
        self, test_session, test_study, test_series, parent_optional_type
    ):
        repo = RecordRepository(test_session)
        # parent_record_id stays None — no exception expected.
        await repo.check_constraints(
            parent_optional_type.name,
            series_uid=test_series.series_uid,
            study_uid=test_study.study_uid,
            parent_record_id=None,
        )


class TestCreateRecordApiParentRequired:
    """End-to-end coverage of POST /api/records/ with parent_required."""

    @pytest.mark.asyncio
    async def test_post_without_parent_returns_409(
        self,
        client,
        test_patient,
        test_study,
        test_series,
        parent_required_type,
    ):
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
                "series_uid": test_series.series_uid,
                "record_type_name": parent_required_type.name,
                "status": "pending",
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "PARENT_REQUIRED"

    @pytest.mark.asyncio
    async def test_post_with_parent_succeeds(
        self,
        client,
        test_patient,
        test_study,
        test_series,
        parent_required_type,
        existing_parent_record,
    ):
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
                "series_uid": test_series.series_uid,
                "record_type_name": parent_required_type.name,
                "status": "pending",
                "parent_record_id": existing_parent_record.id,
            },
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_post_with_required_parent_not_existing_returns_404(
        self,
        client,
        test_patient,
        test_study,
        test_series,
        parent_required_type,
    ):
        # Regression test on separation of responsibilities:
        # check_constraints only enforces NULL/non-NULL; existence of the
        # referenced parent is enforced later by validate_parent_record.
        # A non-NULL but missing parent_record_id must surface as 404
        # (RecordNotFoundError), NOT 409 (PARENT_REQUIRED).
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
                "series_uid": test_series.series_uid,
                "record_type_name": parent_required_type.name,
                "status": "pending",
                "parent_record_id": 999_999,
            },
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body.get("code") != "PARENT_REQUIRED"
