"""Unit tests for the configurable disk path resolver."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from clarinet.models.base import DicomQueryLevel
from clarinet.models.patient import Patient
from clarinet.models.study import Series, Study
from clarinet.services.common.storage_paths import (
    AnonPathError,
    build_context,
    derive_anon_patient_id,
    render_working_folder,
    split_template,
    validate_template,
)
from clarinet.utils.path_template import extract_placeholders

# Default disk_path_template (matches settings.toml). build_context() reads
# settings.disk_path_template when template= is not passed; mock its value
# explicitly because spec_set=True returns a MagicMock for attribute access.
_DEFAULT_TEMPLATE = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"

# Template referencing every supported placeholder — used by tests that
# assert the full context dict. (Not a valid disk_path_template — it does
# not have 3 segments — but build_context() only parses placeholders.)
_ALL_PLACEHOLDERS_TEMPLATE = (
    "{anon_patient_id}_{anon_study_uid}_{anon_series_uid}"
    "_{patient_id}_{patient_auto_id}_{anon_id_prefix}"
    "_{study_uid}_{series_uid}_{study_date}"
    "_{study_modalities}_{series_modality}_{series_num}"
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
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            ctx = build_context(
                patient=patient,
                study=study,
                series=series,
                template=_ALL_PLACEHOLDERS_TEMPLATE,
            )
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
        assert ctx["series_num"] == "00001"

    def test_per_study_mode(self, patient: Patient, study: Study, series: Series) -> None:
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = True
            mock_settings.anon_per_study_patient_id_hex_length = 8
            mock_settings.anon_uid_salt = "salt"
            mock_settings.anon_id_prefix = "CLARINET"
            mock_settings.disk_path_template = _DEFAULT_TEMPLATE
            ctx = build_context(patient=patient, study=study, series=series)
        # per-study hash is 8 hex chars + "CLARINET_" prefix
        assert ctx["anon_patient_id"].startswith("CLARINET_")
        # Hex hash part should be 8 chars after prefix
        assert len(ctx["anon_patient_id"].split("_")[-1]) == 8

    def test_writer_overrides(self, patient: Patient, study: Study, series: Series) -> None:
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "X"
            mock_settings.disk_path_template = _DEFAULT_TEMPLATE
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

    def test_missing_entities_yield_unknown_with_fallback(self) -> None:
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "X"
            ctx = build_context(
                patient=None,
                study=None,
                series=None,
                template=_ALL_PLACEHOLDERS_TEMPLATE,
                fallback_to_unanonymized=True,
            )
        assert ctx["patient_id"] == "unknown"
        assert ctx["study_uid"] == "unknown"
        assert ctx["series_uid"] == "unknown"
        assert ctx["study_modalities"] == "unknown"
        assert ctx["series_modality"] == "unknown"
        assert ctx["study_date"] == "unknown"
        assert ctx["patient_auto_id"] == "unknown"
        assert ctx["series_num"] == "unknown"

    def test_missing_patient_anon_raises_in_default_safe_mode(self) -> None:
        # Patient is supplied but has no anon_id — backend safe mode must
        # surface the asymmetric-anonymization race instead of silently
        # rendering the raw patient_id into a working folder.
        unanon_patient = Patient(id="PAT001", name="John", auto_id=None)
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            mock_settings.disk_path_template = _DEFAULT_TEMPLATE
            with pytest.raises(AnonPathError, match="Patient has no anon_id"):
                build_context(patient=unanon_patient, study=None, series=None)

    def test_missing_study_anon_raises_in_default_safe_mode(self, patient: Patient) -> None:
        from clarinet.models.study import Study

        unanon_study = Study(
            study_uid="1.2.3.4",
            date=date(2026, 4, 15),
            patient_id=patient.id,
            anon_uid=None,
        )
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            mock_settings.disk_path_template = _DEFAULT_TEMPLATE
            with pytest.raises(AnonPathError, match="Study has no anon_uid"):
                build_context(patient=patient, study=unanon_study, series=None)

    def test_missing_study_anon_with_fallback_uses_raw(self, patient: Patient) -> None:
        from clarinet.models.study import Study

        unanon_study = Study(
            study_uid="1.2.3.4",
            date=date(2026, 4, 15),
            patient_id=patient.id,
            anon_uid=None,
        )
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            mock_settings.disk_path_template = _DEFAULT_TEMPLATE
            ctx = build_context(
                patient=patient,
                study=unanon_study,
                series=None,
                fallback_to_unanonymized=True,
            )
        assert ctx["anon_study_uid"] == "1.2.3.4"

    def test_missing_series_anon_raises_in_default_safe_mode(
        self, patient: Patient, study: Study
    ) -> None:
        unanon_series = Series(
            series_uid="1.2.3.4.5",
            series_number=1,
            modality="CT",
            study_uid=study.study_uid,
            anon_uid=None,
        )
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            mock_settings.disk_path_template = _DEFAULT_TEMPLATE
            with pytest.raises(AnonPathError, match="Series has no anon_uid"):
                build_context(patient=patient, study=study, series=unanon_series)


class TestDeriveAnonPatientId:
    def test_per_patient_uses_anon_id(self, patient: Patient, study: Study) -> None:
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            result = derive_anon_patient_id(patient, study)
        assert result == "CLARINET_42"

    def test_per_study_uses_hash(self, patient: Patient, study: Study) -> None:
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = True
            mock_settings.anon_per_study_patient_id_hex_length = 8
            mock_settings.anon_uid_salt = "salt"
            mock_settings.anon_id_prefix = "P"
            result = derive_anon_patient_id(patient, study)
        # Same salt+study_uid+length+prefix must be deterministic across calls
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = True
            mock_settings.anon_per_study_patient_id_hex_length = 8
            mock_settings.anon_uid_salt = "salt"
            mock_settings.anon_id_prefix = "P"
            result2 = derive_anon_patient_id(patient, study)
        assert result == result2
        assert result.startswith("P_")

    def test_missing_anon_id_raises_in_default_safe_mode(self, study: Study) -> None:
        unanon_patient = Patient(id="PAT001", name="John", auto_id=None)
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            with pytest.raises(AnonPathError, match="Patient has no anon_id"):
                derive_anon_patient_id(unanon_patient, study)

    def test_missing_anon_id_with_fallback_uses_raw(self, study: Study) -> None:
        unanon_patient = Patient(id="PAT001", name="John", auto_id=None)
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            result = derive_anon_patient_id(unanon_patient, study, fallback_to_unanonymized=True)
        assert result == "PAT001"


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

    def test_template_with_series_num(self) -> None:
        ctx = _full_ctx()
        result = render_working_folder(
            "{anon_patient_id}/{study_modalities}_{study_date}/{series_num}_{anon_series_uid}",
            DicomQueryLevel.SERIES,
            ctx,
            Path("/data"),
        )
        assert result == Path("/data/CLARINET_42/CT_SR_20260415/00001_9.9.9.5")

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

    def test_series_num_template_passes(self) -> None:
        template = (
            "{anon_patient_id}/{study_modalities}_{study_date}/{series_num}_{anon_series_uid}"
        )
        assert validate_template(template) == template

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
    """``FileRepository(series_read).working_dir`` uses the resolver — no hardcoded layout.

    ``SeriesRead.working_folder`` was removed entirely; the canonical
    entry point for series paths is now ``FileRepository``.
    """

    def test_default_template_layout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clarinet.models.patient import PatientInfo
        from clarinet.models.study import SeriesRead, StudyRead
        from clarinet.repositories import FileRepository

        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.storage_path", "/storage"
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.disk_path_template",
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_per_study_patient_id",
            False,
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_id_prefix",
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
        assert str(FileRepository(series_read).working_dir) == expected

    def test_custom_template_uses_modalities_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from clarinet.models.patient import PatientInfo
        from clarinet.models.study import SeriesRead, StudyRead
        from clarinet.repositories import FileRepository

        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.storage_path", "/storage"
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.disk_path_template",
            "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}",
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_per_study_patient_id",
            False,
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_id_prefix",
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
        assert str(FileRepository(series_read).working_dir) == expected


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
        "series_num": "00001",
    }


class TestRenderAllLevels:
    """``render_all_levels`` returns a level → Path dict driven by the template."""

    def test_returns_only_patient_when_study_none(
        self, patient: Patient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clarinet.services.common.storage_paths import render_all_levels

        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.disk_path_template",
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_per_study_patient_id",
            False,
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_id_prefix",
            "CLARINET",
        )

        dirs = render_all_levels(
            patient=patient, study=None, series=None, storage_path=Path("/storage")
        )
        assert set(dirs) == {DicomQueryLevel.PATIENT}

    def test_returns_patient_and_study_when_series_none(
        self, patient: Patient, study: Study, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from clarinet.services.common.storage_paths import render_all_levels

        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.disk_path_template",
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_per_study_patient_id",
            False,
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_id_prefix",
            "CLARINET",
        )

        dirs = render_all_levels(
            patient=patient, study=study, series=None, storage_path=Path("/storage")
        )
        assert set(dirs) == {DicomQueryLevel.PATIENT, DicomQueryLevel.STUDY}

    def test_returns_all_three_when_all_present(
        self,
        patient: Patient,
        study: Study,
        series: Series,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from clarinet.services.common.storage_paths import render_all_levels

        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.disk_path_template",
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_per_study_patient_id",
            False,
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_id_prefix",
            "CLARINET",
        )

        dirs = render_all_levels(
            patient=patient, study=study, series=series, storage_path=Path("/storage")
        )
        assert set(dirs) == {
            DicomQueryLevel.PATIENT,
            DicomQueryLevel.STUDY,
            DicomQueryLevel.SERIES,
        }
        assert dirs[DicomQueryLevel.SERIES] == Path("/storage/CLARINET_42/9.9.9.9/9.9.9.9.5")

    def test_empty_when_patient_none(self) -> None:
        from clarinet.services.common.storage_paths import render_all_levels

        assert (
            render_all_levels(patient=None, study=None, series=None, storage_path=Path("/storage"))
            == {}
        )


class TestWriterFileResolverUnification:
    """Writer (build_context + render_working_folder) and FileResolver
    (build_working_dirs* → render_all_levels) MUST produce identical paths
    for any ``disk_path_template`` — otherwise a custom template causes the
    writer to drop files in one place and the readers (Slicer / pipeline /
    file validation) to look in another (silent wrong-path bug)."""

    def _writer_series_path(
        self,
        patient: Patient,
        study: Study,
        series: Series,
        template: str,
        storage_path: Path,
    ) -> Path:
        from clarinet.services.common.storage_paths import (
            build_context,
            render_working_folder,
        )

        ctx = build_context(patient=patient, study=study, series=series)
        return render_working_folder(template, DicomQueryLevel.SERIES, ctx, storage_path)

    @pytest.mark.parametrize(
        "template",
        [
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
            "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}",
            "{anon_id_prefix}_{patient_auto_id}/{study_uid}_{study_date}/series_{series_num}",
            "{anon_patient_id}/{anon_study_uid}/{series_modality}_{anon_series_uid}",
        ],
    )
    def test_writer_and_file_resolver_agree(
        self,
        patient: Patient,
        study: Study,
        series: Series,
        template: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from clarinet.services.common.storage_paths import render_all_levels

        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.storage_path", "/storage"
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.disk_path_template", template
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_per_study_patient_id", False
        )
        monkeypatch.setattr(
            "clarinet.services.common.storage_paths.settings.anon_id_prefix", "CLARINET"
        )

        writer_path = self._writer_series_path(patient, study, series, template, Path("/storage"))
        resolver_dirs = render_all_levels(
            patient=patient,
            study=study,
            series=series,
            storage_path=Path("/storage"),
        )
        assert resolver_dirs[DicomQueryLevel.SERIES] == writer_path


class TestExtractPlaceholders:
    def test_default_template(self) -> None:
        assert extract_placeholders("{anon_patient_id}/{anon_study_uid}/{anon_series_uid}") == {
            "anon_patient_id",
            "anon_study_uid",
            "anon_series_uid",
        }

    def test_no_placeholders(self) -> None:
        assert extract_placeholders("a/b/c") == set()

    def test_repeated_placeholders_deduped(self) -> None:
        assert extract_placeholders("{a}/{a}/{b}") == {"a", "b"}

    def test_escaped_braces_ignored(self) -> None:
        assert extract_placeholders("{{literal}}/{a}/{{x}}") == {"a"}


class TestBuildContextPullBased:
    """Phase 1.5: build_context resolves only placeholders present in template."""

    def test_no_anon_template_skips_require_anon_or_raw(
        self, patient: Patient, study: Study, series: Series
    ) -> None:
        """Raw-UID template must NOT invoke anon resolution, even with anon_uid=None."""
        unanon_study = Study(
            study_uid="1.2.3.4",
            date=date(2026, 4, 15),
            patient_id=patient.id,
            anon_uid=None,
        )
        unanon_series = Series(
            series_uid="1.2.3.4.5",
            series_number=1,
            modality="CT",
            study_uid=unanon_study.study_uid,
            anon_uid=None,
        )
        with (
            patch("clarinet.services.common.storage_paths.require_anon_or_raw") as mock_raar,
            patch(
                "clarinet.services.common.storage_paths.settings", spec_set=True
            ) as mock_settings,
        ):
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "X"
            ctx = build_context(
                patient=patient,
                study=unanon_study,
                series=unanon_series,
                template="{patient_id}/{study_uid}/{series_uid}",
            )
        mock_raar.assert_not_called()
        assert ctx == {
            "patient_id": "PAT001",
            "study_uid": "1.2.3.4",
            "series_uid": "1.2.3.4.5",
        }

    def test_anon_template_strict_raises_when_anon_missing(self, patient: Patient) -> None:
        """Template with {anon_study_uid} + no anon_uid + strict → AnonPathError."""
        unanon_study = Study(
            study_uid="1.2.3.4",
            date=date(2026, 4, 15),
            patient_id=patient.id,
            anon_uid=None,
        )
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            with pytest.raises(AnonPathError, match="Study has no anon_uid"):
                build_context(
                    patient=patient,
                    study=unanon_study,
                    series=None,
                    template="{anon_patient_id}/{anon_study_uid}/x",
                )

    def test_anon_template_fallback_returns_raw_uid(self, patient: Patient) -> None:
        unanon_study = Study(
            study_uid="1.2.3.4",
            date=date(2026, 4, 15),
            patient_id=patient.id,
            anon_uid=None,
        )
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            ctx = build_context(
                patient=patient,
                study=unanon_study,
                series=None,
                template="{anon_patient_id}/{anon_study_uid}/x",
                fallback_to_unanonymized=True,
            )
        assert ctx["anon_study_uid"] == "1.2.3.4"

    def test_template_none_defaults_to_settings(
        self, patient: Patient, study: Study, series: Series
    ) -> None:
        """When template=None, falls back to settings.disk_path_template (default behavior)."""
        with patch(
            "clarinet.services.common.storage_paths.settings", spec_set=True
        ) as mock_settings:
            mock_settings.anon_per_study_patient_id = False
            mock_settings.anon_id_prefix = "CLARINET"
            mock_settings.disk_path_template = (
                "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
            )
            ctx = build_context(patient=patient, study=study, series=series)
        # All three anon placeholders resolved (default template references them).
        assert "anon_patient_id" in ctx
        assert "anon_study_uid" in ctx
        assert "anon_series_uid" in ctx
        # Placeholders NOT in default template are absent.
        assert "study_date" not in ctx
        assert "series_num" not in ctx
