"""
Base models for the Clarinet framework.

This module provides the base SQLModel classes and common functionality
used throughout the Clarinet models.
"""

import enum
from datetime import datetime, date, timedelta, UTC
from pydoc import classify_class_attrs
from typing import Optional, List, Dict, Any, Self, Annotated

from sqlmodel import SQLModel, Field, Relationship, Column, JSON
from pydantic import computed_field, field_validator, StringConstraints, constr

# Define common type constraints
DicomUID = Annotated[str, StringConstraints(pattern=r"^[0-9\.]*$", min_length=5, max_length=64)]

type T = Any

class BaseModel(SQLModel):
    """Base model for all Clarinet models with common validation and utilities."""

    @classmethod
    @field_validator("*", mode="before")
    def empty_to_none(cls, value: T) -> Optional[T]:      
        """Convert empty strings to None."""
        if isinstance(value, str):
            value = value.replace("\x00", " ")
            if value == "" or value == "null" or not value:
                return None
        return value


class TaskStatus(str, enum.Enum):
    """Enumeration of possible task status values."""
    
    pending = "pending"
    inwork = "inwork"
    finished = "finished"
    failed = "failed"
    pause = "pause"


class DicomQueryLevel(str, enum.Enum):
    """Enumeration of DICOM query levels."""
    
    series = "series"
    study = "study"
    patient = "patient"