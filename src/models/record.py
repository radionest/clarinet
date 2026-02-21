"""
Record-related models for the Clarinet framework.

This module provides models for records, record types, and record data.
Formerly known as Task/TaskDesign models.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import computed_field, model_validator
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, event, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from src.types import RecordData, RecordSchema, SlicerArgs

from ..exceptions import ValidationError
from ..settings import settings
from ..utils.logger import logger
from .base import BaseModel, DicomQueryLevel, RecordStatus
from .file_schema import FileDefinition
from .patient import Patient, PatientBase
from .study import Series, SeriesBase, SeriesFind, Study, StudyBase
from .user import User, UserRole


class RecordDataSchema(BaseModel):
    """Base class for record data schemas."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)


class SlicerSettings(SQLModel):
    """Settings for Slicer workspace and validation scripts."""

    workspace_setup_script: str | None = None
    workspace_setup_script_args: dict[str, str] | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: dict[str, str] | None = None


class RecordTypeBase(SQLModel):
    """Base model for record type data."""

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

    # File schema definitions
    input_files: list[FileDefinition] | None = None
    output_files: list[FileDefinition] | None = None


class RecordType(RecordTypeBase, table=True):
    """Model representing a type of record that can be created."""

    name: str = Field(min_length=5, max_length=30, primary_key=True)
    data_schema: RecordSchema | None = Field(default_factory=dict, sa_column=Column(JSON))

    slicer_script_args: SlicerArgs | None = Field(default_factory=dict, sa_column=Column(JSON))
    slicer_result_validator_args: SlicerArgs | None = Field(
        default_factory=dict, sa_column=Column(JSON)
    )

    # File schema JSON columns
    input_files: list[FileDefinition] | None = Field(default_factory=list, sa_column=Column(JSON))
    output_files: list[FileDefinition] | None = Field(default_factory=list, sa_column=Column(JSON))

    role_name: str | None = Field(foreign_key="userrole.name", default=None)
    constraint_role: UserRole | None = Relationship(back_populates="allowed_record_types")

    records: list[Record] = Relationship(back_populates="record_type")

    def __hash__(self) -> int:
        """Hash the RecordType by its name."""
        return hash(self.name)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.name == other.name


class RecordTypeCreate(RecordTypeBase):
    """Pydantic model for creating a new record type."""

    data_schema: RecordSchema | None = None
    input_files: list[FileDefinition] | None = None
    output_files: list[FileDefinition] | None = None


class RecordTypeOptional(SQLModel):
    """Pydantic model for updating a record type with optional fields."""

    id: int | None = None
    name: str | None = None
    description: str | None = None
    label: str | None = None
    slicer_script: str | None = None
    slicer_script_args: SlicerArgs | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: SlicerArgs | None = None
    data_schema: RecordSchema | None = None

    role_name: str | None = Field(default=None)
    max_users: int | None = Field(default=None)
    min_users: int | None = Field(default=None)
    level: DicomQueryLevel | None = None

    # File schema fields
    input_files: list[FileDefinition] | None = None
    output_files: list[FileDefinition] | None = None


class RecordTypeFind(SQLModel):
    """Pydantic model for searching record types."""

    name: str | None = Field(default=None)
    constraint_role: str | None = Field(default=None)
    constraint_user_num: int | None = Field(default=None)


class RecordFindResultComparisonOperator(str, Enum):
    """Enumeration of comparison operators for record data searches."""

    eq = "eq"
    lt = "lt"
    gt = "gt"
    contains = "contains"


