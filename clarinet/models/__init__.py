"""
Clarinet framework data models.

This package contains the SQLModel-based models that define the database schema
and data structures used throughout the Clarinet framework.
"""

# Admin dashboard schemas
from .admin import AdminStats, RecordTypeStats, RoleMatrixResponse, UserRoleInfo

# Authentication models
from .auth import AccessToken

# Base models
from .base import BaseModel, DicomQueryLevel, DicomUID, RecordStatus, ViewerMode

# Auto-ID counters
from .counter import AutoIdCounter, patient_auto_id_seq

# File schema models
from .file_schema import (
    FileDefinition,
    FileDefinitionRead,
    FileRole,
    RecordFileLink,
    RecordTypeFileLink,
)

# Patient models
from .patient import Patient, PatientBase, PatientInfo, PatientRead, PatientSave

# Pipeline definition models
from .pipeline_definition import PipelineDefinition, PipelineDefinitionRead

# Record models (formerly Task)
from .record import (
    Record,
    RecordBase,
    RecordContextInfoUpdate,
    RecordCreate,
    RecordFind,
    RecordFindResult,
    RecordFindResultComparisonOperator,
    RecordOptional,
    RecordPage,
    RecordRead,
    RecordSearchFilter,
    RecordSearchQuery,
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

# Report models (custom SQL reports)
from .report import ReportFormat, ReportTemplate

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
from .user import User, UserCreate, UserRead, UserRole, UserRoleCreate, UserRolesLink, UserUpdate

__all__ = [
    "AccessToken",
    "AdminStats",
    "AutoIdCounter",
    "BaseModel",
    "DicomQueryLevel",
    "DicomUID",
    "FileDefinition",
    "FileDefinitionRead",
    "FileRole",
    "Patient",
    "PatientBase",
    "PatientInfo",
    "PatientRead",
    "PatientSave",
    "PipelineDefinition",
    "PipelineDefinitionRead",
    "Record",
    "RecordBase",
    "RecordContextInfoUpdate",
    "RecordCreate",
    "RecordFileLink",
    "RecordFind",
    "RecordFindResult",
    "RecordFindResultComparisonOperator",
    "RecordOptional",
    "RecordPage",
    "RecordRead",
    "RecordSearchFilter",
    "RecordSearchQuery",
    "RecordStatus",
    "RecordType",
    "RecordTypeBase",
    "RecordTypeCreate",
    "RecordTypeFileLink",
    "RecordTypeFind",
    "RecordTypeOptional",
    "RecordTypeRead",
    "RecordTypeStats",
    "ReportFormat",
    "ReportTemplate",
    "RoleMatrixResponse",
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
    "UserRoleCreate",
    "UserRoleInfo",
    "UserRolesLink",
    "ViewerMode",
    "patient_auto_id_seq",
]
