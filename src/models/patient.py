"""
Patient-related models for the Clarinet framework.

This module provides models for patients, studies, and series.
"""

from typing import TYPE_CHECKING

from pydantic import computed_field
from sqlmodel import Column, Field, Integer, Relationship

from ..settings import settings
from .base import BaseModel

if TYPE_CHECKING:
    from .record import Record
    from .study import Study, StudyRead


class PatientBase(BaseModel):
    """Base model for patient data."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default=None, min_length=1, max_length=64)
    anon_name: str | None = Field(default=None, min_length=5, max_length=50, unique=True)
    auto_id: int | None = Field(default=None)

    @computed_field
    def anon_id(self) -> str | None:
        """Generate an anonymous ID using the auto_id."""
        if self.auto_id is None:
            return None
        return f"{settings.anon_id_prefix}_{self.auto_id}"


class Patient(PatientBase, table=True):
    """Model representing a patient in the system."""

    id: str = Field(
        primary_key=True,
        min_length=1,
        max_length=64,
    )
    studies: list[Study] = Relationship(back_populates="patient")
    auto_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            nullable=True,  # SQLite needs nullable for autoincrement non-primary key
            autoincrement=True,
            unique=True,
        ),
    )
    records: list[Record] = Relationship(back_populates="patient")


class PatientSave(PatientBase):
    """Pydantic model for creating a new patient."""

    id: str = Field(min_length=1, max_length=64, alias="patient_id")
    name: str = Field(min_length=1, max_length=64, alias="patient_name")


class PatientRead(PatientBase):
    """Pydantic model for reading patient data with related studies."""

    studies: list[StudyRead] = Field()
