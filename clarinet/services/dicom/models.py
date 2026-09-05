"""Clarinet-specific DICOM models.

The generic Q/R vocabulary (queries, C-FIND results, ``DicomNode``,
``RetrieveResult``) lives in ``dimsechord`` and is re-exported here so
``from clarinet.services.dicom.models import ...`` keeps working. Only models
that carry Clarinet domain meaning — anonymization results, PACS import
requests — are defined locally.
"""

from typing import Literal

from dimsechord import (
    BatchStoreResult,
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
from pydantic import BaseModel, Field

#: Separator used to join multi-value ``ModalitiesInStudy`` into a single
#: string for ``Study.modalities_in_study`` / ``StudyResult.modalities_in_study``.
#: This is the DICOM-standard value-multiplicity separator (PS3.5 §6.4):
#: storing in this format keeps the DB value byte-identical to the wire
#: representation, so re-serialising to DICOM / DICOMweb is a no-op rather
#: than a join-then-split round-trip.
#:
#: The producer is dimsechord's C-FIND result mapper, which uses the same
#: constant; consumers (``files._storage._modalities_string`` for filesystem
#: paths, ``dicomweb.converter._modalities_to_list`` for DICOM JSON arrays)
#: must agree on this character. Path rendering converts the joined value to
#: ``_``-separated for filesystem safety (see ``_modalities_string``).
MODALITIES_SEPARATOR = "\\"


class SkippedSeriesInfo(BaseModel):
    """Info about a series skipped during anonymization."""

    series_uid: str
    modality: str | None = None
    series_description: str | None = None
    reason: str


class AnonymizationResult(BaseModel):
    """Result of a study anonymization operation."""

    study_uid: str
    anon_study_uid: str
    anon_patient_id: str | None = None
    series_count: int
    series_anonymized: int = 0
    series_skipped: int = 0
    instances_anonymized: int
    instances_failed: int
    instances_send_failed: int = 0
    send_failed_by_node: dict[str, int] = Field(default_factory=dict)
    output_dir: str | None = None
    sent_to_pacs: bool = False
    skipped_series: list[SkippedSeriesInfo] = Field(default_factory=list)


class AnonymizeStudyRequest(BaseModel):
    """Request body for anonymizing a study."""

    save_to_disk: bool | None = None
    send_to_pacs: bool | None = None
    per_study_patient_id: bool | None = None


class BackgroundAnonymizationStatus(BaseModel):
    """Response returned when anonymization is dispatched in the background."""

    status: Literal["started"] = "started"
    study_uid: str


class PacsStudyWithSeries(BaseModel):
    """StudyResult enriched with series list and local DB existence flag."""

    study: StudyResult
    series: list[SeriesResult] = Field(default_factory=list)
    already_exists: bool = False


class PacsImportRequest(BaseModel):
    """Request body for importing a study from PACS."""

    study_instance_uid: str
    patient_id: str


__all__ = [
    "MODALITIES_SEPARATOR",
    "AnonymizationResult",
    "AnonymizeStudyRequest",
    "BackgroundAnonymizationStatus",
    "BatchStoreResult",
    "DicomNode",
    "ImageQuery",
    "ImageResult",
    "PacsImportRequest",
    "PacsStudyWithSeries",
    "QueryRetrieveLevel",
    "RetrieveResult",
    "SeriesQuery",
    "SeriesResult",
    "SkippedSeriesInfo",
    "StudyQuery",
    "StudyResult",
]
