"""Tests for RecordRead._format_path(), _get_working_folder(), and validate_record_files().

Covers:
- _format_path with all relations loaded (via RecordRead)
- Anon UID preference over real UIDs
- Patient anon_id from auto_id
- Invalid template handling
- working_folder for SERIES/STUDY/PATIENT levels
- validate_record_files with no input file definitions
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
from clarinet.repositories.record_repository import RecordRepository
from clarinet.services.file_validation import validate_record_files
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
async def rt_series_with_slicer_args(test_session):
    """SERIES-level RecordType carrying both slicer arg variants — for parity
    tests between FileRepository.slicer_args and RecordRead.slicer_*_args_formatted.
    """
    rt = RecordType(
        name="parity-rt",
        description="Parity test RT",
        label="Parity RT",
        level=DicomQueryLevel.SERIES,
        slicer_script_args={
            "input": "{working_folder}/input.nrrd",
            "patient": "{patient_id}",
            "study": "{study_uid}",
        },
        slicer_result_validator_args={
            "check": "{working_folder}/check.json",
            "series": "{series_uid}",
        },
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


# ===========================================================================
# Group 2: _get_working_folder (underlying logic of the legacy
# ``working_folder`` computed field — now exposed only as a helper method;
# routers/services compute paths through ``FileRepository``).
# ===========================================================================


@pytest.mark.asyncio
async def test_working_folder_series_level(
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

    expected = str(
        Path(settings.storage_path)
        / f"{settings.anon_id_prefix}_42"
        / "ANON_STUDY_WF"
        / "ANON_SERIES_WF"
    )
    assert record_read._get_working_folder(fallback_to_unanonymized=True) == expected


@pytest.mark.asyncio
async def test_working_folder_study_level(
    test_session, patient_with_anon, study_with_anon, rt_study
):
    """STUDY level → storage_path/patient_id/study_anon."""
    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        study=study_with_anon,
        record_type=rt_study,
    )

    expected = str(Path(settings.storage_path) / f"{settings.anon_id_prefix}_42" / "ANON_STUDY_WF")
    assert record_read._get_working_folder(fallback_to_unanonymized=True) == expected


@pytest.mark.asyncio
async def test_working_folder_patient_level(test_session, patient_with_anon, rt_patient):
    """PATIENT level → storage_path/patient_id."""
    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        record_type=rt_patient,
    )

    expected = str(Path(settings.storage_path) / f"{settings.anon_id_prefix}_42")
    assert record_read._get_working_folder(fallback_to_unanonymized=True) == expected


# ===========================================================================
# Group 3: validate_record_files
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
# Group 4: _format_path_strict
# ===========================================================================


@pytest.mark.asyncio
async def test_format_path_strict_raises_on_invalid_template(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """_format_path_strict with unknown placeholder → KeyError propagates."""
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

    with pytest.raises(KeyError):
        record_read._format_path_strict("{bad_placeholder}/path")


@pytest.mark.asyncio
async def test_format_path_strict_raises_when_study_not_anonymized(
    test_session, test_patient, study_without_anon, series_without_anon, rt_series
):
    """Backend safe mode (default): missing study.anon_uid → AnonPathError."""
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

    with pytest.raises(AnonPathError, match="Study has no anon_uid"):
        record_read._format_path_strict("{study_anon_uid}/file")


@pytest.mark.asyncio
async def test_format_path_strict_fallback_uses_raw_uids(
    test_session, test_patient, study_without_anon, series_without_anon, rt_series
):
    """Explicit ``fallback_to_unanonymized=True`` substitutes raw UIDs instead."""
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

    rendered = record_read._format_path_strict(
        "{study_anon_uid}/{series_anon_uid}", fallback_to_unanonymized=True
    )
    assert rendered == f"{study_without_anon.study_uid}/{series_without_anon.series_uid}"


@pytest.mark.asyncio
async def test_series_read_format_path_strict_raises_when_unanon(
    test_session, study_without_anon, series_without_anon
):
    """SeriesRead._format_path_strict mirrors the RecordRead safe-by-default contract."""
    from clarinet.models.study import SeriesRead
    from clarinet.repositories.series_repository import SeriesRepository

    repo = SeriesRepository(test_session)
    loaded = await repo.get_with_relations(series_without_anon.series_uid)
    series_read = SeriesRead.model_validate(loaded)
    with pytest.raises(AnonPathError, match="Study has no anon_uid"):
        series_read._format_path_strict("{study_anon_uid}/file")


@pytest.mark.asyncio
async def test_working_folder_type_is_str(
    test_session,
    patient_with_anon,
    study_with_anon,
    series_with_anon,
    rt_series,
    rt_study,
    rt_patient,
):
    """``_get_working_folder`` returns ``str`` (not ``None``) for all three levels."""
    # SERIES level
    rec_series = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
        rt_name=rt_series.name,
    )
    # STUDY level
    rec_study = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=None,
        rt_name=rt_study.name,
    )
    # PATIENT level
    rec_patient = await _create_record(
        test_session,
        patient_id=patient_with_anon.id,
        study_uid=None,
        series_uid=None,
        rt_name=rt_patient.name,
    )

    repo = RecordRepository(test_session)
    for rec in (rec_series, rec_study, rec_patient):
        loaded = await repo.get_with_relations(rec.id)
        record_read = RecordRead.model_validate(loaded)
        assert isinstance(record_read._get_working_folder(fallback_to_unanonymized=True), str)


@pytest.mark.asyncio
async def test_format_slicer_kwargs_includes_working_folder(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """``_format_slicer_kwargs`` resolves ``{working_folder}`` substitution.

    The legacy ``slicer_all_args_formatted`` aggregator was removed with the
    other path-related computed fields; the helper ``_format_slicer_kwargs``
    is the building block ``build_slicer_context`` (and Phase 5
    ``FileRepository.slicer_args``) rely on.
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

    wf = record_read._get_working_folder(fallback_to_unanonymized=True)
    rendered = record_read._format_slicer_kwargs(
        {"out": "{working_folder}/result.nrrd"},
        {"working_folder": wf},
    )
    assert rendered["out"] == f"{wf}/result.nrrd"


