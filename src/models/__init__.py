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
from .file_schema import (
    FileDefinition,
    FileDefinitionRead,
    FileRole,
    RecordFileLink,
    RecordTypeFileLink,
)

# Patient models
from .patient import Patient, PatientBase, PatientRead, PatientSave

# Pipeline definition models
from .pipeline_definition import PipelineDefinition, PipelineDefinitionRead

# Record models (formerly Task)
from .record import (
    Record,
    RecordBase,
    RecordCreate,
    RecordFind,
    RecordFindResult,
    RecordFindResultComparisonOperator,
    RecordRead,
)

# Record type models
from .record_type import (
    RecordType,
    RecordTypeBase,
    RecordTypeCreate,
    RecordTypeFind,
    RecordTypeOptional,
    RecordTypeRead,
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
    "AccessToken",
    "BaseModel",
    "DicomQueryLevel",
    "DicomUID",
    "FileDefinition",
    "FileDefinitionRead",
    "FileRole",
    "Patient",
    "PatientBase",
    "PatientRead",
    "PatientSave",
    "PipelineDefinition",
    "PipelineDefinitionRead",
    "Record",
    "RecordBase",
    "RecordCreate",
    "RecordFileLink",
    "RecordFind",
    "RecordFindResult",
    "RecordFindResultComparisonOperator",
    "RecordRead",
    "RecordStatus",
    "RecordType",
    "RecordTypeBase",
    "RecordTypeCreate",
    "RecordTypeFileLink",
    "RecordTypeFind",
    "RecordTypeOptional",
    "RecordTypeRead",
    "Series",
    "SeriesBase",
    "SeriesCreate",
    "SeriesFind",
    "SeriesRead",
    "SlicerSettings",
    "Study",
    "StudyBase",
    "StudyCreate",
    "StudyRead",
    "User",
    "UserRead",
    "UserRole",
    "UserRolesLink",
]
