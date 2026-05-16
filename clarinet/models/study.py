"""
Study and series models for the Clarinet framework.

This module provides models for medical imaging studies and series.
"""

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlmodel import Field, Relationship

from .base import BaseModel, DicomUID, InstanceCount
from .patient import Patient, PatientInfo

if TYPE_CHECKING:
    from .record import Record, RecordFind


class StudyBase(BaseModel):
    """Base model for study data."""

    study_uid: DicomUID = Field()
    date: date
    anon_uid: str | None = None
    study_description: str | None = Field(default=None, max_length=256)
    modalities_in_study: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Modalities of the study, DICOM-standard '\\'-joined (e.g. "
            "'CT\\SR'); see clarinet.services.dicom.models.MODALITIES_SEPARATOR. "
            "Written by clarinet.services.dicom.operations._ds_modalities; "
            "consumed by _modalities_string (path rendering, converts to '_') "
            "and _modalities_to_list (DICOMweb JSON, splits to array)."
        ),
    )
    patient_id: str


class Study(StudyBase, table=True):
    """Model representing a medical imaging study in the system."""

    study_uid: DicomUID = Field(primary_key=True)
    patient_id: str = Field(foreign_key="patient.id", ondelete="CASCADE")
    anon_uid: str | None = None

    patient: Patient = Relationship(back_populates="studies")
    series: list["Series"] = Relationship(back_populates="study", cascade_delete=True)
    records: list["Record"] = Relationship(back_populates="study", cascade_delete=True)


class StudyCreate(StudyBase):
    """Pydantic model for creating a new study."""

    study_uid: DicomUID = Field()
    patient_id: str


class StudyRead(StudyBase):
    """Pydantic model for reading study data with related entities."""

    patient: PatientInfo
    series: list["SeriesBase"] = Field()


class SeriesBase(BaseModel):
    """Base model for series data."""

    series_uid: DicomUID | None = None
    series_description: str | None = Field(min_length=0, max_length=64, default=None)
    series_number: int = Field(gt=0, lt=100000)
    modality: str | None = Field(default=None, max_length=16)
    instance_count: InstanceCount | None = Field(default=None)
    anon_uid: str | None = Field(default=None)
    study_uid: DicomUID | None = Field(default=None)


class Series(SeriesBase, table=True):
    """Model representing a series within a medical imaging study."""

    series_uid: DicomUID = Field(primary_key=True)
    anon_uid: str | None = None
    series_description: str | None = Field(default=None)
    series_number: int

    study_uid: str = Field(foreign_key="study.study_uid", ondelete="CASCADE")
    study: Study = Relationship(back_populates="series")

    records: list["Record"] = Relationship(back_populates="series", cascade_delete=True)


class SeriesRead(SeriesBase):
    """Pydantic model for reading series data with related entities."""

    study: StudyRead
    records: list[Any] = Field(default_factory=list)  # Will contain RecordRead objects


class SeriesCreate(SeriesBase):
    """Pydantic model for creating a new series."""

    series_uid: DicomUID
    series_description: str | None = Field(min_length=0, max_length=64, default=None)
    series_number: int | None = Field(gt=0, lt=100000)  # type: ignore
    anon_uid: DicomUID | None = Field(default=None, min_length=5, max_length=64)
    study_uid: DicomUID = Field()


class SeriesFind(SeriesBase):
    """Pydantic model for searching series."""

    series_uid: str | None = None
    series_description: str | None = None
    series_number: int | None = Field(default=None, gt=0, lt=100000)  # type: ignore
    modality: str | None = None
    instance_count: InstanceCount | None = None
    anon_uid: str | None = None
    study_uid: str | None = None
    records: list["RecordFind"] = Field(default_factory=list)  # Will contain RecordFind objects
