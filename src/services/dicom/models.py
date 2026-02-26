"""Pydantic models for DICOM client operations."""

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class QueryRetrieveLevel(str, Enum):
    """DICOM Query/Retrieve levels."""

    PATIENT = "PATIENT"
    STUDY = "STUDY"
    SERIES = "SERIES"
    IMAGE = "IMAGE"


class StudyQuery(BaseModel):
    """Query parameters for C-FIND at study level."""

    patient_id: str | None = None
    patient_name: str | None = None
    study_instance_uid: str | None = None
    study_date: str | None = None
    study_description: str | None = None
    accession_number: str | None = None
    modality: str | None = None


class SeriesQuery(BaseModel):
    """Query parameters for C-FIND at series level."""

    study_instance_uid: str
    series_instance_uid: str | None = None
    series_number: str | None = None
    modality: str | None = None
    series_description: str | None = None


class ImageQuery(BaseModel):
    """Query parameters for C-FIND at image level."""

    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str | None = None
    instance_number: str | None = None


class StudyResult(BaseModel):
    """Study-level C-FIND result."""

    patient_id: str | None = None
    patient_name: str | None = None
    study_instance_uid: str
    study_date: str | None = None
    study_time: str | None = None
    study_description: str | None = None
    accession_number: str | None = None
    modalities_in_study: str | None = None
    number_of_study_related_series: int | None = None
    number_of_study_related_instances: int | None = None


class SeriesResult(BaseModel):
    """Series-level C-FIND result."""

    study_instance_uid: str
    series_instance_uid: str
    series_number: int | None = None
    modality: str | None = None
    series_description: str | None = None
    number_of_series_related_instances: int | None = None


class ImageResult(BaseModel):
    """Image-level C-FIND result."""

    study_instance_uid: str
    series_instance_uid: str
    sop_instance_uid: str
    sop_class_uid: str | None = None
    instance_number: int | None = None
    rows: int | None = None
    columns: int | None = None


class RetrieveRequest(BaseModel):
    """Request for C-GET or C-MOVE operation."""

    level: QueryRetrieveLevel
    patient_id: str | None = None
    study_instance_uid: str | None = None
    series_instance_uid: str | None = None
    sop_instance_uid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for dataset creation."""
        data: dict[str, Any] = {"QueryRetrieveLevel": self.level.value}
        if self.patient_id:
            data["PatientID"] = self.patient_id
        if self.study_instance_uid:
            data["StudyInstanceUID"] = self.study_instance_uid
        if self.series_instance_uid:
            data["SeriesInstanceUID"] = self.series_instance_uid
        if self.sop_instance_uid:
            data["SOPInstanceUID"] = self.sop_instance_uid
        return data


class StorageMode(str, Enum):
    """Storage modes for received DICOM instances."""

    DISK = "disk"  # Save to disk
    MEMORY = "memory"  # Keep in memory
    FORWARD = "forward"  # Forward to another server


class StorageConfig(BaseModel):
    """Configuration for storage handler."""

    mode: StorageMode
    output_dir: Path | None = None  # For DISK mode
    destination_aet: str | None = None  # For FORWARD mode
    destination_host: str | None = None  # For FORWARD mode
    destination_port: int | None = None  # For FORWARD mode


class RetrieveResult(BaseModel):
    """Result of C-GET or C-MOVE operation."""

    status: str
    num_remaining: int = 0
    num_completed: int = 0
    num_failed: int = 0
    num_warning: int = 0
    failed_sop_instances: list[str] = Field(default_factory=list)
    instances: dict[str, Any] = Field(default_factory=dict)  # For C-GET with memory mode


class DicomNode(BaseModel):
    """DICOM node configuration."""

    aet: str
    host: str
    port: int


class PacsStudyWithSeries(BaseModel):
    """StudyResult enriched with series list and local DB existence flag."""

    study: StudyResult
    series: list[SeriesResult] = Field(default_factory=list)
    already_exists: bool = False


class PacsImportRequest(BaseModel):
    """Request body for importing a study from PACS."""

    study_instance_uid: str
    patient_id: str


class AssociationConfig(BaseModel):
    """Configuration for DICOM association."""

    calling_aet: str
    called_aet: str
    peer_host: str
    peer_port: int
    max_pdu: int = 16384
    timeout: float = 30.0
