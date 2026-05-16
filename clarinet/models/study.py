"""
Study and series models for the Clarinet framework.

This module provides models for medical imaging studies and series.
"""

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlmodel import Field, Relationship

from ..exceptions import AnonPathError
from ..settings import settings
from ..utils.anon_resolve import require_anon_or_raw
from .base import BaseModel, DicomQueryLevel, DicomUID, InstanceCount
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

    # Path-resolution field. Previously @computed_field; now plain optional
    # field (default ``None``). Callers that need an on-disk path must
    # construct ``FileRepository(series).working_dir`` explicitly. Frontend
    # decodes this as ``Option(String)`` and tolerates ``null``.
    working_folder: str | None = None

    def _format_path_strict(
        self,
        unformatted_path: str,
        *,
        fallback_to_unanonymized: bool = False,
    ) -> str:
        """Format a path with values from this series.

        Raises on failure — use for system templates where all placeholders
        are guaranteed to exist (e.g. working_folder).

        Args:
            unformatted_path: Template string with ``{placeholder}`` tokens.
            fallback_to_unanonymized: If ``False`` (default — backend safe
                mode), missing ``anon_id``/``anon_uid`` raise
                ``AnonPathError`` instead of silently rendering against raw
                identifiers.
        """
        patient = self.study.patient
        patient_id = require_anon_or_raw(
            anon=patient.anon_id,
            raw=patient.id,
            level=DicomQueryLevel.PATIENT,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        study_anon_uid = require_anon_or_raw(
            anon=self.study.anon_uid,
            raw=self.study_uid,
            level=DicomQueryLevel.STUDY,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        series_anon_uid = require_anon_or_raw(
            anon=self.anon_uid,
            raw=self.series_uid,
            level=DicomQueryLevel.SERIES,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )

        return unformatted_path.format(
            patient_id=patient_id,
            patient_anon_name=patient.anon_name,
            study_uid=self.study_uid,
            study_anon_uid=study_anon_uid,
            series_uid=self.series_uid,
            series_anon_uid=series_anon_uid,
            clarinet_storage_path=settings.storage_path,
        )

    def _format_path(self, unformatted_path: str) -> str | None:
        """Format a path template, returning None on failure.

        Safe wrapper for user-defined templates where unknown placeholders
        are expected. Uses ``fallback_to_unanonymized=True`` because user
        templates target the UX layer.
        """
        try:
            return self._format_path_strict(unformatted_path, fallback_to_unanonymized=True)
        except (AttributeError, KeyError, AnonPathError):
            return None


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
