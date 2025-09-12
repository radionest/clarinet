"""
Clarinet framework data models.

This package contains the SQLModel-based models that define the database schema
and data structures used throughout the Clarinet framework.
"""

# Base models
from .base import BaseModel, DicomQueryLevel, DicomUID, TaskStatus

# Patient models
from .patient import Patient, PatientBase, PatientRead, PatientSave

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

# Task models
from .task import (
    Task,
    TaskBase,
    TaskCreate,
    TaskDesign,
    TaskDesignBase,
    TaskDesignCreate,
    TaskDesignFind,
    TaskDesignOptional,
    TaskFind,
    TaskFindResult,
    TaskFindResultComparisonOperator,
    TaskRead,
)

# User models
from .user import User, UserRead, UserRole, UserRolesLink

__all__ = [
    # Base
    "BaseModel",
    "DicomQueryLevel",
    "DicomUID",
    # Patient
    "Patient",
    "PatientBase",
    "PatientRead",
    "PatientSave",
    "Series",
    "SeriesBase",
    "SeriesCreate",
    "SeriesFind",
    "SeriesRead",
    # Study
    "Study",
    "StudyBase",
    "StudyCreate",
    "StudyRead",
    # Task
    "Task",
    "TaskBase",
    "TaskCreate",
    "TaskDesign",
    "TaskDesignBase",
    "TaskDesignCreate",
    "TaskDesignFind",
    "TaskDesignOptional",
    "TaskFind",
    "TaskFindResult",
    "TaskFindResultComparisonOperator",
    "TaskRead",
    "TaskStatus",
    # User
    "User",
    "UserRead",
    "UserRole",
    "UserRolesLink",
]
