"""
Task-related models for the Clarinet framework.

This module provides models for tasks, task types, and task results.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import computed_field, model_validator
from sqlalchemy import Boolean, Float, Integer, String, event, func
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from src.types import ResultSchema, SlicerArgs, TaskResult

from ..exceptions import ValidationError
from ..settings import settings
from ..utils.logger import logger
from .base import BaseModel, DicomQueryLevel, TaskStatus
from .patient import Patient, PatientRead
from .study import Series, SeriesBase, Study, StudyRead
from .user import User, UserRole


class TaskResultSchema(BaseModel):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)


class TaskDesignBase(SQLModel):
    """Base model for task type data."""

    name: str
    description: str | None = None
    label: str | None = None
    slicer_script: str | None = None
    slicer_script_args: SlicerArgs | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: SlicerArgs | None = None

    role_name: str | None = Field(default=None)
    max_users: int | None = Field(default=None)
    min_users: int | None = Field(default=1)
    level: DicomQueryLevel = Field(default=DicomQueryLevel.SERIES)


class TaskDesign(TaskDesignBase, table=True):
    """Model representing a type of task that can be performed."""

    name: str = Field(min_length=5, max_length=30, primary_key=True)
    result_schema: ResultSchema | None = Field(default_factory=dict, sa_column=Column(JSON))

    slicer_script_args: SlicerArgs | None = Field(default_factory=dict, sa_column=Column(JSON))
    slicer_result_validator_args: SlicerArgs | None = Field(
        default_factory=dict, sa_column=Column(JSON)
    )

    role_name: str | None = Field(foreign_key="userrole.name", default=None)
    constraint_role: UserRole | None = Relationship(back_populates="allowed_task_designs")

    tasks: list["Task"] = Relationship(back_populates="task_design")

    def __hash__(self) -> int:
        """TaskType hash is taken by its"""
        return hash(self.name)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.name == other.name


class TaskDesignCreate(TaskDesignBase):
    """Pydantic model for creating a new task type."""

    result_schema: ResultSchema | None = None


class TaskDesignOptional(SQLModel):
    """Pydantic model for updating a task type with optional fields."""

    id: int | None = None
    name: str | None = None
    description: str | None = None
    label: str | None = None
    slicer_script: str | None = None
    slicer_script_args: SlicerArgs | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: SlicerArgs | None = None
    result_schema: ResultSchema | None = None

    role_name: str | None = Field(default=None)
    max_users: int | None = Field(default=None)
    min_users: int | None = Field(default=None)
    level: DicomQueryLevel | None = None


class TaskDesignFind(SQLModel):
    """Pydantic model for searching task types."""

    name: str | None = Field(default=None)
    constraint_role: str | None = Field(default=None)
    constraint_user_num: int | None = Field(default=None)


class TaskFindResultComparisonOperator(str, Enum):
    """Enumeration of comparison operators for task result searches."""

    eq = "eq"
    lt = "lt"
    gt = "gt"
    contains = "contains"


class TaskFindResult(SQLModel):
    """Model for specifying search criteria for task results."""

    result_name: str
    result_value: str | bool | int | float
    comparison_operator: TaskFindResultComparisonOperator | None = Field(
        default=TaskFindResultComparisonOperator.eq
    )

    @computed_field
    def sql_type(self) -> type[String] | type[Boolean] | type[Integer] | type[Float]:  # type: ignore[type-arg]
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

    # Primary key (used in __hash__ and __eq__)
    id: int | None = None

    # Core fields
    info: str | None = Field(default=None, max_length=3000)
    status: TaskStatus = TaskStatus.pending

    # Foreign key fields
    study_uid: str | None
    series_uid: str | None = None
    task_design_id: str
    user_id: str | None = None
    patient_id: str

    # Anon UIDs (used in working_folder)
    study_anon_uid: str | None = None
    series_anon_uid: str | None = None

    # Storage path
    clarinet_storage_path: str | None = None

    # Study relationship field is only defined in Task subclass, not in base

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            raise NotImplementedError
        return self.id == other.id

    @computed_field
    def radiant(self) -> str | None:
        """Generate a radiant URL for this task."""
        # This computed field only works for Task instances that have a study relationship
        if not hasattr(self, "study") or not hasattr(self, "patient"):
            return None
        study = getattr(self, "study", None)
        if study is None:
            return None
        if study.anon_uid:
            return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={study.anon_uid}"
        return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={study.study_uid}"

    def _format_path(self, unformated_path: str) -> str | None:
        """Format a path template with values from this task."""
        try:
            # Get study and patient if they exist (only available in Task, not TaskBase)
            study = getattr(self, "study", None)
            patient = study.patient if study and hasattr(study, "patient") else None
            series = getattr(self, "series", None)

            return unformated_path.format(
                patient_id=patient.anon_id if patient else self.patient_id,
                patient_anon_name=patient.anon_name if patient else None,
                study_uid=self.study_uid,
                study_anon_uid=study.anon_uid if study else self.study_anon_uid,
                series_uid=self.series_uid,
                series_anon_uid=series.anon_uid if series else self.series_anon_uid,
                user_id=self.user_id,
                clarinet_storage_path=self.clarinet_storage_path,
            )
        except (AttributeError, KeyError) as e:
            logger.error(f"Error formatting path: {e}")
            return None

    def _format_slicer_kwargs(self, slicer_kwargs: SlicerArgs) -> SlicerArgs:
        """Format Slicer script arguments with values from this task."""
        if slicer_kwargs is None:
            return {}
        result: SlicerArgs = {}
        for k, v in slicer_kwargs.items():
            formatted = self._format_path(v)
            if formatted is not None:
                result[k] = formatted
        return result

    @computed_field
    def slicer_args_formated(self) -> SlicerArgs | None:
        """Get formatted Slicer script arguments."""
        if (
            not hasattr(self, "task_design")
            or self.task_design is None
            or self.task_design.slicer_script_args is None
        ):
            return None

        result = self._format_slicer_kwargs(self.task_design.slicer_script_args)
        return result

    @computed_field
    def slicer_validator_args_formated(self) -> SlicerArgs | None:
        """Get formatted Slicer validator arguments."""
        if (
            not hasattr(self, "task_design")
            or self.task_design is None
            or self.task_design.slicer_result_validator_args is None
        ):
            return None
        result = self._format_slicer_kwargs(self.task_design.slicer_result_validator_args)
        return result

    @computed_field
    def working_folder(self) -> str | None:
        """Get the working folder path for this task."""
        if not hasattr(self, "task_design") or self.task_design is None:
            return None

        match self.task_design.level:
            case "SERIES":
                return self._format_path(
                    f"{settings.storage_path}/{self.patient_id}/{self.study_anon_uid}/{self.series_anon_uid}"
                )
            case "STUDY":
                return self._format_path(
                    f"{settings.storage_path}/{self.patient_id}/{self.study_anon_uid}"
                )
            case _:
                raise NotImplementedError(
                    "Working folder attribute only available for Study and Series level task types."
                )

    @computed_field
    def slicer_all_args_formated(self) -> SlicerArgs | None:
        """Get all formatted Slicer arguments."""
        # Get working folder
        working_folder_path = self._get_working_folder()
        if working_folder_path is None:
            return None

        all_args: SlicerArgs = {"working_folder": working_folder_path}

        # Format slicer args if available
        if (
            hasattr(self, "task_design")
            and self.task_design is not None
            and self.task_design.slicer_script_args is not None
        ):
            formatted_args = self._format_slicer_kwargs(self.task_design.slicer_script_args)
            all_args.update(formatted_args)

        # Format validator args if available
        if (
            hasattr(self, "task_design")
            and self.task_design is not None
            and self.task_design.slicer_result_validator_args is not None
        ):
            formatted_validator = self._format_slicer_kwargs(
                self.task_design.slicer_result_validator_args
            )
            all_args.update(formatted_validator)

        return all_args

    def _get_working_folder(self) -> str | None:
        """Get the working folder path for this task."""
        if not hasattr(self, "task_design") or self.task_design is None:
            return None

        match self.task_design.level:
            case "SERIES":
                return self._format_path(
                    f"{settings.storage_path}/{self.patient_id}/{self.study_anon_uid}/{self.series_anon_uid}"
                )
            case "STUDY":
                return self._format_path(
                    f"{settings.storage_path}/{self.patient_id}/{self.study_anon_uid}"
                )
            case _:
                raise NotImplementedError(
                    "Working folder attribute only available for Study and Series level task types."
                )


class Task(TaskBase, table=True):
    """Model representing a task in the system."""

    id: int | None = Field(default=None, primary_key=True)

    patient_id: str = Field(foreign_key="patient.id")
    patient: Patient = Relationship(back_populates="tasks")

    study_uid: str | None = Field(default=None, foreign_key="study.study_uid")
    study: Study = Relationship(back_populates="tasks")

    series_uid: str | None = Field(default=None, foreign_key="series.series_uid")
    series: Series | None = Relationship(back_populates="tasks")

    task_design_id: str = Field(foreign_key="taskdesign.name")
    task_design: TaskDesign = Relationship(back_populates="tasks")

    user_id: str | None = Field(default=None, foreign_key="user.id")
    user: User | None = Relationship(back_populates="tasks")

    result: TaskResult | None = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))
    changed_at: datetime | None = Field(
        sa_column_kwargs={"onupdate": func.now(), "server_default": func.now()}
    )

    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_task_level(self) -> "Task":
        match (self.task_design.level, self.patient_id, self.study_uid, self.series_uid):
            case ("PATIENT", _, None, None) | ("STUDY", _, _, None) | ("SERIES", _, _, _):
                return self
            case ("STUDY" | "SERIES", _, None, _):
                raise ValidationError("Tasks of level STUDY or SERIES should have Study UID.")
            case ("SERIES", _, _, None):
                raise ValidationError("Tasks of level SERIES should have Series UID.")
            case _:
                raise NotImplementedError(
                    "Something unexpected happened during validation of task."
                )


# Add event listener to update timestamps based on status changes
@event.listens_for(Task.status, "set")
def set_task_timestamps(target: Task, value: Any, oldvalue: Any, _initiator: Any) -> None:
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
    result: TaskResult | None = None
    patient: "PatientRead"
    study: "StudyRead"
    series: SeriesBase | None = None
    task_design: TaskDesignBase


class TaskFind(SQLModel):
    """Pydantic model for searching tasks."""

    status: TaskStatus | None = None
    name: str
    result: TaskResult | None = None
    user_id: str | None = None
    is_absent: bool | None = None
