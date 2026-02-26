"""Regression tests for DICOMweb DICOM JSON converter.

Ensures dataset_to_dicom_json never mutates the original pydicom Dataset
(e.g. by stripping PixelData), which would corrupt cached instances and
cause frame retrieval to fail with 404.
"""

from pydicom import Dataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage

from src.services.dicomweb.converter import dataset_to_dicom_json


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
