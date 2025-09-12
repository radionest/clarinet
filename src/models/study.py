"""
Study and series models for the Clarinet framework.

This module provides models for medical imaging studies and series.
"""

from datetime import date
from typing import TYPE_CHECKING

from pydantic import computed_field
from sqlmodel import Field, Relationship

from ..settings import settings
from ..utils.logger import logger
from .base import BaseModel, DicomUID
from .patient import Patient, PatientBase

if TYPE_CHECKING:
    from .task import Task, TaskFind, TaskRead


class StudyBase(BaseModel):
    """Base model for study data."""

    study_uid: DicomUID = Field()
    date: date
    anon_uid: DicomUID | None = None
    patient_id: str


class Study(StudyBase, table=True):
    """Model representing a medical imaging study in the system."""

    study_uid: DicomUID = Field(primary_key=True)
    patient_id: str = Field(foreign_key="patient.id")
    anon_uid: str | None = None

    patient: Patient = Relationship(back_populates="studies")
    series: list["Series"] = Relationship(back_populates="study")
    tasks: list["Task"] = Relationship(back_populates="study")


class StudyCreate(StudyBase):
    """Pydantic model for creating a new study."""

    study_uid: DicomUID = Field()
    patient_id: str


class StudyRead(StudyBase):
    """Pydantic model for reading study data with related entities."""

    patient: PatientBase
    series: list["SeriesBase"] = Field()


class SeriesBase(BaseModel):
    """Base model for series data."""

    series_uid: DicomUID | None = None
    series_description: str | None = Field(min_length=0, max_length=64, default=None)
    series_number: int = Field(gt=0, lt=100000)
    anon_uid: DicomUID | None = Field(default=None, min_length=5, max_length=64)
    study_uid: DicomUID | None = Field(default=None)


class Series(SeriesBase, table=True):
    """Model representing a series within a medical imaging study."""

    series_uid: DicomUID = Field(primary_key=True)
    anon_uid: str | None = None
    series_description: str | None = Field(default=None)
    series_number: int

    study_uid: str = Field(foreign_key="study.study_uid")
    study: Study = Relationship(back_populates="series")

    tasks: list["Task"] = Relationship(back_populates="series")


class SeriesRead(SeriesBase):
    """Pydantic model for reading series data with related entities."""

    study: StudyRead
    tasks: list["TaskRead"] = Field()

    def _format_path(self, unformated_path: str) -> str | None:
        """Format a path with values from this series."""
        try:
            return unformated_path.format(
                patient_id=self.study.patient.anon_id,
                patient_anon_name=self.study.patient.anon_name,
                study_uid=self.study_uid,
                study_anon_uid=self.study.anon_uid,
                series_uid=self.series_uid,
                series_anon_uid=self.anon_uid,
                clarinet_storage_path=settings.storage_path,
            )
        except AttributeError as e:
            logger.error(e)
            return None

    @computed_field
    def working_folder(self) -> str | None:
        """Get the full path to the working folder for this series."""
        return self._format_path(
            "{clarinet_storage_path}/{patient_id}/{study_anon_uid}/{series_anon_uid}"
        )


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
    series_number: int | None = None  # type: ignore
    anon_uid: str | None = None
    study_uid: str | None = None
    tasks: list["TaskFind"] = Field()
