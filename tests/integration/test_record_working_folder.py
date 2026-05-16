"""Tests for path resolution through ``FileRepository`` + ``render_slicer_args``.

The legacy ``RecordRead._format_path*`` / ``_format_slicer_kwargs`` /
``_get_working_folder`` helpers were removed in the FileRepository
refactor (Phase 3). This file now exercises the same path-resolution
surface via:

- ``FileRepository(record).working_dir`` (replaces ``_get_working_folder``)
- ``clarinet.services.slicer.args.render_slicer_args`` (replaces
  ``_format_slicer_kwargs``)
- ``validate_record_files`` (unchanged — still the public API)

Covers SERIES/STUDY/PATIENT levels, per-record
``clarinet_storage_path`` override, and a small matrix of custom
``disk_path_template`` settings.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from clarinet.exceptions.domain import AnonPathError
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.file_schema import FileDefinition, FileRole, RecordTypeFileLink
from clarinet.models.patient import Patient
from clarinet.models.record import Record, RecordRead, RecordType
from clarinet.models.study import Series, Study
from clarinet.repositories import FileRepository
from clarinet.repositories.record_repository import RecordRepository
from clarinet.services.file_validation import validate_record_files
from clarinet.services.slicer.args import render_slicer_args
from clarinet.settings import settings
from tests.utils.test_helpers import RecordFactory

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
        name="wf-test-series",
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
        name="wf-test-study",
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
        name="wf-test-patient",
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
    """SERIES-level RecordType with input file definitions via M2M links."""
    rt = RecordType(
        name="wf-test-with-files",
        description="Series level with input files",
        label="WF Files",
        level=DicomQueryLevel.SERIES,
    )
    test_session.add(rt)
    await test_session.flush()

    fd = FileDefinition(name="master", pattern="master.nrrd")
    test_session.add(fd)
    await test_session.flush()

    link = RecordTypeFileLink(
        record_type_name=rt.name,
        file_definition_id=fd.id,
        role=FileRole.INPUT,
        required=True,
    )
    test_session.add(link)
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
# Group 1: FileRepository.working_dir (replaces _get_working_folder)
# ===========================================================================


@pytest.mark.asyncio
async def test_working_dir_series_level(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """SERIES level → storage_path/patient_id/study_anon/series_anon."""
    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        study=study_with_anon,
        series=series_with_anon,
        record_type=rt_series,
    )

    expected = (
        Path(settings.storage_path)
        / f"{settings.anon_id_prefix}_42"
        / "ANON_STUDY_WF"
        / "ANON_SERIES_WF"
    )
    assert FileRepository(record_read).working_dir == expected


@pytest.mark.asyncio
async def test_working_dir_study_level(test_session, patient_with_anon, study_with_anon, rt_study):
    """STUDY level → storage_path/patient_id/study_anon."""
    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        study=study_with_anon,
        record_type=rt_study,
    )

    expected = Path(settings.storage_path) / f"{settings.anon_id_prefix}_42" / "ANON_STUDY_WF"
    assert FileRepository(record_read).working_dir == expected


@pytest.mark.asyncio
async def test_working_dir_patient_level(test_session, patient_with_anon, rt_patient):
    """PATIENT level → storage_path/patient_id."""
    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        record_type=rt_patient,
    )

    expected = Path(settings.storage_path) / f"{settings.anon_id_prefix}_42"
    assert FileRepository(record_read).working_dir == expected


@pytest.mark.asyncio
async def test_working_dir_strict_raises_on_unanonymized_study(
    test_session, test_patient, study_without_anon, series_without_anon, rt_series
):
    """FileRepository is strict by default — unanon study → AnonPathError."""
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

    with pytest.raises(AnonPathError, match=r"Study|Series has no anon_uid"):
        FileRepository(record_read)


# ===========================================================================
# Group 2: validate_record_files
# ===========================================================================


@pytest.mark.asyncio
async def test_validate_record_files_no_input_files(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """record_type.file_registry has no input files → returns None."""
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

    # rt_series has no file_links (no input file definitions)
    result = await validate_record_files(record_read)
    assert result is None


# ===========================================================================
# Group 3: per-record clarinet_storage_path override and custom
# disk_path_template (regression coverage for the FileResolver surface)
# ===========================================================================


@pytest.mark.asyncio
async def test_working_dir_uses_per_record_clarinet_storage_path_override(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """``Record.clarinet_storage_path`` overrides ``settings.storage_path``.

    Per-record override exists only on ``Record`` — ``Series``-derived paths
    always use ``settings.storage_path`` (intentional asymmetry).
    """
    custom_storage = "/custom/storage/root"
    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        study=study_with_anon,
        series=series_with_anon,
        record_type=rt_series,
        clarinet_storage_path=custom_storage,
    )

    expected = (
        Path(custom_storage) / f"{settings.anon_id_prefix}_42" / "ANON_STUDY_WF" / "ANON_SERIES_WF"
    )
    rendered = FileRepository(record_read).working_dir
    assert rendered == expected
    # Settings-level storage_path must NOT be used when override is set.
    assert not str(rendered).startswith(str(settings.storage_path))


@pytest.mark.asyncio
async def test_working_dir_falls_back_to_settings_storage_path_when_override_none(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """Without per-record override, ``settings.storage_path`` is used."""
    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        study=study_with_anon,
        series=series_with_anon,
        record_type=rt_series,
        # clarinet_storage_path intentionally omitted (None)
    )

    assert record_read.clarinet_storage_path is None
    rendered = FileRepository(record_read).working_dir
    assert str(rendered).startswith(str(settings.storage_path))


@pytest.mark.parametrize(
    ("template", "expected_segments"),
    [
        # Default template — anon_patient_id / anon_study_uid / anon_series_uid
        (
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
            ("CLARINET_42", "ANON_STUDY_WF", "ANON_SERIES_WF"),
        ),
        # Custom: series_modality prefix on the series segment (modality
        # is None on the fixture → renders as "unknown").
        (
            "{anon_patient_id}/{anon_study_uid}/{series_modality}_{anon_series_uid}",
            ("CLARINET_42", "ANON_STUDY_WF", "unknown_ANON_SERIES_WF"),
        ),
        # Custom: series_num prefix on the series segment
        (
            "{anon_patient_id}/{anon_study_uid}/{series_num}_{anon_series_uid}",
            ("CLARINET_42", "ANON_STUDY_WF", "00001_ANON_SERIES_WF"),
        ),
        # Custom: anon_id_prefix + patient_auto_id, raw study_uid
        (
            "{anon_id_prefix}_{patient_auto_id}/{study_uid}/{anon_series_uid}",
            ("CLARINET_42", "1.2.840.10008.1.1.1", "ANON_SERIES_WF"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_working_dir_respects_custom_disk_path_template(
    test_session,
    patient_with_anon,
    study_with_anon,
    series_with_anon,
    rt_series,
    monkeypatch,
    template,
    expected_segments,
):
    """``settings.disk_path_template`` controls the layout at SERIES level."""
    monkeypatch.setattr(settings, "disk_path_template", template)

    # series_with_anon is created without ``modality`` — pin the
    # expectation so a future fixture tweak (e.g. setting modality="CT")
    # doesn't silently shift the parametrized expected value.
    assert series_with_anon.modality is None

    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        study=study_with_anon,
        series=series_with_anon,
        record_type=rt_series,
    )

    expected = Path(settings.storage_path).joinpath(*expected_segments)
    assert FileRepository(record_read).working_dir == expected


# ===========================================================================
# Group 4: render_slicer_args — working_folder substitution
# ===========================================================================


@pytest.mark.asyncio
async def test_render_slicer_args_substitutes_working_folder(
    test_session, patient_with_anon, study_with_anon, series_with_anon
):
    """``render_slicer_args`` substitutes ``{working_folder}`` placeholder."""
    rt = RecordType(
        name="wf-test-slicer-args",
        description="Test working_folder in slicer args",
        label="WF Slicer Args",
        level=DicomQueryLevel.STUDY,
        slicer_script_args={
            "output_path": "{working_folder}/output.nrrd",
            "study_uid": "{study_anon_uid}",
        },
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)

    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=None,
        rt_name=rt.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    args = render_slicer_args(record_read)
    assert args is not None
    expected_wf = str(
        Path(settings.storage_path) / f"{settings.anon_id_prefix}_42" / "ANON_STUDY_WF"
    )
    # The "/" in "{working_folder}/output.nrrd" comes from the user-defined
    # slicer kwarg template, not from a real filesystem join — it survives
    # ``str.format`` verbatim, hence the literal "/" here (cross-platform OK).
    assert args["output_path"] == f"{expected_wf}/output.nrrd"
    assert args["study_uid"] == "ANON_STUDY_WF"


@pytest.mark.asyncio
async def test_render_slicer_args_validator_branch(
    test_session, patient_with_anon, study_with_anon, series_with_anon
):
    """``validator=True`` reads ``slicer_result_validator_args``."""
    rt = RecordType(
        name="wf-test-validator-args",
        description="Test validator branch",
        label="WF Validator",
        level=DicomQueryLevel.SERIES,
        slicer_script_args={"out": "{working_folder}/result.nrrd"},
        slicer_result_validator_args={"check": "{working_folder}/check.json"},
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)

    record = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
        rt_name=rt.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    wf = str(FileRepository(record_read).working_dir)
    script = render_slicer_args(record_read, validator=False)
    validator = render_slicer_args(record_read, validator=True)

    assert script == {"out": f"{wf}/result.nrrd"}
    assert validator == {"check": f"{wf}/check.json"}


@pytest.mark.asyncio
async def test_render_slicer_args_empty_when_unset(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """``render_slicer_args`` returns ``{}`` for a record type with no args.

    ``RecordType.slicer_script_args`` uses ``default_factory=dict`` so an
    unset RT comes back as ``{}`` (not ``None``). The renderer iterates
    the empty dict and returns ``{}`` — matches the legacy behaviour of
    ``RecordRead._format_slicer_kwargs``.
    """
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

    assert render_slicer_args(record_read) == {}
    assert render_slicer_args(record_read, validator=True) == {}


@pytest.mark.asyncio
async def test_render_slicer_args_strict_raises_on_unanon_record(
    test_session, test_patient, study_without_anon, series_without_anon
):
    """Strict mode: non-anon record with anon template → AnonPathError."""
    rt = RecordType(
        name="wf-test-strict-args",
        description="Strict mode test",
        label="WF Strict",
        level=DicomQueryLevel.SERIES,
        slicer_script_args={"x": "{working_folder}/x.json"},
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)

    record = await _create_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=study_without_anon.study_uid,
        series_uid=series_without_anon.series_uid,
        rt_name=rt.name,
    )

    repo = RecordRepository(test_session)
    loaded = await repo.get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    with pytest.raises(AnonPathError):
        render_slicer_args(record_read)
