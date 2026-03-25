"""Tests for build_slicer_context() — Slicer script context builder.

Covers:
- Standard variables (working_folder, study_uid, series_uid) by level
- File paths from file_registry (FileDefinition names → resolved paths)
- output_file convenience alias (first OUTPUT file)
- Cross-level file resolution (e.g. master_model at PATIENT level)
- Custom slicer_script_args override
- Unresolved template warning
"""

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.models.patient import PatientBase
from clarinet.models.record import RecordRead
from clarinet.models.record_type import RecordTypeRead
from clarinet.models.study import SeriesBase, StudyBase
from clarinet.services.slicer.context import build_slicer_context, build_slicer_context_async

TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_record_read(
    *,
    level: DicomQueryLevel = DicomQueryLevel.STUDY,
    file_registry: list[FileDefinitionRead] | None = None,
    slicer_script_args: dict[str, str] | None = None,
    slicer_result_validator_args: dict[str, str] | None = None,
    patient_id: str = "PAT001",
    patient_anon_id: str | None = "CLARINET_1",
    patient_anon_name: str | None = "ANON_NAME",
    study_uid: str | None = "1.2.3.4",
    study_anon_uid: str | None = "ANON_STUDY",
    series_uid: str | None = None,
    series_anon_uid: str | None = None,
    user_id: UUID | None = TEST_USER_ID,
) -> RecordRead:
    """Build a minimal RecordRead for testing."""
    patient = PatientBase(
        id=patient_id,
        name="Test Patient",
        anon_name=patient_anon_name,
        auto_id=1 if patient_anon_id else None,
    )

    study = None
    if study_uid:
        study = StudyBase(
            study_uid=study_uid,
            patient_id=patient_id,
            anon_uid=study_anon_uid,
            date=date(2024, 1, 1),
        )

    series = None
    if series_uid:
        series = SeriesBase(
            series_uid=series_uid,
            study_uid=study_uid or "",
            series_number=1,
            anon_uid=series_anon_uid,
        )

    record_type = RecordTypeRead(
        name="test-type",
        description="Test",
        label="Test",
        level=level,
        file_registry=file_registry or [],
        slicer_script="test.py",
        slicer_script_args=slicer_script_args,
        slicer_result_validator_args=slicer_result_validator_args,
    )

    return RecordRead(
        id=1,
        patient_id=patient_id,
        patient=patient,
        study_uid=study_uid,
        study=study,
        series_uid=series_uid,
        series=series,
        record_type_name="test-type",
        record_type=record_type,
        status="pending",
        user_id=user_id,
        clarinet_storage_path="/storage",
    )


# ---------------------------------------------------------------------------
# Standard variables
# ---------------------------------------------------------------------------


@patch("clarinet.services.slicer.context.settings")
def test_standard_vars_study_level(mock_settings):
    """STUDY level → working_folder + study_uid present."""
    mock_settings.storage_path = "/storage"

    record = _make_record_read(level=DicomQueryLevel.STUDY)
    ctx = build_slicer_context(record)

    assert ctx["working_folder"] == str(Path("/storage/CLARINET_1/ANON_STUDY"))
    assert ctx["study_uid"] == "ANON_STUDY"
    assert "series_uid" not in ctx


@patch("clarinet.services.slicer.context.settings")
def test_standard_vars_series_level(mock_settings):
    """SERIES level → working_folder + study_uid + series_uid present."""
    mock_settings.storage_path = "/storage"

    record = _make_record_read(
        level=DicomQueryLevel.SERIES,
        study_uid="1.2.3.4",
        study_anon_uid="ANON_STUDY",
        series_uid="1.2.3.4.5",
        series_anon_uid="ANON_SERIES",
    )
    ctx = build_slicer_context(record)

    assert ctx["working_folder"] == str(Path("/storage/CLARINET_1/ANON_STUDY/ANON_SERIES"))
    assert ctx["study_uid"] == "ANON_STUDY"
    assert ctx["series_uid"] == "ANON_SERIES"


