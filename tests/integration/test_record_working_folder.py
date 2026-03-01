"""Tests for RecordRead._format_path(), _get_working_folder(), and validate_record_files().

Covers:
- _format_path with all relations loaded (via RecordRead)
- Anon UID preference over real UIDs
- Patient anon_id from auto_id
- Invalid template handling
- working_folder for SERIES/STUDY/PATIENT levels
- validate_record_files with empty input_files
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from src.api.routers.record import validate_record_files
from src.models.base import DicomQueryLevel, RecordStatus
from src.models.patient import Patient
from src.models.record import Record, RecordRead, RecordType
from src.models.study import Series, Study
from src.repositories.record_repository import RecordRepository
from src.settings import settings

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def patient_with_anon(test_session):
    """Patient with auto_id set so anon_id returns 'CLARINET_42'."""
    patient = Patient(id="PAT_ANON_WF", name="Anon Patient", anon_name="ANON_WF001", auto_id=42)
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def study_with_anon(test_session, patient_with_anon):
    """Study with anon_uid set."""
    study = Study(
        patient_id=patient_with_anon.id,
        study_uid="1.2.840.10008.1.1.1",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_STUDY_WF",
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)
    return study


@pytest_asyncio.fixture
async def study_without_anon(test_session, test_patient):
    """Study without anon_uid (None)."""
    study = Study(
        patient_id=test_patient.id,
        study_uid="1.2.840.10008.2.2.2",
        date=datetime.now(UTC).date(),
        anon_uid=None,
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)
    return study


@pytest_asyncio.fixture
async def series_with_anon(test_session, study_with_anon):
    """Series with anon_uid set."""
    series = Series(
        study_uid=study_with_anon.study_uid,
        series_uid="1.2.840.10008.1.1.1.1",
        series_number=1,
        series_description="Anon Series",
        anon_uid="ANON_SERIES_WF",
    )
    test_session.add(series)
    await test_session.commit()
    await test_session.refresh(series)
    return series


@pytest_asyncio.fixture
async def series_without_anon(test_session, study_without_anon):
    """Series without anon_uid."""
    series = Series(
        study_uid=study_without_anon.study_uid,
        series_uid="1.2.840.10008.2.2.2.1",
        series_number=1,
        series_description="No Anon Series",
        anon_uid=None,
    )
    test_session.add(series)
    await test_session.commit()
    await test_session.refresh(series)
    return series


@pytest_asyncio.fixture
async def rt_series(test_session):
    """SERIES-level RecordType."""
    rt = RecordType(
        name="wf_test_series",
        description="Series level for working folder tests",
        label="WF Series",
        level=DicomQueryLevel.SERIES,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def rt_study(test_session):
    """STUDY-level RecordType."""
    rt = RecordType(
        name="wf_test_study",
        description="Study level for working folder tests",
        label="WF Study",
        level=DicomQueryLevel.STUDY,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def rt_patient(test_session):
    """PATIENT-level RecordType."""
    rt = RecordType(
        name="wf_test_patient",
        description="Patient level for working folder tests",
        label="WF Patient",
        level=DicomQueryLevel.PATIENT,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def rt_with_input_files(test_session):
    """SERIES-level RecordType with input_files defined."""
    rt = RecordType(
        name="wf_test_with_files",
        description="Series level with input files",
        label="WF Files",
        level=DicomQueryLevel.SERIES,
        input_files=[{"name": "master", "pattern": "master.nrrd"}],
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _create_record(session, *, patient_id, study_uid, series_uid, rt_name, **kwargs):
    """Create a Record, commit, and return it."""
    record = Record(
        patient_id=patient_id,
        study_uid=study_uid,
        series_uid=series_uid,
        record_type_name=rt_name,
        status=RecordStatus.pending,
        **kwargs,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


# ===========================================================================
# Group 1: _format_path
# ===========================================================================


@pytest.mark.asyncio
async def test_format_path_with_all_relations(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """All relations loaded via get_with_relations → correct path with anon UIDs."""
    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
        rt_name=rt_series.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    template = "{patient_id}/{study_anon_uid}/{series_anon_uid}"
    result = record_read._format_path(template)

    assert result == f"{settings.anon_id_prefix}_42/ANON_STUDY_WF/ANON_SERIES_WF"


@pytest.mark.asyncio
async def test_format_path_anon_uids_preferred(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """When relations loaded, anon UIDs from Study/Series preferred over real UIDs."""
    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
        rt_name=rt_series.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    # study_anon_uid and series_anon_uid should come from the relation objects
    template = "{study_anon_uid}/{series_anon_uid}"
    result = record_read._format_path(template)

    assert result == "ANON_STUDY_WF/ANON_SERIES_WF"
    # Confirm these differ from the real UIDs
    assert study_with_anon.study_uid != "ANON_STUDY_WF"
    assert series_with_anon.series_uid != "ANON_SERIES_WF"


@pytest.mark.asyncio
async def test_format_path_real_uid_fallback_when_no_anon(
    test_session, test_patient, study_without_anon, series_without_anon, rt_series
):
    """Study/Series without anon_uid → fallback to real study_uid/series_uid."""
    record = await _create_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=study_without_anon.study_uid,
        series_uid=series_without_anon.series_uid,
        rt_name=rt_series.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    template = "{study_anon_uid}/{series_anon_uid}"
    result = record_read._format_path(template)

    # No anon_uid on study/series → falls back to real UIDs
    assert result == f"{study_without_anon.study_uid}/{series_without_anon.series_uid}"


@pytest.mark.asyncio
async def test_format_path_invalid_template_returns_none(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """Template with {invalid_var} → None (KeyError caught)."""
    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
        rt_name=rt_series.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    result = record_read._format_path("{invalid_var}/path")
    assert result is None


@pytest.mark.asyncio
async def test_format_path_patient_anon_id_when_auto_id_set(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """Patient with auto_id=42 → {patient_id} resolves to 'CLARINET_42'."""
    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
        rt_name=rt_series.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    result = record_read._format_path("{patient_id}")
    assert result == f"{settings.anon_id_prefix}_42"


@pytest.mark.asyncio
async def test_format_path_patient_id_fallback_when_no_auto_id(
    test_session, test_patient, study_without_anon, series_without_anon, rt_series
):
    """Patient without auto_id → {patient_id} falls back to raw patient.id."""
    # test_patient from conftest has no auto_id set
    record = await _create_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=study_without_anon.study_uid,
        series_uid=series_without_anon.series_uid,
        rt_name=rt_series.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    result = record_read._format_path("{patient_id}")
    # anon_id is None (no auto_id) → falls back to self.patient_id
    assert result == test_patient.id


# ===========================================================================
# Group 2: _get_working_folder (underlying logic of working_folder computed field)
# ===========================================================================


@pytest.mark.asyncio
async def test_working_folder_series_level(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """SERIES level → storage_path/patient_id/study_anon/series_anon."""
    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
        rt_name=rt_series.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    expected = f"{settings.storage_path}/{settings.anon_id_prefix}_42/ANON_STUDY_WF/ANON_SERIES_WF"
    assert record_read.working_folder == expected


@pytest.mark.asyncio
async def test_working_folder_study_level(
    test_session, patient_with_anon, study_with_anon, rt_study
):
    """STUDY level → storage_path/patient_id/study_anon."""
    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=None,
        rt_name=rt_study.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    expected = f"{settings.storage_path}/{settings.anon_id_prefix}_42/ANON_STUDY_WF"
    assert record_read.working_folder == expected


@pytest.mark.asyncio
async def test_working_folder_patient_level(test_session, patient_with_anon, rt_patient):
    """PATIENT level → storage_path/patient_id."""
    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=None,
        series_uid=None,
        rt_name=rt_patient.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    expected = f"{settings.storage_path}/{settings.anon_id_prefix}_42"
    assert record_read.working_folder == expected


# ===========================================================================
# Group 3: validate_record_files
# ===========================================================================


@pytest.mark.asyncio
async def test_validate_record_files_no_input_files(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """record_type.input_files is empty → returns None."""
    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
        rt_name=rt_series.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    # rt_series has no input_files (empty list by default)
    result = validate_record_files(record_read)
    assert result is None
