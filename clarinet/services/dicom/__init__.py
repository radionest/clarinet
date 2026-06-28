"""DICOM client for query-retrieve operations. Core models live in dimsechord."""

from dimsechord import (
    BatchStoreResult,
    DicomClient,
    DicomNode,
    ImageQuery,
    ImageResult,
    QueryRetrieveLevel,
    RetrieveResult,
    SeriesQuery,
    SeriesResult,
    StudyQuery,
    StudyResult,
)
from pynetdicom import _config as _pynetdicom_config

from clarinet.services.dicom.anonymizer import DicomAnonymizer
from clarinet.services.dicom.models import (
    AnonymizationResult,
    AnonymizeStudyRequest,
    BackgroundAnonymizationStatus,
    PacsImportRequest,
    PacsStudyWithSeries,
    SkippedSeriesInfo,
)
from clarinet.services.dicom.series_filter import (
    SeriesFilter,
    SeriesFilterCriteria,
    SeriesFilterResult,
)
from clarinet.settings import settings

# Process-wide pynetdicom identifier logging toggle (independent of dimsechord).
_pynetdicom_config.LOG_RESPONSE_IDENTIFIERS = settings.dicom_log_identifiers
_pynetdicom_config.LOG_REQUEST_IDENTIFIERS = settings.dicom_log_identifiers

__all__ = [
    "AnonymizationResult",
    "AnonymizeStudyRequest",
    "BackgroundAnonymizationStatus",
    "BatchStoreResult",
    "DicomAnonymizer",
    "DicomClient",
    "DicomNode",
    "ImageQuery",
    "ImageResult",
    "PacsImportRequest",
    "PacsStudyWithSeries",
    "QueryRetrieveLevel",
    "RetrieveResult",
    "SeriesFilter",
    "SeriesFilterCriteria",
    "SeriesFilterResult",
    "SeriesQuery",
    "SeriesResult",
    "SkippedSeriesInfo",
    "StudyQuery",
    "StudyResult",
]
