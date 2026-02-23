"""Tests for Record model consistency fixes.

Covers:
- RecordRead serialization of started_at/finished_at timestamps
- RecordTypeOptional schema (no id field)
- SeriesRepository.find_by_criteria() with RecordFind EXISTS filtering
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from src.models.base import DicomQueryLevel, RecordStatus
from src.models.patient import Patient
from src.models.record import Record, RecordFind, RecordRead, RecordType, RecordTypeOptional
from src.models.study import Series, SeriesFind, Study
from src.repositories.series_repository import SeriesRepository

# ---------------------------------------------------------------------------
# Group 1: RecordRead timestamps (started_at / finished_at)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_read_includes_started_at_on_inwork(
    test_session, test_user, test_patient, test_study
):
    """started_at is set when status transitions to inwork and exposed via RecordRead."""
    record_type = RecordType(
        name="Timestamps Inwork",
        description="test",
        level=DicomQueryLevel.STUDY,
    )
    test_session.add(record_type)
    await test_session.commit()

    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.pending,
    )
    test_session.add(record)
    await test_session.commit()

    # Transition to inwork — event listener should set started_at
    record.status = RecordStatus.inwork
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)

    assert record.started_at is not None

    # Eagerly load relations needed by RecordRead
    await test_session.refresh(record, ["patient", "study", "record_type"])
    read = RecordRead.model_validate(record, from_attributes=True)
    data = read.model_dump()
    assert data["started_at"] is not None


@pytest.mark.asyncio
async def test_record_read_includes_finished_at_on_finished(
    test_session, test_user, test_patient, test_study
):
    """finished_at is set when status transitions to finished and exposed via RecordRead."""
    record_type = RecordType(
        name="Timestamps Finish",
        description="test",
        level=DicomQueryLevel.STUDY,
    )
    test_session.add(record_type)
    await test_session.commit()

    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.inwork,
    )
    test_session.add(record)
    await test_session.commit()

    # Transition to finished
    record.status = RecordStatus.finished
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)

    assert record.finished_at is not None

    await test_session.refresh(record, ["patient", "study", "record_type"])
    read = RecordRead.model_validate(record, from_attributes=True)
    data = read.model_dump()
    assert data["finished_at"] is not None


@pytest.mark.asyncio
async def test_record_read_timestamps_none_for_pending(
    test_session, test_user, test_patient, test_study
):
    """started_at and finished_at are None for a freshly created pending record."""
    record_type = RecordType(
        name="Timestamps Pending",
        description="test",
        level=DicomQueryLevel.STUDY,
    )
    test_session.add(record_type)
    await test_session.commit()

    record = Record(
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=test_user.id,
        record_type_name=record_type.name,
        status=RecordStatus.pending,
    )
    test_session.add(record)
    await test_session.commit()
    await test_session.refresh(record)

    await test_session.refresh(record, ["patient", "study", "record_type"])
    read = RecordRead.model_validate(record, from_attributes=True)
    data = read.model_dump()
    assert data["started_at"] is None
    assert data["finished_at"] is None


# ---------------------------------------------------------------------------
# Group 2: RecordTypeOptional has no id field
# ---------------------------------------------------------------------------


def test_record_type_optional_has_no_id_field():
    """RecordTypeOptional must not expose an id field (update schema only)."""
    assert "id" not in RecordTypeOptional.model_fields


# ---------------------------------------------------------------------------
# Group 3: find_by_criteria with RecordFind (EXISTS sub-query filtering)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _series_with_records(test_session, test_user):
    """Create test data: 3 series with varying records for criteria tests."""
    patient = Patient(id="CRIT_PAT001", name="Criteria Patient", anon_name="ANON_CRIT_001")
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.6.7.8.100",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_CRIT_STUDY",
    )
    test_session.add(study)
    await test_session.commit()

    series_a = Series(
        series_uid="1.2.3.4.5.100.1",
        series_description="Series A",
        series_number=1,
        study_uid=study.study_uid,
    )
    series_b = Series(
        series_uid="1.2.3.4.5.100.2",
        series_description="Series B",
        series_number=2,
        study_uid=study.study_uid,
    )
    series_c = Series(
        series_uid="1.2.3.4.5.100.3",
        series_description="Series C",
        series_number=3,
        study_uid=study.study_uid,
    )
    test_session.add_all([series_a, series_b, series_c])
    await test_session.commit()

    rt_alpha = RecordType(
        name="rt_alpha_criteria",
        description="alpha",
        level=DicomQueryLevel.SERIES,
    )
    rt_beta = RecordType(
        name="rt_beta_criteria",
        description="beta",
        level=DicomQueryLevel.SERIES,
    )
    test_session.add_all([rt_alpha, rt_beta])
    await test_session.commit()

    # Series A: rt_alpha (finished, test_user) + rt_beta (pending, no user)
    rec_a1 = Record(
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=series_a.series_uid,
        record_type_name=rt_alpha.name,
        status=RecordStatus.finished,
        user_id=test_user.id,
    )
    rec_a2 = Record(
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=series_a.series_uid,
        record_type_name=rt_beta.name,
        status=RecordStatus.pending,
    )

    # Series B: rt_alpha (pending, test_user)
    rec_b1 = Record(
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=series_b.series_uid,
        record_type_name=rt_alpha.name,
        status=RecordStatus.pending,
        user_id=test_user.id,
    )

    # Series C: no records

    test_session.add_all([rec_a1, rec_a2, rec_b1])
    await test_session.commit()

    return {
        "series_a": series_a,
        "series_b": series_b,
        "series_c": series_c,
        "rt_alpha": rt_alpha,
        "rt_beta": rt_beta,
    }


@pytest.mark.asyncio
async def test_find_by_record_type_name(test_session, _series_with_records):
    """Filter series that have a record of a given type name."""
    data = _series_with_records
    repo = SeriesRepository(test_session)

    query = SeriesFind(records=[RecordFind(record_type_name=data["rt_alpha"].name)])
    result = await repo.find_by_criteria(query)
    uids = {s.series_uid for s in result}

    assert data["series_a"].series_uid in uids
    assert data["series_b"].series_uid in uids
    assert data["series_c"].series_uid not in uids


@pytest.mark.asyncio
async def test_find_by_record_type_name_and_status(test_session, _series_with_records):
    """Filter series that have a record with a specific type AND status."""
    data = _series_with_records
    repo = SeriesRepository(test_session)

    query = SeriesFind(
        records=[
            RecordFind(
                record_type_name=data["rt_alpha"].name,
                status=RecordStatus.finished,
            )
        ]
    )
    result = await repo.find_by_criteria(query)
    uids = {s.series_uid for s in result}

    assert uids == {data["series_a"].series_uid}


@pytest.mark.asyncio
async def test_find_by_record_type_name_and_user_id(test_session, test_user, _series_with_records):
    """Filter series that have a record with a specific type AND user."""
    data = _series_with_records
    repo = SeriesRepository(test_session)

    query = SeriesFind(
        records=[
            RecordFind(
                record_type_name=data["rt_alpha"].name,
                user_id=test_user.id,
            )
        ]
    )
    result = await repo.find_by_criteria(query)
    uids = {s.series_uid for s in result}

    assert data["series_a"].series_uid in uids
    assert data["series_b"].series_uid in uids
    assert data["series_c"].series_uid not in uids


@pytest.mark.asyncio
async def test_find_is_absent(test_session, _series_with_records):
    """is_absent=True returns series that do NOT have the given record type."""
    data = _series_with_records
    repo = SeriesRepository(test_session)

    query = SeriesFind(records=[RecordFind(record_type_name=data["rt_alpha"].name, is_absent=True)])
    result = await repo.find_by_criteria(query)
    uids = {s.series_uid for s in result}

    assert uids == {data["series_c"].series_uid}


@pytest.mark.asyncio
async def test_find_multiple_record_criteria(test_session, _series_with_records):
    """Multiple RecordFind entries are AND-combined — only series matching all criteria."""
    data = _series_with_records
    repo = SeriesRepository(test_session)

    query = SeriesFind(
        records=[
            RecordFind(record_type_name=data["rt_alpha"].name),
            RecordFind(record_type_name=data["rt_beta"].name),
        ]
    )
    result = await repo.find_by_criteria(query)
    uids = {s.series_uid for s in result}

    # Only Series A has both rt_alpha and rt_beta
    assert uids == {data["series_a"].series_uid}


@pytest.mark.asyncio
async def test_find_is_absent_combined_with_present(test_session, _series_with_records):
    """Combine present (EXISTS) and absent (~EXISTS) criteria in one query."""
    data = _series_with_records
    repo = SeriesRepository(test_session)

    query = SeriesFind(
        records=[
            RecordFind(record_type_name=data["rt_alpha"].name),
            RecordFind(record_type_name=data["rt_beta"].name, is_absent=True),
        ]
    )
    result = await repo.find_by_criteria(query)
    uids = {s.series_uid for s in result}

    # Series B has alpha but NOT beta
    assert uids == {data["series_b"].series_uid}
