"""Unit tests for DICOM dataset → Pydantic result parsing.

Covers ``DicomOperations._parse_study_result``, in particular the
``ModalitiesInStudy`` multi-value handling that previously serialized
as a Python list repr (``"['CT', 'SR']"``) before the
``_ds_modalities`` fix.
"""

import logging

from pydicom import Dataset
from pydicom.multival import MultiValue

from clarinet.services.common.storage_paths import _modalities_string
from clarinet.services.dicom.operations import DicomOperations, _ds_modalities


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

    def test_ds_modalities_non_iterable_returns_none_and_warns(
        self, caplog: logging.LogCaptureFixture
    ) -> None:
        """Non-iterable, non-string ModalitiesInStudy must return None.

        Previously the function fell back to ``str(val)`` and poisoned the DB
        with a Python list repr (``"['CT', 'SR']"``); now it logs a warning
        and returns None so the downstream path renderer sees a clean value.
        """

        class Stub:
            ModalitiesInStudy = object()  # non-iterable, non-str

        with caplog.at_level(logging.WARNING):
            result = _ds_modalities(Stub())  # type: ignore[arg-type]

        assert result is None
