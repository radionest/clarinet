"""
Patient-related models for the Clarinet framework.

This module provides models for patients, studies, and series.
"""

from datetime import date
from typing import Optional, List, Dict, Any, ForwardRef

from sqlmodel import SQLModel, Field, Relationship, Column, Identity
from pydantic import computed_field, Field as PydanticField

from .base import BaseModel, DicomUID

from ..settings import settings

class PatientBase(BaseModel):
    """Base model for patient data."""
    
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default=None, min_length=1, max_length=64)
    anon_name: Optional[str] = Field(
        default=None, min_length=5, max_length=50, unique=True
    )
    auto_id: Optional[int] = Field(default=None)

    @computed_field(return_type=str)
    @property
    def anon_id(self) -> str:
        """Generate an anonymous ID using the auto_id."""
        return f"{settings.anon_id_prefix}_{self.auto_id}"


class Patient(PatientBase, table=True):
    """Model representing a patient in the system."""

    id: str = Field(
        primary_key=True,
        min_length=1, max_length=64,
        schema_extra={"validation_alias": "patient_id"},
    )
    studies: List["Study"] = Relationship(back_populates="patient")
    auto_id: Optional[int] = Field(
        default=None, sa_column=Column(Identity(always=True))
    )
    tasks: List["Task"] = Relationship(back_populates="patient")


class PatientSave(PatientBase):
    """Pydantic model for creating a new patient."""
    
    id: str = Field(min_length=1, max_length=64, schema_extra={"validation_alias": "patient_id"})


class PatientRead(PatientBase):
    """Pydantic model for reading patient data with related studies."""
    
    studies: List["StudyRead"] = []


# Forward references for relationships
StudyRead = ForwardRef("StudyRead")