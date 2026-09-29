"""Tests for Record model consistency fixes.

Covers:
- RecordRead serialization of started_at/finished_at timestamps
- RecordTypeOptional schema (no id field)
- SeriesRepository.find_by_criteria() with RecordFind EXISTS filtering
- The direct-write guard on Record.status / Record.user_id
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from clarinet.exceptions.domain import DirectRecordWriteError
from clarinet.models import Record, RecordStatus
from clarinet.models.base import DicomQueryLevel
from clarinet.models.record import RecordFind, RecordRead, RecordType, RecordTypeOptional
from clarinet.models.study import Series, SeriesFind, Study
from clarinet.repositories.record_repository import RecordRepository
from clarinet.repositories.series_repository import SeriesRepository
from tests.utils.factories import make_patient, make_record_type, seed_record

# ---------------------------------------------------------------------------
# Group 1: RecordRead timestamps (started_at / finished_at)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_read_includes_started_at_on_inwork(
    test_session, test_user, test_patient, test_study
):
    """started_at is set when status transitions to inwork and exposed via RecordRead."""
    record_type = RecordType(
        name="timestamps-inwork",
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

    # Transition to inwork through the writer — started_at is stamped there.
    repo = RecordRepository(test_session)
    await repo.write_transition(
        record.id,
        expected_status=RecordStatus.pending,
        expected_user_id=test_user.id,
        to=RecordStatus.inwork,
        owner=test_user.id,
    )
    record = await repo.get_with_relations(record.id, populate_existing=True)
    assert record.started_at is not None
    assert RecordRead.model_validate(record).model_dump()["started_at"] is not None


@pytest.mark.asyncio
async def test_record_read_includes_finished_at_on_finished(
    test_session, test_user, test_patient, test_study
):
    """finished_at is set when status transitions to finished and exposed via RecordRead."""
    record_type = RecordType(
        name="timestamps-finish",
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

    # Transition to finished through the writer — finished_at is stamped there.
    repo = RecordRepository(test_session)
    await repo.write_transition(
        record.id,
        expected_status=RecordStatus.inwork,
        expected_user_id=test_user.id,
        to=RecordStatus.finished,
        owner=test_user.id,
    )
    record = await repo.get_with_relations(record.id, populate_existing=True)
    assert record.finished_at is not None
    assert RecordRead.model_validate(record).model_dump()["finished_at"] is not None


@pytest.mark.asyncio
async def test_record_read_timestamps_none_for_pending(
    test_session, test_user, test_patient, test_study
):
    """started_at and finished_at are None for a freshly created pending record."""
    record_type = RecordType(
        name="timestamps-pending",
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


@pytest.mark.asyncio
async def test_record_read_model_validate_defaults_allowed_commands(
    test_session, test_user, test_patient, test_study
):
    """model_validate(record) — not via record_read_for — still works: allowed_commands defaults to []."""
    record_type = RecordType(
        name="timestamps-allowed-commands",
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
    await test_session.refresh(record, ["patient", "study", "record_type"])

    read = RecordRead.model_validate(record, from_attributes=True)
    assert read.allowed_commands == []


# ---------------------------------------------------------------------------
# Group 1b: the direct-write guard on Record.status / Record.user_id
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def saved_record(test_session, test_patient, test_study, test_series):
    test_session.add(make_record_type("guard-rt", unique_by=None))
    await test_session.commit()
    return await seed_record(
        test_session,
        test_patient.id,
        test_study.study_uid,
        test_series.series_uid,
        "guard-rt",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attr", "value"), [("status", RecordStatus.finished), ("user_id", uuid4())]
)
async def test_a_saved_records_lifecycle_columns_refuse_assignment(
    test_session, saved_record, attr, value
):
    with pytest.raises(DirectRecordWriteError, match=attr):
        setattr(saved_record, attr, value)


@pytest.mark.asyncio
async def test_generic_field_updates_cannot_bypass_the_guard(test_session, saved_record):
    # Captured before rollback: rollback() expires saved_record, and a bare
    # attribute access on an expired instance outside an async context raises
    # MissingGreenlet.
    record_id = saved_record.id
    with pytest.raises(DirectRecordWriteError):
        await RecordRepository(test_session).update_fields(
            record_id, {"status": RecordStatus.finished}
        )
    await test_session.rollback()
    fresh = await RecordRepository(test_session).get_with_relations(
        record_id, populate_existing=True
    )
    assert fresh.status != RecordStatus.finished


def test_a_new_record_may_set_its_status_and_owner():
    record = Record(
        patient_id="P", record_type_name="rt-name", status=RecordStatus.finished, user_id=uuid4()
    )
    assert (record.status, record.started_at, record.finished_at) == (
        RecordStatus.finished,
        None,
        None,
    )


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
    patient = make_patient("CRIT_PAT001", "Criteria Patient", anon_name="ANON_CRIT_001")
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
        name="rt-alpha-criteria",
        description="alpha",
        level=DicomQueryLevel.SERIES,
    )
    rt_beta = RecordType(
        name="rt-beta-criteria",
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
