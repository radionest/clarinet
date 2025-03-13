"""
Study and series models for the Clarinet framework.

This module provides models for medical imaging studies and series.
"""

from datetime import date
from typing import Optional, List, Dict, Any, Union, ForwardRef

from sqlmodel import SQLModel, Field, Relationship, Column
from pydantic import computed_field

from ..settings import settings
from ..utils.logger import logger

from .patient import Patient, PatientBase
from .base import BaseModel, DicomUID


class StudyBase(BaseModel):
    """Base model for study data."""
    
    study_uid: DicomUID = Field()
    date: date
    anon_uid: Optional[DicomUID] = None
    patient_id: str


class Study(StudyBase, table=True):
    """Model representing a medical imaging study in the system."""

    study_uid: DicomUID = Field(primary_key=True)
    patient_id: str = Field(foreign_key="patient.id")
    anon_uid: Optional[str] = None

    patient: Patient = Relationship(back_populates="studies")
    series: List["Series"] = Relationship(back_populates="study")
    tasks: List["Task"] = Relationship(back_populates="study")


class StudyCreate(StudyBase):
    """Pydantic model for creating a new study."""
    
    study_uid: DicomUID = Field()
    patient_id: str


class StudyRead(StudyBase):
    """Pydantic model for reading study data with related entities."""
    
    patient: PatientBase
    series: List["SeriesBase"] = []


class SeriesBase(BaseModel):
    """Base model for series data."""
    
    series_uid: Optional[DicomUID] = None
    series_description: Optional[str] = Field(min_length=0, max_length=64, default=None)
    series_number: int = Field(gt=0, lt=100000)
    anon_uid: Optional[DicomUID] = Field(default=None, min_length=5, max_length=64)
    study_uid: Optional[DicomUID] = Field(default=None)


class Series(SeriesBase, table=True):
    """Model representing a series within a medical imaging study."""

    series_uid: DicomUID = Field(primary_key=True)
    anon_uid: Optional[str] = None
    series_description: Optional[str] = Field(default=None)
    series_number: int

    study_uid: str = Field(foreign_key="study.study_uid")
    study: Study = Relationship(back_populates="series")

    tasks: List["Task"] = Relationship(back_populates="series")


class SeriesRead(SeriesBase):
    """Pydantic model for reading series data with related entities."""
    
    study: StudyRead
    tasks: List["TaskRead"] = []

    def _format_path(self, unformated_path: str) -> Optional[str]:
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

    @computed_field(return_type=str)
    @property
    def working_folder(self)->Optional[str]:
        """Get the full path to the working folder for this series."""
        return self._format_path(
            "{clarinet_storage_path}/{patient_id}/{study_anon_uid}/{series_anon_uid}"
        )


class SeriesCreate(SeriesBase):
    """Pydantic model for creating a new series."""
    
    series_uid: DicomUID
    series_description: Optional[str] = Field(min_length=0, max_length=64, default=None)
    series_number: Optional[int] = Field(gt=0, lt=100000) # type: ignore
    anon_uid: Optional[DicomUID] = Field(default=None, min_length=5, max_length=64)
    study_uid: DicomUID = Field()


class SeriesFind(SeriesBase):
    """Pydantic model for searching series."""
    
    series_uid: Optional[str] = None
    series_description: Optional[str] = None
    series_number: Optional[int] = None  # type: ignore
    anon_uid: Optional[str] = None
    study_uid: Optional[str] = None
    tasks: List["TaskFind"] = []


# Forward references for relationships
TaskRead = ForwardRef("TaskRead")
TaskFind = ForwardRef("TaskFind")