"""Unit tests for DICOM dataset → Pydantic result parsing.

Covers ``DicomOperations._parse_study_result``, in particular the
``ModalitiesInStudy`` multi-value handling that previously serialized
as a Python list repr (``"['CT', 'SR']"``) before the
``_ds_modalities`` fix.
"""

from pydicom import Dataset
from pydicom.multival import MultiValue

from clarinet.services.dicom.anon_path import _modalities_string
from clarinet.services.dicom.operations import DicomOperations


class TestParseStudyResult:
    def test_modalities_single_value(self) -> None:
        ds = Dataset()
        ds.StudyInstanceUID = "1.2.3"
        ds.ModalitiesInStudy = "CT"
        result = DicomOperations(calling_aet="TEST")._parse_study_result(ds)
        assert result.modalities_in_study == "CT"

    def test_modalities_multi_value_joins_with_backslash(self) -> None:
        ds = Dataset()
        ds.StudyInstanceUID = "1.2.3"
        ds.ModalitiesInStudy = MultiValue(str, ["CT", "SR"])
        result = DicomOperations(calling_aet="TEST")._parse_study_result(ds)
        assert result.modalities_in_study == "CT\\SR"

    def test_modalities_missing_yields_none(self) -> None:
        ds = Dataset()
        ds.StudyInstanceUID = "1.2.3"
        result = DicomOperations(calling_aet="TEST")._parse_study_result(ds)
        assert result.modalities_in_study is None

    def test_modalities_round_trip_into_path_segment(self) -> None:
        """Parsed multi-value → ``_modalities_string`` → sorted ``_``-joined."""

        class StudyStub:
            modalities_in_study: str | None

        ds = Dataset()
        ds.StudyInstanceUID = "1.2.3"
        ds.ModalitiesInStudy = MultiValue(str, ["SR", "CT"])  # unsorted input
        result = DicomOperations(calling_aet="TEST")._parse_study_result(ds)

        study = StudyStub()
        study.modalities_in_study = result.modalities_in_study
        assert _modalities_string(study) == "CT_SR"