@patch("clarinet.services.slicer.context.settings")
def test_standard_vars_patient_level(mock_settings):
    """PATIENT level → working_folder only (no study_uid/series_uid)."""
    mock_settings.storage_path = "/storage"

    record = _make_record_read(
        level=DicomQueryLevel.PATIENT,
        study_uid=None,
        study_anon_uid=None,
    )
    ctx = build_slicer_context(record)

    assert ctx["working_folder"] == str(Path("/storage/CLARINET_1"))
    assert "study_uid" not in ctx
    assert "series_uid" not in ctx


# ---------------------------------------------------------------------------
# File paths from file_registry
# ---------------------------------------------------------------------------


@patch("clarinet.services.slicer.context.settings")
def test_file_paths_from_registry(mock_settings):
    """FileDefinition names → resolved absolute paths in context."""
    mock_settings.storage_path = "/storage"

    seg_fd = FileDefinitionRead(
        name="segmentation_single",
        pattern="segmentation_single_{user_id}.seg.nrrd",
        role=FileRole.OUTPUT,
    )
    record = _make_record_read(
        level=DicomQueryLevel.STUDY,
        file_registry=[seg_fd],
    )
    ctx = build_slicer_context(record)

    expected = str(
        Path(f"/storage/CLARINET_1/ANON_STUDY/segmentation_single_{TEST_USER_ID}.seg.nrrd")
    )
    assert ctx["segmentation_single"] == expected


@patch("clarinet.services.slicer.context.settings")
def test_output_file_alias(mock_settings):
    """First OUTPUT file → output_file convenience alias."""
    mock_settings.storage_path = "/storage"

    input_fd = FileDefinitionRead(
        name="master_model",
        pattern="master_model.seg.nii",
        role=FileRole.INPUT,
        level=DicomQueryLevel.PATIENT,
    )
    output_fd = FileDefinitionRead(
        name="master_projection",
        pattern="master_projection.seg.nrrd",
        role=FileRole.OUTPUT,
    )
    record = _make_record_read(
        level=DicomQueryLevel.SERIES,
        file_registry=[input_fd, output_fd],
        series_uid="1.2.3.4.5",
        series_anon_uid="ANON_SERIES",
    )
    ctx = build_slicer_context(record)

    expected_output = str(
        Path("/storage/CLARINET_1/ANON_STUDY/ANON_SERIES/master_projection.seg.nrrd")
    )
    assert ctx["output_file"] == expected_output
    assert ctx["master_projection"] == expected_output


@patch("clarinet.services.slicer.context.settings")
def test_cross_level_file_resolution(mock_settings):
    """master_model (PATIENT level) resolved from SERIES-level record."""
    mock_settings.storage_path = "/storage"

    master_fd = FileDefinitionRead(
        name="master_model",
        pattern="master_model.seg.nii",
        role=FileRole.INPUT,
        level=DicomQueryLevel.PATIENT,
    )
    record = _make_record_read(
        level=DicomQueryLevel.SERIES,
        file_registry=[master_fd],
        series_uid="1.2.3.4.5",
        series_anon_uid="ANON_SERIES",
    )
    ctx = build_slicer_context(record)

    # master_model is PATIENT level, so resolved at patient dir
    expected = str(Path("/storage/CLARINET_1/master_model.seg.nii"))
    assert ctx["master_model"] == expected


# ---------------------------------------------------------------------------
# Custom args override
# ---------------------------------------------------------------------------


@patch("clarinet.services.slicer.context.settings")
def test_custom_args_override(mock_settings):
    """Custom slicer_script_args override auto-injected values."""
    mock_settings.storage_path = "/storage"

    record = _make_record_read(
        level=DicomQueryLevel.SERIES,
        slicer_script_args={
            "target_study_uid": "{study_anon_uid}",
            "custom_var": "static_value",
        },
        series_uid="1.2.3.4.5",
        series_anon_uid="ANON_SERIES",
    )
    ctx = build_slicer_context(record)

    assert ctx["target_study_uid"] == "ANON_STUDY"
    assert ctx["custom_var"] == "static_value"


