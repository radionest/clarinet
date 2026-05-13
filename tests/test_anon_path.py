"""Unit tests for the configurable disk path resolver."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from clarinet.models.base import DicomQueryLevel
from clarinet.models.patient import Patient
from clarinet.models.study import Series, Study
from clarinet.services.dicom.anon_path import (
    AnonPathError,
    build_context,
    derive_anon_patient_id,
    render_working_folder,
    split_template,
    validate_template,
)


@pytest.fixture
def patient() -> Patient:
    return Patient(id="PAT001", name="John Doe", auto_id=42, anon_name="ANON12345")


@pytest.fixture
def study(patient: Patient) -> Study:
    return Study(
        study_uid="1.2.3.4",
        date=date(2026, 4, 15),
        study_description="CT chest",
        modalities_in_study="CT\\SR",
        patient_id=patient.id,
        anon_uid="9.9.9.9",
    )


@pytest.fixture
def series(study: Study) -> Series:
    return Series(
        series_uid="1.2.3.4.5",
        series_number=1,
        modality="CT",
        study_uid=study.study_uid,
        anon_uid="9.9.9.9.5",
    )


class TestSplitTemplate:
    def test_three_segments_ok(self) -> None:
        segs = split_template("a/b/c")
        assert (segs.patient, segs.study, segs.series) == ("a", "b", "c")

    def test_one_segment_rejected(self) -> None:
        with pytest.raises(AnonPathError, match="exactly 3"):
            split_template("a")

    def test_two_segments_rejected(self) -> None:
        with pytest.raises(AnonPathError, match="exactly 3"):
            split_template("a/b")

    def test_four_segments_rejected(self) -> None:
        with pytest.raises(AnonPathError, match="exactly 3"):
            split_template("a/b/c/d")

    def test_empty_segment_rejected(self) -> None:
        with pytest.raises(AnonPathError, match="exactly 3"):
            split_template("a//c")


class TestBuildContext:
    def test_default_per_patient_mode(self, patient: Patient, study: Study, series: Series) -> None:
        with patch("clarinet.services.dicom.anon_path.settings", spec_set=True) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            ctx = build_context(patient=patient, study=study, series=series)
        # anon_patient_id derives from patient.anon_id (= "{prefix}_{auto_id}")
        assert ctx["anon_patient_id"].startswith("CLARINET_")
        assert ctx["anon_study_uid"] == "9.9.9.9"
        assert ctx["anon_series_uid"] == "9.9.9.9.5"
        assert ctx["patient_id"] == "PAT001"
        assert ctx["patient_auto_id"] == "42"
        assert ctx["study_uid"] == "1.2.3.4"
        assert ctx["series_uid"] == "1.2.3.4.5"
        assert ctx["study_date"] == "20260415"
        assert ctx["study_modalities"] == "CT_SR"
        assert ctx["series_modality"] == "CT"

    def test_per_study_mode(self, patient: Patient, study: Study, series: Series) -> None:
        with patch("clarinet.services.dicom.anon_path.settings", spec_set=True) as mock_settings:
            mock_settings.anon_per_study_patient_id = True
            mock_settings.anon_per_study_patient_id_hex_length = 8
            mock_settings.anon_uid_salt = "salt"
            mock_settings.anon_id_prefix = "CLARINET"
            ctx = build_context(patient=patient, study=study, series=series)
        # per-study hash is 8 hex chars + "CLARINET_" prefix
        assert ctx["anon_patient_id"].startswith("CLARINET_")
        # Hex hash part should be 8 chars after prefix
        assert len(ctx["anon_patient_id"].split("_")[-1]) == 8

    def test_writer_overrides(self, patient: Patient, study: Study, series: Series) -> None:
        with patch("clarinet.services.dicom.anon_path.settings", spec_set=True) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "X"
            ctx = build_context(
                patient=patient,
                study=study,
                series=series,
                anon_patient_id="OVERRIDE",
                anon_study_uid="OS",
                anon_series_uid="OSS",
            )
        assert ctx["anon_patient_id"] == "OVERRIDE"
        assert ctx["anon_study_uid"] == "OS"
        assert ctx["anon_series_uid"] == "OSS"

    def test_missing_entities_yield_unknown(self) -> None:
        with patch("clarinet.services.dicom.anon_path.settings", spec_set=True) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "X"
            ctx = build_context(patient=None, study=None, series=None)
        assert ctx["patient_id"] == "unknown"
        assert ctx["study_uid"] == "unknown"
        assert ctx["series_uid"] == "unknown"
        assert ctx["study_modalities"] == "unknown"
        assert ctx["series_modality"] == "unknown"
        assert ctx["study_date"] == "unknown"
        assert ctx["patient_auto_id"] == "unknown"


class TestDeriveAnonPatientId:
    def test_per_patient_uses_anon_id(self, patient: Patient, study: Study) -> None:
        with patch("clarinet.services.dicom.anon_path.settings", spec_set=True) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            result = derive_anon_patient_id(patient, study)
        assert result == "CLARINET_42"

    def test_per_study_uses_hash(self, patient: Patient, study: Study) -> None:
        with patch("clarinet.services.dicom.anon_path.settings", spec_set=True) as mock_settings:
            mock_settings.anon_per_study_patient_id = True
            mock_settings.anon_per_study_patient_id_hex_length = 8
            mock_settings.anon_uid_salt = "salt"
            mock_settings.anon_id_prefix = "P"
            result = derive_anon_patient_id(patient, study)
        # Same salt+study_uid+length+prefix must be deterministic across calls
        with patch("clarinet.services.dicom.anon_path.settings", spec_set=True) as mock_settings:
            mock_settings.anon_per_study_patient_id = True
            mock_settings.anon_per_study_patient_id_hex_length = 8
            mock_settings.anon_uid_salt = "salt"
            mock_settings.anon_id_prefix = "P"
            result2 = derive_anon_patient_id(patient, study)
        assert result == result2
        assert result.startswith("P_")


class TestRenderWorkingFolder:
    def test_series_level(self) -> None:
        ctx = {
            "anon_patient_id": "CLARINET_42",
            "anon_study_uid": "9.9.9",
            "anon_series_uid": "9.9.9.5",
            "patient_id": "PAT",
            "patient_auto_id": "42",
            "anon_id_prefix": "CLARINET",
            "study_uid": "1.2",
            "series_uid": "1.2.5",
            "study_date": "20260415",
            "study_modalities": "CT_SR",
            "series_modality": "CT",
        }
        result = render_working_folder(
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
            DicomQueryLevel.SERIES,
            ctx,
            Path("/data"),
        )
        assert result == Path("/data/CLARINET_42/9.9.9/9.9.9.5")

    def test_study_level_only_two_segments(self) -> None:
        ctx = _full_ctx()
        result = render_working_folder(
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
            DicomQueryLevel.STUDY,
            ctx,
            Path("/data"),
        )
        assert result == Path("/data/CLARINET_42/9.9.9")

    def test_patient_level_only_one_segment(self) -> None:
        ctx = _full_ctx()
        result = render_working_folder(
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
            DicomQueryLevel.PATIENT,
            ctx,
            Path("/data"),
        )
        assert result == Path("/data/CLARINET_42")

    def test_custom_template_with_modalities_and_date(self) -> None:
        ctx = _full_ctx()
        result = render_working_folder(
            "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}",
            DicomQueryLevel.SERIES,
            ctx,
            Path("/data"),
        )
        assert result == Path("/data/42/CT_SR_20260415/9.9.9.5")

    def test_unknown_placeholder_raises(self) -> None:
        ctx = _full_ctx()
        with pytest.raises(AnonPathError, match="unknown placeholder"):
            render_working_folder(
                "{not_a_real_field}/{anon_study_uid}/{anon_series_uid}",
                DicomQueryLevel.SERIES,
                ctx,
                Path("/data"),
            )

    def test_rejects_dotdot_segment(self) -> None:
        ctx = _full_ctx()
        ctx["patient_auto_id"] = ".."
        with pytest.raises(AnonPathError, match="unsafe"):
            render_working_folder(
                "{patient_auto_id}/{anon_study_uid}/{anon_series_uid}",
                DicomQueryLevel.SERIES,
                ctx,
                Path("/data"),
            )

    def test_rejects_slash_in_rendered_segment(self) -> None:
        ctx = _full_ctx()
        ctx["patient_auto_id"] = "evil/path"
        with pytest.raises(AnonPathError, match="unsafe"):
            render_working_folder(
                "{patient_auto_id}/{anon_study_uid}/{anon_series_uid}",
                DicomQueryLevel.SERIES,
                ctx,
                Path("/data"),
            )


class TestValidateTemplate:
    def test_default_template_passes(self) -> None:
        result = validate_template("{anon_patient_id}/{anon_study_uid}/{anon_series_uid}")
        assert result == "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"

    def test_custom_modalities_date_passes(self) -> None:
        assert (
            validate_template("{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}")
            == "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"
        )

    def test_unknown_placeholder_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown placeholder"):
            validate_template("{patient_auto_id}/{not_a_field}/{series_uid}")

    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="relative"):
            validate_template("/abs/{study_uid}/{series_uid}")

    def test_two_segments_rejected(self) -> None:
        with pytest.raises(ValueError, match="exactly 3"):
            validate_template("{patient_id}/{study_uid}")

    def test_four_segments_rejected(self) -> None:
        with pytest.raises(ValueError, match="exactly 3"):
            validate_template("{patient_id}/{study_uid}/{series_uid}/extra")

    def test_dotdot_in_template_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"'\.\.'"):
            validate_template("../{study_uid}/{series_uid}")

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            validate_template("")

    def test_empty_placeholder_rejected(self) -> None:
        with pytest.raises((ValueError, IndexError, KeyError)):
            validate_template("{patient_id}/{}/{series_uid}")

    def test_unclosed_brace_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_template("{patient_id/{study_uid}/{series_uid}")

    def test_mixed_valid_invalid_in_segment(self) -> None:
        with pytest.raises(ValueError, match="unknown placeholder"):
            validate_template("{patient_id}_{bogus}/{study_uid}/{series_uid}")

    def test_duplicate_placeholder_passes(self) -> None:
        template = "{patient_id}_{patient_id}/{study_uid}/{series_uid}"
        assert validate_template(template) == template


class TestSeriesReadWorkingFolder:
    """SeriesRead.working_folder uses the resolver — no hardcoded layout."""

    def test_default_template_layout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clarinet.models.patient import PatientInfo
        from clarinet.models.study import SeriesRead, StudyRead

        monkeypatch.setattr("clarinet.services.dicom.anon_path.settings.storage_path", "/storage")
        monkeypatch.setattr(
            "clarinet.services.dicom.anon_path.settings.disk_path_template",
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
        )
        monkeypatch.setattr(
            "clarinet.services.dicom.anon_path.settings.anon_per_study_patient_id",
            False,
        )
        monkeypatch.setattr(
            "clarinet.services.dicom.anon_path.settings.anon_id_prefix",
            "CLARINET",
        )

        patient = PatientInfo(id="P1", name="John", auto_id=7)
        study = StudyRead(
            study_uid="1.2.3",
            date=date(2026, 1, 5),
            patient_id="P1",
            anon_uid="9.9.9",
            patient=patient,
            series=[],
        )
        series_read = SeriesRead(
            series_uid="1.2.3.4",
            series_number=1,
            modality="CT",
            anon_uid="9.9.9.4",
            study=study,
            records=[],
        )
        expected = str(Path("/storage") / "CLARINET_7" / "9.9.9" / "9.9.9.4")
        assert series_read.working_folder == expected

    def test_custom_template_uses_modalities_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clarinet.models.patient import PatientInfo
        from clarinet.models.study import SeriesRead, StudyRead

        monkeypatch.setattr("clarinet.services.dicom.anon_path.settings.storage_path", "/storage")
        monkeypatch.setattr(
            "clarinet.services.dicom.anon_path.settings.disk_path_template",
            "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}",
        )
        monkeypatch.setattr(
            "clarinet.services.dicom.anon_path.settings.anon_per_study_patient_id",
            False,
        )
        monkeypatch.setattr(
            "clarinet.services.dicom.anon_path.settings.anon_id_prefix",
            "CLARINET",
        )

        patient = PatientInfo(id="P1", name="John", auto_id=7)
        study = StudyRead(
            study_uid="1.2.3",
            date=date(2026, 1, 5),
            patient_id="P1",
            modalities_in_study="CT\\PT",
            anon_uid="9.9.9",
            patient=patient,
            series=[],
        )
        series_read = SeriesRead(
            series_uid="1.2.3.4",
            series_number=1,
            modality="CT",
            anon_uid="9.9.9.4",
            study=study,
            records=[],
        )
        expected = str(Path("/storage") / "7" / "CT_PT_20260105" / "9.9.9.4")
        assert series_read.working_folder == expected


def _full_ctx() -> dict[str, str]:
    return {
        "anon_patient_id": "CLARINET_42",
        "anon_study_uid": "9.9.9",
        "anon_series_uid": "9.9.9.5",
        "patient_id": "PAT",
        "patient_auto_id": "42",
        "anon_id_prefix": "CLARINET",
        "study_uid": "1.2",
        "series_uid": "1.2.5",
        "study_date": "20260415",
        "study_modalities": "CT_SR",
        "series_modality": "CT",
    }
