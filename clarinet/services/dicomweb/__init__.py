"""DICOMweb proxy service — translates DICOMweb HTTP requests to DICOM Q/R operations."""

import pydicom.config as _pydicom_config

# Vendor-specific private tags (e.g. Philips/Siemens (01F1,1026)) often have wrong-length
# values that pydicom would otherwise log as ERROR on every dataset. Convert them to VR=UN
# instead — `dataset_to_dicom_json` drops tags that fail JSON serialization anyway.
# NOTE: this is a process-wide pydicom setting (also affects anonymization and pipeline
# workers that import this package). Unlike the strict_reading() race fixed in
# converter.py it is write-once at import and never restored, so nothing races on it.
_pydicom_config.convert_wrong_length_to_UN = True

from clarinet.services.dicomweb.cache import DicomWebCache  # noqa: E402
from clarinet.services.dicomweb.cleanup import DicomWebCacheCleanupService  # noqa: E402
from clarinet.services.dicomweb.converter import (  # noqa: E402
    convert_datasets_to_dicom_json,
    dataset_to_dicom_json,
    image_result_to_dicom_json,
    series_result_to_dicom_json,
    study_result_to_dicom_json,
)
from clarinet.services.dicomweb.models import MemoryCachedSeries  # noqa: E402
from clarinet.services.dicomweb.multipart import (  # noqa: E402
    build_multipart_response,
    extract_frames_from_dataset,
)
from clarinet.services.dicomweb.service import DicomWebProxyService  # noqa: E402

__all__ = [
    "DicomWebCache",
    "DicomWebCacheCleanupService",
    "DicomWebProxyService",
    "MemoryCachedSeries",
    "build_multipart_response",
    "convert_datasets_to_dicom_json",
    "dataset_to_dicom_json",
    "extract_frames_from_dataset",
    "image_result_to_dicom_json",
    "series_result_to_dicom_json",
    "study_result_to_dicom_json",
]