# ---------------------------------------------------------------------------
# Unresolved template warning
# ---------------------------------------------------------------------------


@patch("clarinet.services.slicer.context.settings")
def test_unresolved_template_skipped(mock_settings):
    """Unknown placeholder in custom args → key skipped (warning logged via loguru)."""
    mock_settings.storage_path = "/storage"

    record = _make_record_read(
        level=DicomQueryLevel.STUDY,
        slicer_script_args={
            "bad_var": "{nonexistent_placeholder}",
            "good_var": "static_value",
        },
    )
    ctx = build_slicer_context(record)

    # Unresolvable key should not be in context
    assert "bad_var" not in ctx
    # Resolvable key should still be present
    assert ctx["good_var"] == "static_value"


# ---------------------------------------------------------------------------
# No output files → no output_file key
# ---------------------------------------------------------------------------


@patch("clarinet.services.slicer.context.settings")
def test_no_output_file_when_no_outputs(mock_settings):
    """No OUTPUT files in registry → output_file key absent."""
    mock_settings.storage_path = "/storage"

    input_fd = FileDefinitionRead(
        name="some_input",
        pattern="input.nrrd",
        role=FileRole.INPUT,
    )
    record = _make_record_read(
        level=DicomQueryLevel.STUDY,
        file_registry=[input_fd],
    )
    ctx = build_slicer_context(record)

    assert "output_file" not in ctx
    assert "some_input" in ctx


# ---------------------------------------------------------------------------
# Async context builder (no hydrators)
# ---------------------------------------------------------------------------


@patch("clarinet.services.slicer.context.settings")
def test_origin_type_from_parent(mock_settings):
    """origin_type in file patterns resolved from parent when provided."""
    mock_settings.storage_path = "/storage"
    mock_settings.pacs_host = "localhost"
    mock_settings.pacs_port = 4242
    mock_settings.pacs_aet = "ORTHANC"
    mock_settings.pacs_calling_aet = "SLICER"
    mock_settings.pacs_prefer_cget = True
    mock_settings.pacs_move_aet = "SLICER"

    seg_fd = FileDefinitionRead(
        name="segmentation",
        pattern="segmentation_{origin_type}_{user_id}.seg.nrrd",
        role=FileRole.OUTPUT,
    )
    record = _make_record_read(
        level=DicomQueryLevel.STUDY,
        file_registry=[seg_fd],
    )

    parent = _make_record_read(level=DicomQueryLevel.STUDY)
    parent_rt = RecordTypeRead(
        name="parent-seg",
        level=DicomQueryLevel.STUDY,
        file_registry=[],
    )
    parent = parent.model_copy(update={"record_type": parent_rt, "record_type_name": "parent-seg"})

    ctx = build_slicer_context(record, parent=parent)

    expected = str(
        Path(f"/storage/CLARINET_1/ANON_STUDY/segmentation_parent-seg_{TEST_USER_ID}.seg.nrrd")
    )
    assert ctx["segmentation"] == expected


@pytest.mark.asyncio
@patch("clarinet.services.slicer.context.settings")
async def test_build_slicer_context_async_no_hydrators(mock_settings):
    """build_slicer_context_async without hydrators returns same as sync."""
    mock_settings.storage_path = "/storage"

    record = _make_record_read(level=DicomQueryLevel.STUDY)
    mock_session = AsyncMock()

    ctx = await build_slicer_context_async(record, mock_session)

    assert ctx["working_folder"] == str(Path("/storage/CLARINET_1/ANON_STUDY"))
    assert ctx["study_uid"] == "ANON_STUDY"
    assert "series_uid" not in ctx
