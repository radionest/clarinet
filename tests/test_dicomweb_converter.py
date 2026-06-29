"""Regression tests for DICOMweb DICOM JSON converter.

Ensures dataset_to_dicom_json never mutates the original pydicom Dataset
(e.g. by stripping PixelData), which would corrupt cached instances and
cause frame retrieval to fail with 404.
"""

from dimsechord import (
    dataset_to_dicom_json,
    study_result_to_dicom_json,
)
from pydicom import Dataset, config
from pydicom.dataelem import RawDataElement
from pydicom.tag import Tag
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage

from clarinet.services.dicom.models import StudyResult


def _make_dataset_with_pixel_data() -> Dataset:
    """Create a minimal Dataset that includes PixelData."""
    ds = Dataset()
    ds.StudyInstanceUID = "1.2.3.4"
    ds.SeriesInstanceUID = "1.2.3.4.5"
    ds.SOPInstanceUID = "1.2.3.4.5.6"
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.Rows = 2
    ds.Columns = 2
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0
    ds.PixelData = b"\x00\x01\x02\x03"

    ds.file_meta = Dataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def test_dataset_to_dicom_json_preserves_pixel_data() -> None:
    """dataset_to_dicom_json must NOT strip PixelData from the original Dataset."""
    ds = _make_dataset_with_pixel_data()
    original_pixel_data = ds.PixelData

    base_url = "http://localhost/dicom-web"
    json_obj = dataset_to_dicom_json(ds, base_url)

    # Original dataset still has PixelData
    assert hasattr(ds, "PixelData"), "PixelData was stripped from the original Dataset"
    assert ds.PixelData == original_pixel_data, "PixelData content was modified"

    # JSON output contains BulkDataURI (not InlineBinary) for PixelData tag
    pixel_tag = json_obj.get("7FE00010")
    assert pixel_tag is not None, "PixelData tag missing from JSON output"
    assert "BulkDataURI" in pixel_tag, "Expected BulkDataURI for PixelData tag"
    assert "InlineBinary" not in pixel_tag, "PixelData should not be base64-encoded"
    assert "1.2.3.4.5.6" in pixel_tag["BulkDataURI"], "BulkDataURI should reference instance UID"


def test_dataset_to_dicom_json_without_pixel_data() -> None:
    """Conversion works for datasets that have no PixelData (e.g. structured reports)."""
    ds = Dataset()
    ds.StudyInstanceUID = "1.2.3.4"
    ds.SeriesInstanceUID = "1.2.3.4.5"
    ds.SOPInstanceUID = "1.2.3.4.5.7"
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.file_meta = Dataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    base_url = "http://localhost/dicom-web"
    json_obj = dataset_to_dicom_json(ds, base_url)

    # Should still have BulkDataURI pointing to frames endpoint
    pixel_tag = json_obj.get("7FE00010")
    assert pixel_tag is not None
    assert "BulkDataURI" in pixel_tag


def test_dataset_to_dicom_json_normalizes_invalid_is_value() -> None:
    """Float-formatted IS values (Philips scanner output) serialize as integers.

    Regression: ``to_json_dict(suppress_invalid_tags=True)`` wrapped serialization
    in ``config.strict_reading()``, mutating the GLOBAL pydicom validation mode —
    concurrent conversions raced and multi-study OHIF load failed with
    "Invalid value for VR IS" instead of rendering.

    The bad value MUST be a RawDataElement (production-faithful post-C-GET state):
    under the old code strict RAISE fired during raw→DataElement conversion and the
    tag was silently dropped, so this test fails on a revert. A pre-converted value
    (``add_new``) would serialize fine under both implementations and pin nothing.
    """
    ds = Dataset()
    ds.StudyInstanceUID = "1.2.3.4"
    ds.SeriesInstanceUID = "1.2.3.4.5"
    ds.SOPInstanceUID = "1.2.3.4.5.8"
    # ImagesInAcquisition, float-formatted (14 chars > 12 max for IS)
    ds._dict[Tag(0x00201002)] = RawDataElement(
        Tag(0x00201002), "IS", 14, b"606.0000000000", 0, True, True
    )

    mode_before = config.settings._reading_validation_mode

    json_obj = dataset_to_dicom_json(ds, "http://localhost/dicom-web")

    assert json_obj["00201002"] == {"vr": "IS", "Value": [606]}
    assert config.settings._reading_validation_mode == mode_before, (
        "global pydicom validation mode must not be mutated"
    )


def test_dataset_to_dicom_json_skips_unconvertible_tag() -> None:
    """A tag whose raw value cannot be converted at all is dropped, not raised."""
    ds = Dataset()
    ds.StudyInstanceUID = "1.2.3.4"
    ds.SeriesInstanceUID = "1.2.3.4.5"
    ds.SOPInstanceUID = "1.2.3.4.5.9"
    # Simulate post-dcmread state: raw IS bytes that fail int/float conversion
    ds._dict[Tag(0x00201002)] = RawDataElement(Tag(0x00201002), "IS", 4, b"abcd", 0, True, True)

    json_obj = dataset_to_dicom_json(ds, "http://localhost/dicom-web")

    assert "00201002" not in json_obj
    assert json_obj["0020000D"] == {"vr": "UI", "Value": ["1.2.3.4"]}


def test_study_result_modalities_multi_value_splits_backslash() -> None:
    r"""ModalitiesInStudy stored as 'CT\SR' must serialize as a JSON array."""
    result = StudyResult(study_instance_uid="1.2.3", modalities_in_study="CT\\SR")
    j = study_result_to_dicom_json(result)
    assert j["00080061"] == {"vr": "CS", "Value": ["CT", "SR"]}


def test_study_result_modalities_single_value() -> None:
    """Single-modality studies serialize as a one-element array."""
    result = StudyResult(study_instance_uid="1.2.3", modalities_in_study="CT")
    j = study_result_to_dicom_json(result)
    assert j["00080061"] == {"vr": "CS", "Value": ["CT"]}


def test_study_result_modalities_missing_tag_omitted() -> None:
    """Missing modalities → tag absent from JSON output."""
    result = StudyResult(study_instance_uid="1.2.3", modalities_in_study=None)
    j = study_result_to_dicom_json(result)
    assert "00080061" not in j
