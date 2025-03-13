"""
Task-related models for the Clarinet framework.

This module provides models for tasks, task types, and task results.
"""

from datetime import datetime, UTC
from typing import Optional, List, Dict, Any, ForwardRef, Self, Union
from enum import Enum

from sqlmodel import SQLModel, Field, Relationship, Column, JSON
from sqlalchemy import Boolean, func, event, String, Integer, Float

from pydantic import computed_field, field_validator

from .base import BaseModel, DicomQueryLevel, TaskStatus
from .user import User, UserRole
from .patient import Patient
from .study import Study, Series

from ..settings import settings

class TaskTypeBase(SQLModel):
    """Base model for task type data."""
    
    name: str
    description: Optional[str] = None
    result_schema: Optional[Dict] = {}
    label: Optional[str] = None
    slicer_script: Optional[str] = None
    slicer_script_args: Optional[Dict] = None
    slicer_result_validator: Optional[str] = None
    slicer_result_validator_args: Optional[Dict] = None

    role_name: Optional[str] = Field(default=None)
    max_users: Optional[int] = Field(default=None)
    min_users: Optional[int] = Field(default=1)
    level: Optional[DicomQueryLevel] = None


class TaskType(TaskTypeBase, table=True):
    """Model representing a type of task that can be performed."""

    id: Optional[int] = Field(default=None, primary_key=True)
    result_schema: Optional[Dict] = Field(default_factory=dict, sa_column=Column(JSON))
    level: DicomQueryLevel

    slicer_script_args: Optional[Dict] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    slicer_result_validator_args: Optional[Dict] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )

    role_name: Optional[str] = Field(foreign_key="userrole.name", default=None)
    constraint_role: Optional[UserRole] = Relationship(back_populates="allowed_tasks")

    tasks: "Task" = Relationship(back_populates="task_type")

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.id == other.id


class TaskTypeCreate(TaskTypeBase):
    """Pydantic model for creating a new task type."""
    pass


class TaskTypeOptional(TaskTypeBase):
    """Pydantic model for updating a task type with optional fields."""
    
    id: Optional[int] = None 
    name: Optional[str] = None # type: ignore
    description: Optional[str] = None
    result_schema: Optional[Dict] = None

    role_name: Optional[str] = Field(default=None)
    max_users: Optional[int] = Field(default=None)
    min_users: Optional[int] = Field(default=None)
    level: Optional[DicomQueryLevel] = None


class TaskTypeFind(SQLModel):
    """Pydantic model for searching task types."""
    
    name: Optional[str] = Field(default=None)
    constraint_role: Optional[str] = Field(default=None)
    constraint_user_num: Optional[int] = Field(default=None)


class TaskFindResultComparisonOperator(str, Enum):
    """Enumeration of comparison operators for task result searches."""
    
    eq = "eq"
    lt = "lt"
    gt = "gt"
    contains = "contains"


class TaskFindResult(SQLModel):
    """Model for specifying search criteria for task results."""
    
    result_name: str
    result_value: Union[str, bool, int, float]
    comparison_operator: Optional[TaskFindResultComparisonOperator] = Field(
        default=TaskFindResultComparisonOperator.eq
    )

    @computed_field(return_type=type)
    @property
    def sql_type(self) -> (type[String] | type[Boolean] | type[Integer] | type[Float]):
        """Determine the appropriate SQL type based on the result value."""
        
        match self.result_value:
            case str():
                return String
            case bool():
                return Boolean
            case int():
                return Integer
            case float():
                return Float
            case _:
                raise NotImplementedError("Unsupported result type")


