"""
Clarinet framework data models.

This package contains the SQLModel-based models that define the database schema
and data structures used throughout the Clarinet framework.
"""

# Base models
from .base import (
    BaseModel,
    DicomUID,
    TaskStatus,
    DicomQueryLevel
)

# User models
from .user import (
    User, 
    UserBase,
    UserRead,
    UserRole,
    UserRolesLink,
    HTTPSession
)

# Patient models
from .patient import (
    Patient,
    PatientBase,
    PatientRead,
    PatientSave
)

# Study models
from .study import (
    Study,
    StudyBase,
    StudyCreate,
    StudyRead,
    Series,
    SeriesBase,
    SeriesCreate,
    SeriesFind,
    SeriesRead
)

# Task models
from .task import (
    Task,
    TaskBase,
    TaskCreate,
    TaskFind,
    TaskRead,
    TaskType,
    TaskTypeBase,
    TaskTypeCreate,
    TaskTypeFind,
    TaskTypeOptional,
    TaskFindResult,
    TaskFindResultComparisonOperator
)

__all__ = [
    # Base
    'BaseModel', 'DicomUID', 'TaskStatus', 'DicomQueryLevel',
    
    # User
    'User', 'UserBase', 'UserRead', 'UserRole', 'UserRolesLink', 'HTTPSession',
    
    # Patient
    'Patient', 'PatientBase', 'PatientRead', 'PatientSave',
    
    # Study
    'Study', 'StudyBase', 'StudyCreate', 'StudyRead',
    'Series', 'SeriesBase', 'SeriesCreate', 'SeriesFind', 'SeriesRead',
    
    # Task
    'Task', 'TaskBase', 'TaskCreate', 'TaskFind', 'TaskRead',
    'TaskType', 'TaskTypeBase', 'TaskTypeCreate', 'TaskTypeFind', 'TaskTypeOptional',
    'TaskFindResult', 'TaskFindResultComparisonOperator'
]