# ===========================================================================
# Group 5: per-record clarinet_storage_path override and custom disk_path_template
#
# Phase 0 safety net: these surfaces are silently exercised by production
# (``RecordRead._get_working_folder`` uses ``self.clarinet_storage_path or
# settings.storage_path``; ``settings.disk_path_template`` drives the
# layout) but had no regression test, so the FileRepository refactor could
# drop them without detection. Parametrized matrix detects any divergence
# between ``_get_working_folder`` and the underlying
# ``render_working_folder()`` as the path-resolution surface keeps moving
# into ``FileRepository``.
# ===========================================================================


@pytest.mark.asyncio
async def test_working_folder_uses_per_record_clarinet_storage_path_override(
    test_session, patient_with_anon, study_with_anon, series_with_anon, rt_series
):
    """``Record.clarinet_storage_path`` overrides ``settings.storage_path``.

    Per-record override exists only on ``RecordRead`` — ``SeriesRead`` paths
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

    expected = str(
        Path(custom_storage) / f"{settings.anon_id_prefix}_42" / "ANON_STUDY_WF" / "ANON_SERIES_WF"
    )
    rendered = record_read._get_working_folder(fallback_to_unanonymized=True)
    assert rendered == expected
    # Settings-level storage_path must NOT be used when override is set.
    assert not rendered.startswith(str(settings.storage_path))


@pytest.mark.asyncio
async def test_working_folder_falls_back_to_settings_storage_path_when_override_none(
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
    rendered = record_read._get_working_folder(fallback_to_unanonymized=True)
    assert rendered.startswith(str(settings.storage_path))


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
async def test_working_folder_respects_custom_disk_path_template(
    test_session,
    patient_with_anon,
    study_with_anon,
    series_with_anon,
    rt_series,
    monkeypatch,
    template,
    expected_segments,
):
    """``settings.disk_path_template`` controls the layout at SERIES level.

    Four template variants exercise:
    - Default layout (regression baseline).
    - ``series_modality`` placeholder (renders ``"unknown"`` when None).
    - ``series_num`` placeholder in the series segment.
    - ``anon_id_prefix`` + ``patient_auto_id`` + raw ``study_uid`` (mix of anon
      and raw placeholders).

    Without this matrix the upcoming refactor could silently drop placeholder
    substitution for any non-anon field (patient_auto_id, series_num,
    series_modality, study_uid).
    """
    # Patch the shared ``settings`` singleton directly — both
    # ``clarinet.models.record`` and ``clarinet.services.common.storage_paths``
    # import it by the same identity, so the mutation propagates regardless
    # of which module the call site reads from.
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

    expected = str(Path(settings.storage_path).joinpath(*expected_segments))
    assert record_read._get_working_folder(fallback_to_unanonymized=True) == expected


@pytest.mark.asyncio
async def test_slicer_args_working_folder_placeholder(
    test_session, patient_with_anon, study_with_anon
):
    """``_format_slicer_kwargs`` substitutes ``{working_folder}`` placeholder.

    Exercises the helper directly (since the legacy
    ``slicer_args_formatted`` computed field is gone — Phase 5 will route
    Slicer-context construction through ``FileRepository.slicer_args``).
    """
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

    wf = record_read._get_working_folder(fallback_to_unanonymized=True)
    args = record_read._format_slicer_kwargs(
        record_read.record_type.slicer_script_args, {"working_folder": wf}
    )
    assert "output_path" in args
    expected_wf = str(
        Path(settings.storage_path) / f"{settings.anon_id_prefix}_42" / "ANON_STUDY_WF"
    )
    # The "/" in "{working_folder}/output.nrrd" comes from the user-defined
    # slicer kwarg template, not from a real filesystem join — it survives
    # ``str.format`` verbatim, hence the literal "/" here (cross-platform OK).
    assert args["output_path"] == f"{expected_wf}/output.nrrd"
    assert args["study_uid"] == "ANON_STUDY_WF"


# ===========================================================================
# Group 6: FileRepository.slicer_args ≡ RecordRead._format_slicer_kwargs
#
# Phase 1 acceptance criterion (file-repo roadmap): snapshot parity with the
# legacy computed fields. After Phase 4 those computed fields are gone, so
# the parity check is now against the underlying ``_format_slicer_kwargs``
# helper that ``build_slicer_context`` (and Phase 5
# ``FileRepository.slicer_args``) both read from. Without this parity proof,
# Phase 5 could silently regress the slicer-arg surface.
# ===========================================================================


@pytest.mark.asyncio
async def test_file_repository_slicer_args_matches_format_slicer_kwargs_script(
    test_session,
    patient_with_anon,
    study_with_anon,
    series_with_anon,
    rt_series_with_slicer_args,
):
    from clarinet.repositories import FileRepository
    from tests.utils.test_helpers import RecordFactory

    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        study=study_with_anon,
        series=series_with_anon,
        record_type=rt_series_with_slicer_args,
    )
    wf = str(FileRepository(record_read).working_dir)
    legacy = record_read._format_slicer_kwargs(
        record_read.record_type.slicer_script_args, {"working_folder": wf}
    )
    new = FileRepository(record_read).slicer_args(validator=False)
    assert new == legacy


@pytest.mark.asyncio
async def test_file_repository_slicer_args_matches_format_slicer_kwargs_validator(
    test_session,
    patient_with_anon,
    study_with_anon,
    series_with_anon,
    rt_series_with_slicer_args,
):
    from clarinet.repositories import FileRepository
    from tests.utils.test_helpers import RecordFactory

    record_read = await RecordFactory.create_record_with_relations(
        test_session,
        patient=patient_with_anon,
        study=study_with_anon,
        series=series_with_anon,
        record_type=rt_series_with_slicer_args,
    )
    wf = str(FileRepository(record_read).working_dir)
    legacy = record_read._format_slicer_kwargs(
        record_read.record_type.slicer_result_validator_args, {"working_folder": wf}
    )
    new = FileRepository(record_read).slicer_args(validator=True)
    assert new == legacy