class TaskBase(BaseModel):
    """Base model for task data."""
    
    info: Optional[str] = Field(default=None, max_length=3000)
    status: TaskStatus = TaskStatus.pending

    study_uid: str
    series_uid: Optional[str] = None
    task_type_id: int
    user_id: Optional[str] = None

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            raise NotImplementedError
        return self.id == other.id

    @computed_field(return_type=str) # type: ignore[prop-decorator]
    @property
    def radiant(self) -> Optional[str]:
        """Generate a radiant URL for this task."""
        if not hasattr(self, "study") or self.study is None:
            return None
        if self.study.anon_uid:
            return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={self.study.anon_uid}"
        return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={self.study.study_uid}"

    def _format_path(self, unformated_path: str) -> Optional[str]:
        """Format a path template with values from this task."""
        try:
            return unformated_path.format(
                patient_id=self.study.patient.anon_id,
                patient_anon_name=self.study.patient.anon_name,
                study_uid=self.study_uid,
                study_anon_uid=self.study.anon_uid,
                series_uid=self.series_uid,
                series_anon_uid=self.series.anon_uid if hasattr(self, "series") and self.series else None,
                user_id=self.user_id,
                clarinet_storage_path=self.clarinet_storage_path,
            )
        except AttributeError as e:
            from clarinet.utils.logger import logger
            logger.error(e)
            return None

    def _format_slicer_kwargs(self, slicer_kwargs: dict[str,str]) -> dict[str,Optional[str]]:
        """Format Slicer script arguments with values from this task."""
        if slicer_kwargs is None:
            return {}
        return {k: self._format_path(v) for k, v in slicer_kwargs.items()}

    @computed_field(return_type=dict)
    @property
    def slicer_args_formated(self) -> Optional[Dict]:
        """Get formatted Slicer script arguments."""
        if not hasattr(self, 'task_type') or self.task_type is None or self.task_type.slicer_script_args is None:
            return None
        
        return self._format_slicer_kwargs(self.task_type.slicer_script_args)

    @computed_field(return_type=dict)
    @property
    def slicer_validator_args_formated(self) -> Optional[Dict]:
        """Get formatted Slicer validator arguments."""
        if not hasattr(self, 'task_type') or self.task_type is None or self.task_type.slicer_result_validator_args is None:
            return None
        return self._format_slicer_kwargs(self.task_type.slicer_result_validator_args)

    @computed_field(return_type=str)
    @property
    def working_folder(self) -> Optional[str]:
        """Get the working folder path for this task."""
        if not hasattr(self, 'task_type') or self.task_type is None:
            return None
        
        match self.task_type.level:
            case DicomQueryLevel.series:
                return self._format_path(
                    f"{settings.storage_path}/{self.patient_id}/{self.study_anon_uid}/{self.series_anon_uid}"
                )
            case DicomQueryLevel.study:
                return self._format_path(
                    f"{settings.storage_path}/{self.patient_id}/{self.study_anon_uid}"
                )
            case _:
                raise NotImplementedError(
                    "Working folder attribute only available for Study and Series level task types."
                )

    @computed_field(return_type=dict)
    @property
    def slicer_all_args_formated(self) -> Optional[Dict]:
        """Get all formatted Slicer arguments."""
        if self.working_folder is None:
            return None
        
        all_args = self._format_slicer_kwargs({"working_folder": self.working_folder})
        all_args.update(self.slicer_args_formated or {})
        all_args.update(self.slicer_validator_args_formated or {})
        return all_args


class Task(TaskBase, table=True):
    """Model representing a task in the system."""

    id: Optional[int] = Field(default=None, primary_key=True)

    patient_id: str = Field(foreign_key="patient.id")
    patient: Patient = Relationship(back_populates="tasks")

    study_uid: str = Field(foreign_key="study.study_uid")
    study: Study = Relationship(back_populates="tasks")

    series_uid: Optional[str] = Field(default=None, foreign_key="series.series_uid")
    series: Optional[Series] = Relationship(back_populates="tasks")

    task_type_id: int = Field(foreign_key="tasktype.id")
    task_type: TaskType = Relationship(back_populates="tasks")

    user_id: Optional[str] = Field(default=None, foreign_key="user.id")
    user: Optional[User] = Relationship(back_populates="tasks")

    result: Optional[Dict] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(UTC))
    changed_at: Optional[datetime] = Field(sa_column_kwargs={"onupdate": func.now(), "server_default": func.now()})

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


# Add event listener to update timestamps based on status changes
@event.listens_for(Task.status, 'set')
def set_task_timestamps(target: Task, value: Any, oldvalue: Any, initiator: Any) -> None:
    """Update task timestamps when status changes."""
    if value == oldvalue:
        return
    match value:
        case TaskStatus.inwork:
            target.started_at = datetime.now(UTC)
        case TaskStatus.finished:
            target.finished_at = datetime.now(UTC)
        case _:
            return


class TaskCreate(TaskBase):
    """Pydantic model for creating a new task."""
    pass


class TaskRead(TaskBase):
    """Pydantic model for reading task data with related entities."""
    
    id: int
    result: Optional[Dict] = None
    patient: "PatientRead"
    study: "StudyRead"
    series: Optional[SeriesBase] = None
    task_type: TaskTypeBase


class TaskFind(SQLModel):
    """Pydantic model for searching tasks."""
    
    status: Optional[TaskStatus] = None
    name: str
    result: Optional[dict] = None
    user_id: Optional[str] = None
    is_absent: Optional[bool] = None


# Import required forward references to resolve circular dependencies
from .patient import PatientRead
from .study import StudyRead, SeriesBase