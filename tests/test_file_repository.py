"""Unit tests for FileRepository (Phase 1 of file-repo refactor).

Covers construction from all four supported types (RecordRead, SeriesRead,
StudyRead, PatientRead), the level-semantics of ``working_dir``, the
RecordRead-only methods (``resolve_file``, ``slicer_args``), snapshot
parity with the legacy ``RecordRead.slicer_*_args_formatted`` computed
fields, and storage-path / template configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clarinet.exceptions.domain import AnonPathError
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.models.patient import PatientRead
from clarinet.models.record import RecordRead
from clarinet.models.study import SeriesRead, StudyRead
from clarinet.repositories import FileRepository

# ── helpers ────────────────────────────────────────────────────────────────


def _make_patient_mock(
    patient_id: str = "PAT001",
    anon_id: str | None = "CLARINET_1",
    anon_name: str | None = "Anon Patient",
    auto_id: int | None = 1,
) -> MagicMock:
    p = MagicMock(spec=PatientRead)
    p.id = patient_id
    p.anon_id = anon_id
    p.anon_name = anon_name
    p.auto_id = auto_id
    return p


def _make_study_mock(
    study_uid: str = "1.2.3.4.5",
    anon_uid: str | None = "9.8.7.6.5",
    patient: MagicMock | None = None,
    date: Any = None,
    modalities_in_study: str | None = None,
) -> MagicMock:
    s = MagicMock(spec=StudyRead)
    s.study_uid = study_uid
    s.anon_uid = anon_uid
    s.patient = patient if patient is not None else _make_patient_mock()
    s.date = date
    s.modalities_in_study = modalities_in_study
    return s


def _make_series_mock(
    series_uid: str = "1.2.3.4.5.6",
    anon_uid: str | None = "9.8.7.6.5.4",
    study: MagicMock | None = None,
    modality: str | None = "CT",
    series_number: int | None = 1,
) -> MagicMock:
    s = MagicMock(spec=SeriesRead)
    s.series_uid = series_uid
    s.anon_uid = anon_uid
    s.study = study if study is not None else _make_study_mock()
    s.study_uid = s.study.study_uid
    s.modality = modality
    s.series_number = series_number
    return s


def _make_record_type_mock(
    name: str = "ct-segmentation",
    level: DicomQueryLevel = DicomQueryLevel.SERIES,
    file_registry: list[FileDefinitionRead] | None = None,
    slicer_script_args: dict[str, str] | None = None,
    slicer_result_validator_args: dict[str, str] | None = None,
) -> MagicMock:
    rt = MagicMock()
    rt.name = name
    rt.level = level
    rt.file_registry = file_registry or []
    rt.slicer_script_args = slicer_script_args
    rt.slicer_result_validator_args = slicer_result_validator_args
    return rt


def _make_record_mock(
    *,
    level: DicomQueryLevel = DicomQueryLevel.SERIES,
    record_id: int = 42,
    clarinet_storage_path: str | None = None,
    file_registry: list[FileDefinitionRead] | None = None,
    slicer_script_args: dict[str, str] | None = None,
    slicer_result_validator_args: dict[str, str] | None = None,
    data: dict | None = None,
) -> MagicMock:
    record = MagicMock(spec=RecordRead)
    record.id = record_id
    record.user_id = None
    record.patient_id = "PAT001"
    record.study_uid = "1.2.3.4.5" if level != DicomQueryLevel.PATIENT else None
    record.series_uid = "1.2.3.4.5.6" if level == DicomQueryLevel.SERIES else None
    record.study_anon_uid = "9.8.7.6.5" if level != DicomQueryLevel.PATIENT else None
    record.series_anon_uid = "9.8.7.6.5.4" if level == DicomQueryLevel.SERIES else None
    record.clarinet_storage_path = clarinet_storage_path
    record.data = data or {}
    record.file_links = None

    patient = _make_patient_mock()
    record.patient = patient
    study = _make_study_mock(patient=patient) if level != DicomQueryLevel.PATIENT else None
    record.study = study
    record.series = _make_series_mock(study=study) if level == DicomQueryLevel.SERIES else None
    record.record_type = _make_record_type_mock(
        level=level,
        file_registry=file_registry,
        slicer_script_args=slicer_script_args,
        slicer_result_validator_args=slicer_result_validator_args,
    )

    # Patch _format_slicer_kwargs to a callable we can verify (real method
    # is on RecordRead; MagicMock spec exposes it but as a MagicMock too).
    # We delegate to the real method to retain byte-for-byte parity, but
    # _make_record_mock doesn't have access to the real RecordRead instance,
    # so we wire in a passthrough that mimics the legacy behavior.
    real_kwargs = RecordRead._format_slicer_kwargs
    record._format_slicer_kwargs = lambda kw, extra: real_kwargs(record, kw, extra)
    record._format_path = lambda p, **extra: RecordRead._format_path(record, p, **extra)
    record._format_path_strict = lambda p, **kw: RecordRead._format_path_strict(record, p, **kw)
    record._resolve_patient_id_for_path = lambda f: RecordRead._resolve_patient_id_for_path(
        record, f
    )
    record._resolve_study_anon_uid_for_path = lambda f: RecordRead._resolve_study_anon_uid_for_path(
        record, f
    )
    record._resolve_series_anon_uid_for_path = lambda f: (
        RecordRead._resolve_series_anon_uid_for_path(record, f)
    )
    return record


# ── Construction by type ───────────────────────────────────────────────────


class TestFileRepositoryConstruction:
    @patch("clarinet.services.common.file_resolver.settings")
    def test_construct_from_record_read_series_level(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/data"
        record = _make_record_mock(level=DicomQueryLevel.SERIES)

        repo = FileRepository(record)
        assert repo.working_dir == Path("/data/CLARINET_1/9.8.7.6.5/9.8.7.6.5.4")
        dirs = repo.working_dirs_all()
        assert DicomQueryLevel.PATIENT in dirs
        assert DicomQueryLevel.STUDY in dirs
        assert DicomQueryLevel.SERIES in dirs

    @patch("clarinet.services.common.file_resolver.settings")
    def test_construct_from_record_read_study_level(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/data"
        record = _make_record_mock(level=DicomQueryLevel.STUDY)

        repo = FileRepository(record)
        assert repo.working_dir == Path("/data/CLARINET_1/9.8.7.6.5")

    @patch("clarinet.services.common.file_resolver.settings")
    def test_construct_from_record_read_patient_level(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/data"
        record = _make_record_mock(level=DicomQueryLevel.PATIENT)

        repo = FileRepository(record)
        assert repo.working_dir == Path("/data/CLARINET_1")
        dirs = repo.working_dirs_all()
        assert dirs == {DicomQueryLevel.PATIENT: Path("/data/CLARINET_1")}

    @patch("clarinet.services.common.file_resolver.settings")
    def test_construct_from_series_read(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/data"
        series = _make_series_mock()

        repo = FileRepository(series)
        assert repo.working_dir == Path("/data/CLARINET_1/9.8.7.6.5/9.8.7.6.5.4")
        dirs = repo.working_dirs_all()
        assert set(dirs.keys()) == {
            DicomQueryLevel.PATIENT,
            DicomQueryLevel.STUDY,
            DicomQueryLevel.SERIES,
        }

    @patch("clarinet.services.common.file_resolver.settings")
    def test_construct_from_study_read(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/data"
        study = _make_study_mock()

        repo = FileRepository(study)
        assert repo.working_dir == Path("/data/CLARINET_1/9.8.7.6.5")
        dirs = repo.working_dirs_all()
        assert set(dirs.keys()) == {DicomQueryLevel.PATIENT, DicomQueryLevel.STUDY}

    @patch("clarinet.services.common.file_resolver.settings")
    def test_construct_from_patient_read(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/data"
        patient = _make_patient_mock()

        repo = FileRepository(patient)
        assert repo.working_dir == Path("/data/CLARINET_1")
        dirs = repo.working_dirs_all()
        assert dirs == {DicomQueryLevel.PATIENT: Path("/data/CLARINET_1")}

    def test_construct_rejects_unknown_type(self) -> None:
        with pytest.raises(TypeError, match="FileRepository accepts"):
            FileRepository(object())  # type: ignore[arg-type]

    @patch("clarinet.services.common.file_resolver.settings")
    def test_construct_record_fails_on_non_anonymized_series(
        self, mock_settings: MagicMock
    ) -> None:
        """Non-anonymized RecordRead cannot construct FileRepository.

        Confirms slicer_args is NOT a UX-fallback exception — strict mode
        applies uniformly. UX-routers must catch AnonPathError per Phase 4.
        """
        mock_settings.storage_path = "/data"
        record = _make_record_mock(level=DicomQueryLevel.SERIES)
        record.series.anon_uid = None
        record.series_anon_uid = None

        with pytest.raises(AnonPathError, match="Series has no anon_uid"):
            FileRepository(record)


# ── resolve_file (RecordRead-only) ─────────────────────────────────────────


class TestFileRepositoryResolveFile:
    @patch("clarinet.services.common.file_resolver.settings")
    def test_resolve_file_with_file_definition(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/data"
        fd = FileDefinitionRead(
            name="report",
            pattern="report.json",
            role=FileRole.OUTPUT,
            required=True,
            multiple=False,
            level=None,
        )
        record = _make_record_mock(file_registry=[fd])

        repo = FileRepository(record)
        path = repo.resolve_file(fd)
        assert path == Path("/data/CLARINET_1/9.8.7.6.5/9.8.7.6.5.4/report.json")

    @patch("clarinet.services.common.file_resolver.settings")
    @pytest.mark.parametrize(
        "factory",
        [_make_series_mock, _make_study_mock, _make_patient_mock],
    )
    def test_resolve_file_rejects_non_record_inputs(
        self, mock_settings: MagicMock, factory
    ) -> None:
        mock_settings.storage_path = "/data"
        entity = factory()
        fd = FileDefinitionRead(
            name="r", pattern="r.json", role=FileRole.OUTPUT, required=True, multiple=False
        )
        repo = FileRepository(entity)
        with pytest.raises(TypeError, match="resolve_file requires RecordRead"):
            repo.resolve_file(fd)


# ── slicer_args (RecordRead-only, snapshot parity) ─────────────────────────


class TestFileRepositorySlicerArgs:
    @patch("clarinet.services.common.file_resolver.settings")
    def test_slicer_args_default_renders_script_args(self, mock_settings: MagicMock) -> None:
        """Literal snapshot: script-args templates render against
        working_dir + record fields (patient_id, study/series UIDs, etc.)."""
        mock_settings.storage_path = "/data"
        slicer_args = {
            "input": "{working_folder}/input.nrrd",
            "patient": "{patient_id}",
            "study": "{study_uid}",
        }
        record = _make_record_mock(slicer_script_args=slicer_args)

        result = FileRepository(record).slicer_args(validator=False)
        # ``{working_folder}/input.nrrd`` is a slicer template: the literal "/"
        # is preserved by ``str.format`` (not joined via ``Path``), so on
        # Windows the result is ``str(working_dir) + "/input.nrrd"`` with
        # mixed separators. Build ``expected`` the same way for portability.
        working_dir = str(Path("/data") / "CLARINET_1" / "9.8.7.6.5" / "9.8.7.6.5.4")
        assert result == {
            "input": f"{working_dir}/input.nrrd",
            "patient": "CLARINET_1",
            "study": "1.2.3.4.5",
        }

    @patch("clarinet.services.common.file_resolver.settings")
    def test_slicer_args_validator_renders_validator_args(self, mock_settings: MagicMock) -> None:
        """Literal snapshot for validator branch."""
        mock_settings.storage_path = "/data"
        validator_args = {
            "check": "{working_folder}/check.json",
            "series": "{series_uid}",
        }
        record = _make_record_mock(slicer_result_validator_args=validator_args)

        result = FileRepository(record).slicer_args(validator=True)
        working_dir = str(Path("/data") / "CLARINET_1" / "9.8.7.6.5" / "9.8.7.6.5.4")
        assert result == {
            "check": f"{working_dir}/check.json",
            "series": "1.2.3.4.5.6",
        }

    @patch("clarinet.services.common.file_resolver.settings")
    def test_slicer_args_none_when_record_type_has_no_args(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/data"
        record = _make_record_mock(slicer_script_args=None, slicer_result_validator_args=None)

        repo = FileRepository(record)
        assert repo.slicer_args(validator=False) is None
        assert repo.slicer_args(validator=True) is None

    @patch("clarinet.services.common.file_resolver.settings")
    @pytest.mark.parametrize(
        "factory",
        [_make_series_mock, _make_study_mock, _make_patient_mock],
    )
    def test_slicer_args_rejects_non_record_inputs(self, mock_settings: MagicMock, factory) -> None:
        mock_settings.storage_path = "/data"
        entity = factory()
        repo = FileRepository(entity)
        with pytest.raises(TypeError, match="slicer_args requires RecordRead"):
            repo.slicer_args()


# ── Configuration ──────────────────────────────────────────────────────────


class TestFileRepositoryConfiguration:
    @patch("clarinet.services.common.file_resolver.settings")
    def test_clarinet_storage_path_override_applied(self, mock_settings: MagicMock) -> None:
        mock_settings.storage_path = "/default"
        record = _make_record_mock(clarinet_storage_path="/custom")

        repo = FileRepository(record)
        assert repo.working_dir.is_relative_to(Path("/custom"))

    @patch("clarinet.services.common.storage_paths.settings")
    @patch("clarinet.services.common.file_resolver.settings")
    def test_disk_path_template_custom_template_respected(
        self,
        mock_fr_settings: MagicMock,
        mock_sp_settings: MagicMock,
    ) -> None:
        """A raw-UID template renders without invoking anon resolution."""
        mock_fr_settings.storage_path = "/data"
        mock_sp_settings.disk_path_template = "{patient_id}/{study_uid}/{series_uid}"
        mock_sp_settings.anon_per_study_patient_id = False
        mock_sp_settings.anon_id_prefix = "CLARINET"

        # Series with no anon_uid — would raise under the default template,
        # but our raw-UID template never asks for it.
        series = _make_series_mock(anon_uid=None)
        series.study.anon_uid = None
        series.study.patient.anon_id = None

        repo = FileRepository(series)
        assert repo.working_dir == Path("/data/PAT001/1.2.3.4.5/1.2.3.4.5.6")

    @patch("clarinet.services.common.file_resolver.settings")
    def test_anon_path_error_raises_in_strict_mode(self, mock_settings: MagicMock) -> None:
        """Series without anon_uid + default template → AnonPathError on __init__."""
        mock_settings.storage_path = "/data"
        series = _make_series_mock(anon_uid=None)

        with pytest.raises(AnonPathError, match="Series has no anon_uid"):
            FileRepository(series)
