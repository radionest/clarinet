"""
Clarinet framework data models.

This package contains the SQLModel-based models that define the database schema
and data structures used throughout the Clarinet framework.
"""

# Authentication models
from .auth import AccessToken

# Base models
from .base import BaseModel, DicomQueryLevel, DicomUID, RecordStatus

# File schema models
from .file_schema import FileDefinition

# Patient models
from .patient import Patient, PatientBase, PatientRead, PatientSave

# Record models (formerly Task)
from .record import (
    Record,
    RecordBase,
    RecordCreate,
    RecordDataSchema,
    RecordFind,
    RecordFindResult,
    RecordFindResultComparisonOperator,
    RecordRead,
    RecordType,
    RecordTypeBase,
    RecordTypeCreate,
    RecordTypeFind,
    RecordTypeOptional,
    SlicerSettings,
)

# Study models
from .study import (
    Series,
    SeriesBase,
    SeriesCreate,
    SeriesFind,
    SeriesRead,
    Study,
    StudyBase,
    StudyCreate,
    StudyRead,
)

# User models
from .user import User, UserRead, UserRole, UserRolesLink

__all__ = [
    # Authentication
    "AccessToken",
    # Base
    "BaseModel",
    "DicomQueryLevel",
    "DicomUID",
    # File schema
    "FileDefinition",
    # Patient
    "Patient",
    "PatientBase",
    "PatientRead",
    "PatientSave",
    # Record (formerly Task)
    "Record",
    "RecordBase",
    "RecordCreate",
    "RecordDataSchema",
    "RecordFind",
    "RecordFindResult",
    "RecordFindResultComparisonOperator",
    "RecordRead",
    "RecordStatus",
    "RecordType",
    "RecordTypeBase",
    "RecordTypeCreate",
    "RecordTypeFind",
    "RecordTypeOptional",
    "Series",
    "SeriesBase",
    "SeriesCreate",
    "SeriesFind",
    "SeriesRead",
    "SlicerSettings",
    # Study
    "Study",
    "StudyBase",
    "StudyCreate",
    "StudyRead",
    # User
    "User",
    "UserRead",
    "UserRole",
    "UserRolesLink",
]