class RecordFindResult(SQLModel):
    """Model for specifying search criteria for record data."""

    result_name: str
    result_value: str | bool | int | float
    comparison_operator: RecordFindResultComparisonOperator | None = Field(
        default=RecordFindResultComparisonOperator.eq
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


class RecordBase(BaseModel):
    """Base model for record data."""

    # Primary key (used in __hash__ and __eq__)
    id: int | None = None

    # Core fields
    context_info: str | None = Field(default=None, max_length=3000)
    status: RecordStatus = RecordStatus.pending

    # Foreign key fields
    study_uid: str | None
    series_uid: str | None = None
    record_type_name: str
    user_id: UUID | None = None
    patient_id: str

    # Anon UIDs (used in working_folder)
    study_anon_uid: str | None = None
    series_anon_uid: str | None = None

    # Storage path
    clarinet_storage_path: str | None = None

    # Matched files from file validation
    files: dict[str, str] | None = None

    # Study relationship field is only defined in Record subclass, not in base

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.id == other.id

    @computed_field
    def radiant(self) -> str | None:
        """Generate a radiant URL for this record."""
        # This computed field only works for Record instances that have a study relationship
        if not hasattr(self, "study") or not hasattr(self, "patient"):
            return None
        study = getattr(self, "study", None)
        if study is None:
            return None
        if study.anon_uid:
            return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={study.anon_uid}"
        return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={study.study_uid}"

    def _format_path(self, unformatted_path: str) -> str | None:
        """Format a path template with values from this record."""
        try:
            # Get study and patient if they exist (only available in Record, not RecordBase)
            study = getattr(self, "study", None)
            patient = study.patient if study and hasattr(study, "patient") else None
            series = getattr(self, "series", None)

            return unformatted_path.format(
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
        """Format Slicer script arguments with values from this record."""
        if slicer_kwargs is None:
            return {}
        result: SlicerArgs = {}
        for k, v in slicer_kwargs.items():
            formatted = self._format_path(v)
            if formatted is not None:
                result[k] = formatted
        return result

    @computed_field
    def slicer_args_formatted(self) -> SlicerArgs | None:
        """Get formatted Slicer script arguments."""
        if (
            not hasattr(self, "record_type")
            or self.record_type is None
            or self.record_type.slicer_script_args is None
        ):
            return None

        result = self._format_slicer_kwargs(self.record_type.slicer_script_args)
        return result

    @computed_field
    def slicer_validator_args_formatted(self) -> SlicerArgs | None:
        """Get formatted Slicer validator arguments."""
        if (
            not hasattr(self, "record_type")
            or self.record_type is None
            or self.record_type.slicer_result_validator_args is None
        ):
            return None
        result = self._format_slicer_kwargs(self.record_type.slicer_result_validator_args)
        return result

    def _get_working_folder(self) -> str | None:
        """Get the working folder path for this record."""
        if not hasattr(self, "record_type") or self.record_type is None:
            return None

        match self.record_type.level:
            case "SERIES":
                return self._format_path(
                    f"{settings.storage_path}/{self.patient_id}/{self.study_anon_uid}/{self.series_anon_uid}"
                )
            case "STUDY":
                return self._format_path(
                    f"{settings.storage_path}/{self.patient_id}/{self.study_anon_uid}"
                )
            case "PATIENT":
                return self._format_path(f"{settings.storage_path}/{self.patient_id}")
            case _:
                raise NotImplementedError(
                    "Working folder attribute only available for Study and Series level record types."
                )

    @property
    @computed_field
    def working_folder(self) -> str | None:
        """Get the working folder path for this record."""
        return self._get_working_folder()

    @computed_field
    def slicer_all_args_formatted(self) -> SlicerArgs | None:
        """Get all formatted Slicer arguments."""
        # Get working folder
        working_folder_path = self._get_working_folder()
        if working_folder_path is None:
            return None

        all_args: SlicerArgs = {"working_folder": working_folder_path}

        # Format slicer args if available
        if (
            hasattr(self, "record_type")
            and self.record_type is not None
            and self.record_type.slicer_script_args is not None
        ):
            formatted_args = self._format_slicer_kwargs(self.record_type.slicer_script_args)
            all_args.update(formatted_args)

        # Format validator args if available
        if (
            hasattr(self, "record_type")
            and self.record_type is not None
            and self.record_type.slicer_result_validator_args is not None
        ):
            formatted_validator = self._format_slicer_kwargs(
                self.record_type.slicer_result_validator_args
            )
            all_args.update(formatted_validator)

        return all_args


class Record(RecordBase, table=True):
    """Model representing a record in the system."""

    id: int | None = Field(default=None, primary_key=True)

    patient_id: str = Field(foreign_key="patient.id")
    patient: Patient = Relationship(back_populates="records")

    study_uid: str | None = Field(default=None, foreign_key="study.study_uid")
    study: Study = Relationship(back_populates="records")

    series_uid: str | None = Field(default=None, foreign_key="series.series_uid")
    series: Series | None = Relationship(back_populates="records")

    record_type_name: str = Field(foreign_key="recordtype.name")
    record_type: RecordType = Relationship(back_populates="records")

    user_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PG_UUID(as_uuid=True),
            ForeignKey("user.id"),
            nullable=True,
        ),
    )
    user: User | None = Relationship(back_populates="records")

    data: RecordData | None = Field(default_factory=dict, sa_column=Column(JSON))

    # Matched files from file validation (key: file definition name, value: filename)
    files: dict[str, str] | None = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))
    changed_at: datetime | None = Field(
        sa_column_kwargs={"onupdate": func.now(), "server_default": func.now()}
    )

    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_record_level(self) -> Record:
        match (self.record_type.level, self.patient_id, self.study_uid, self.series_uid):
            case ("PATIENT", _, None, None) | ("STUDY", _, _, None) | ("SERIES", _, _, _):
                return self
            case ("STUDY" | "SERIES", _, None, _):
                raise ValidationError("Records of level STUDY or SERIES should have Study UID.")
            case ("SERIES", _, _, None):
                raise ValidationError("Records of level SERIES should have Series UID.")
            case _:
                raise NotImplementedError(
                    "Something unexpected happened during validation of record."
                )


# Add event listener to update timestamps based on status changes
@event.listens_for(Record.status, "set")
def set_record_timestamps(target: Record, value: Any, oldvalue: Any, _initiator: Any) -> None:
    """Update record timestamps when status changes."""
    if value == oldvalue:
        return
    match value:
        case RecordStatus.inwork:
            target.started_at = datetime.now(UTC)
        case RecordStatus.finished:
            target.finished_at = datetime.now(UTC)
        case _:
            return


class RecordCreate(RecordBase):
    """Pydantic model for creating a new record."""

    pass


class RecordRead(RecordBase):
    """Pydantic model for reading record data with related entities."""

    id: int
    data: RecordData | None = None
    patient: PatientBase
    study: StudyBase
    series: SeriesBase | None = None
    record_type: RecordTypeBase


class RecordFind(SQLModel):
    """Pydantic model for searching records."""

    status: RecordStatus | None = None
    name: str
    data: RecordData | None = None
    user_id: UUID | None = None
    is_absent: bool | None = None


SeriesFind.model_rebuild()
