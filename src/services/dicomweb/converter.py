"""DICOM JSON conversion for DICOMweb responses.

Converts Pydantic models and pydicom Datasets into DICOM JSON format
as defined by the DICOMweb standard (PS3.18 Appendix F).
"""

from typing import Any

from pydicom import Dataset
from pydicom.dataelem import DataElement

from src.services.dicom.models import ImageResult, SeriesResult, StudyResult


def _tag_value(vr: str, value: Any) -> dict[str, Any]:
    """Build a single DICOM JSON tag entry.

    Args:
        vr: Value Representation (e.g. "LO", "DA")
        value: The value(s) to include

    Returns:
        Dict with "vr" and optionally "Value" keys
    """
    entry: dict[str, Any] = {"vr": vr}
    if value is not None:
        entry["Value"] = value if isinstance(value, list) else [value]
    return entry


def _fields_to_dicom_json(fields: list[tuple[str, str, Any]]) -> dict[str, Any]:
    """Convert a list of (tag, VR, value) tuples to DICOM JSON format.

    Args:
        fields: List of (DICOM tag, VR, value) tuples; None values are skipped

    Returns:
        DICOM JSON dict keyed by tag
    """
    return {tag: _tag_value(vr, val) for tag, vr, val in fields if val is not None}


def study_result_to_dicom_json(result: StudyResult) -> dict[str, Any]:
    """Convert a StudyResult to DICOM JSON format.

    Args:
        result: Study-level C-FIND result

    Returns:
        DICOM JSON dict keyed by tag
    """
    return _fields_to_dicom_json(
        [
            ("0020000D", "UI", result.study_instance_uid),
            ("00100020", "LO", result.patient_id),
            (
                "00100010",
                "PN",
                {"Alphabetic": result.patient_name} if result.patient_name else None,
            ),
            ("00080020", "DA", result.study_date),
            ("00080030", "TM", result.study_time),
            ("00081030", "LO", result.study_description),
            ("00080050", "SH", result.accession_number),
            ("00080061", "CS", result.modalities_in_study),
            ("00201206", "IS", result.number_of_study_related_series),
            ("00201208", "IS", result.number_of_study_related_instances),
        ]
    )


def series_result_to_dicom_json(result: SeriesResult) -> dict[str, Any]:
    """Convert a SeriesResult to DICOM JSON format.

    Args:
        result: Series-level C-FIND result

    Returns:
        DICOM JSON dict keyed by tag
    """
    return _fields_to_dicom_json(
        [
            ("0020000D", "UI", result.study_instance_uid),
            ("0020000E", "UI", result.series_instance_uid),
            ("00080060", "CS", result.modality),
            ("00200011", "IS", result.series_number),
            ("0008103E", "LO", result.series_description),
            ("00201209", "IS", result.number_of_series_related_instances),
        ]
    )


def image_result_to_dicom_json(result: ImageResult) -> dict[str, Any]:
    """Convert an ImageResult to DICOM JSON format.

    Args:
        result: Image-level C-FIND result

    Returns:
        DICOM JSON dict keyed by tag
    """
    return _fields_to_dicom_json(
        [
            ("0020000D", "UI", result.study_instance_uid),
            ("0020000E", "UI", result.series_instance_uid),
            ("00080018", "UI", result.sop_instance_uid),
            ("00080016", "UI", result.sop_class_uid),
            ("00200013", "IS", result.instance_number),
            ("00280010", "US", result.rows),
            ("00280011", "US", result.columns),
        ]
    )


def _skip_bulk_data(_data_element: DataElement) -> str:
    """Bulk data handler that skips encoding of large binary elements like PixelData.

    Passed to ``Dataset.to_json_dict`` so that PixelData is never base64-encoded,
    avoiding the need to copy or mutate the original dataset.

    Args:
        _data_element: The pydicom data element being serialized (unused, required by API)

    Returns:
        Empty string placeholder (the tag is replaced with BulkDataURI afterward)
    """
    return ""


def dataset_to_dicom_json(ds: Dataset, base_url: str) -> dict[str, Any]:
    """Convert a pydicom Dataset to DICOM JSON, replacing PixelData with BulkDataURI.

    The original dataset is **never mutated** — PixelData is skipped during JSON
    serialization via a bulk data handler, then replaced with a BulkDataURI entry.

    Args:
        ds: pydicom Dataset (may contain PixelData)
        base_url: Base URL for constructing BulkDataURIs

    Returns:
        DICOM JSON dict keyed by tag
    """
    json_dict = ds.to_json_dict(
        suppress_invalid_tags=True,
        bulk_data_element_handler=_skip_bulk_data,
    )

    # Always set BulkDataURI for pixel data retrieval — even when PixelData
    # was stripped from the dataset before conversion (metadata endpoint) or
    # when pydicom omits large binary elements via bulk_data_threshold.
    study_uid = str(ds.get("StudyInstanceUID", ""))
    series_uid = str(ds.get("SeriesInstanceUID", ""))
    instance_uid = str(ds.get("SOPInstanceUID", ""))

    json_dict.pop("7FE00010", None)
    if instance_uid:
        json_dict["7FE00010"] = {
            "vr": "OW",
            "BulkDataURI": (
                f"{base_url}/studies/{study_uid}/series/{series_uid}"
                f"/instances/{instance_uid}/frames/1"
            ),
        }

    return json_dict
