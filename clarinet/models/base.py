"""
Base models for the Clarinet framework.

This module provides the base SQLModel classes and common functionality
used throughout the Clarinet models.
"""

import enum
from typing import Annotated, Any

from annotated_types import Ge, Le
from pydantic import StringConstraints, field_validator
from sqlmodel import SQLModel

# Define common type constraints
DicomUID = Annotated[str, StringConstraints(pattern=r"^[0-9\.]*$", min_length=5, max_length=64)]
InstanceCount = Annotated[int, Ge(0), Le(1_000_000)]

type T = Any


class BaseModel(SQLModel):
    """Base model for all Clarinet models with common validation and utilities."""

    @classmethod
    @field_validator("*", mode="before")
    def empty_to_none(cls, value: T) -> T | None:
        """Convert empty strings to None."""
        if isinstance(value, str):
            value = value.replace("\x00", " ")
            if value == "" or value == "null" or not value:
                return None
        return value


class RecordStatus(str, enum.Enum):
    """Enumeration of possible record status values."""

    blocked = "blocked"
    pending = "pending"
    inwork = "inwork"
    finished = "finished"
    failed = "failed"
    pause = "pause"


class DicomQueryLevel(str, enum.Enum):
    """Enumeration of DICOM query levels."""

    SERIES = "SERIES"
    STUDY = "STUDY"
    PATIENT = "PATIENT"
