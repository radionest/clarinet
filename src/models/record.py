"""
Record-related models for the Clarinet framework.

This module provides models for records and record data.
RecordType models live in ``record_type.py`` and are re-exported here
for backward compatibility.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import computed_field, model_validator
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, event, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from src.types import RecordData, SlicerArgs

from ..exceptions import ConfigurationError, ValidationError
from ..settings import settings
from .base import BaseModel, RecordStatus
from .file_schema import RecordFileLink, RecordFileLinkRead
from .patient import Patient, PatientBase
from .record_type import (
    RecordType,
    RecordTypeBase,
    RecordTypeCreate,
    RecordTypeFind,
    RecordTypeOptional,
    RecordTypeRead,
    SlicerSettings,
)
from .study import Series, SeriesBase, SeriesFind, Study, StudyBase
from .user import User

# Re-export RecordType symbols for backward compatibility
__all__ = [
    "Record",
    "RecordBase",
    "RecordCreate",
    "RecordFind",
    "RecordFindResult",
    "RecordFindResultComparisonOperator",
    "RecordRead",
    "RecordType",
    "RecordTypeBase",
    "RecordTypeCreate",
    "RecordTypeFind",
    "RecordTypeOptional",
    "RecordTypeRead",
    "SlicerSettings",
]


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

    # Study relationship field is only defined in Record subclass, not in base

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.id == other.id


class Record(RecordBase, table=True):
    """Model representing a record in the system."""

    id: int | None = Field(default=None, primary_key=True)

    patient_id: str = Field(foreign_key="patient.id", ondelete="CASCADE")
    patient: Patient = Relationship(back_populates="records")

    study_uid: str | None = Field(default=None, foreign_key="study.study_uid", ondelete="CASCADE")
    study: Study = Relationship(back_populates="records")

    series_uid: str | None = Field(
        default=None, foreign_key="series.series_uid", ondelete="CASCADE"
    )
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

    # M2M relationship to FileDefinition via link table
    file_links: list[RecordFileLink] = Relationship(
        back_populates="record",
        cascade_delete=True,
    )

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
    files: dict[str, str] | None = Field(default=None, schema_extra={"deprecated": True})
    file_checksums: dict[str, str] | None = Field(default=None, schema_extra={"deprecated": True})
    file_links: list[RecordFileLinkRead] | None = None
    created_at: datetime | None = None
    changed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    patient: PatientBase
    study: StudyBase | None = None
    series: SeriesBase | None = None
    record_type: RecordTypeRead

    @model_validator(mode="before")
    @classmethod
    def populate_files_from_links(cls, data: Any) -> Any:
        """Populate files, file_checksums, and file_links from M2M when validating from ORM."""
        if isinstance(data, Record):
            result: dict[str, Any] = {}
            for field_name in cls.model_fields:
                if field_name in ("files", "file_checksums", "file_links"):
                    continue
                result[field_name] = getattr(data, field_name, None)
            try:
                links = data.file_links
                result["files"] = {
                    link.file_definition.name: link.filename for link in (links or [])
                }
                result["file_checksums"] = {
                    link.file_definition.name: link.checksum
                    for link in (links or [])
                    if link.checksum
                }
                result["file_links"] = [
                    RecordFileLinkRead(
                        name=link.file_definition.name,
                        filename=link.filename,
                        checksum=link.checksum,
                    )
                    for link in (links or [])
                ]
            except Exception:
                result["files"] = None
                result["file_checksums"] = None
                result["file_links"] = None
            return result
        return data

    @computed_field
    def radiant(self) -> str | None:
        """Generate a radiant URL for this record."""
        if self.study is None:
            return None
        if self.study.anon_uid:
            return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={self.study.anon_uid}"
        return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={self.study.study_uid}"

    def _format_path_strict(self, unformatted_path: str) -> str:
        """Format a path template with values from this record.

        Raises on failure — use for system templates where all placeholders
        are guaranteed to exist (e.g. working_folder).
        """
        return unformatted_path.format(
            patient_id=self.patient.anon_id
            if self.patient.anon_id is not None
            else self.patient_id,
            patient_anon_name=self.patient.anon_name,
            study_uid=self.study_uid,
            study_anon_uid=(self.study.anon_uid if self.study else self.study_anon_uid)
            or self.study_uid,
            series_uid=self.series_uid,
            series_anon_uid=(self.series.anon_uid if self.series else self.series_anon_uid)
            or self.series_uid,
            user_id=self.user_id,
            clarinet_storage_path=self.clarinet_storage_path or settings.storage_path,
        )

    def _format_path(self, unformatted_path: str) -> str | None:
        """Format a path template, returning None on failure.

        Safe wrapper for user-defined templates (e.g. slicer kwargs)
        where unknown placeholders are expected.
        """
        try:
            return self._format_path_strict(unformatted_path)
        except (AttributeError, KeyError):
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
        if self.record_type.slicer_script_args is None:
            return None
        return self._format_slicer_kwargs(self.record_type.slicer_script_args)

    @computed_field
    def slicer_validator_args_formatted(self) -> SlicerArgs | None:
        """Get formatted Slicer validator arguments."""
        if self.record_type.slicer_result_validator_args is None:
            return None
        return self._format_slicer_kwargs(self.record_type.slicer_result_validator_args)

    def _get_working_folder(self) -> str:
        """Get the working folder path for this record."""
        match self.record_type.level:
            case "SERIES":
                return self._format_path_strict(
                    f"{settings.storage_path}/{{patient_id}}/{{study_anon_uid}}/{{series_anon_uid}}"
                )
            case "STUDY":
                return self._format_path_strict(
                    f"{settings.storage_path}/{{patient_id}}/{{study_anon_uid}}"
                )
            case "PATIENT":
                return self._format_path_strict(f"{settings.storage_path}/{{patient_id}}")
            case _:
                raise ConfigurationError(
                    f"Unknown record type level '{self.record_type.level}' — "
                    "expected SERIES, STUDY, or PATIENT."
                )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def working_folder(self) -> str:
        """Get the working folder path for this record."""
        return self._get_working_folder()

    @computed_field
    def slicer_all_args_formatted(self) -> SlicerArgs:
        """Get all formatted Slicer arguments."""
        all_args: SlicerArgs = {"working_folder": self._get_working_folder()}

        if self.record_type.slicer_script_args is not None:
            all_args.update(self._format_slicer_kwargs(self.record_type.slicer_script_args))

        if self.record_type.slicer_result_validator_args is not None:
            all_args.update(
                self._format_slicer_kwargs(self.record_type.slicer_result_validator_args)
            )

        return all_args


class RecordFind(SQLModel):
    """Criteria for filtering series by their records."""

    record_type_name: str
    status: RecordStatus | None = None
    user_id: UUID | None = None
    is_absent: bool = False


SeriesFind.model_rebuild()
