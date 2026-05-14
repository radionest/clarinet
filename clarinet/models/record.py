"""
Record-related models for the Clarinet framework.

This module provides models for records and record data.
RecordType models live in ``record_type.py`` and are re-exported here
for backward compatibility.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from pydantic import (
    ConfigDict,
    Discriminator,
    StringConstraints,
    Tag,
    computed_field,
    model_validator,
)
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, event, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Column, Field, Relationship, SQLModel

from clarinet.types import DbInt64, DbPositiveInt32, PortableJSON, RecordData, SlicerArgs
from clarinet.utils.anon_resolve import require_anon_or_raw
from clarinet.utils.logger import logger

from ..exceptions import AnonPathError, ConfigurationError, ValidationError
from ..settings import settings
from .base import BaseModel, DicomQueryLevel, DicomUID, RecordStatus
from .file_schema import RecordFileLink, RecordFileLinkRead
from .patient import Patient, PatientInfo
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
    "RecordOptional",
    "RecordRead",
    "RecordSearchQuery",
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


class _SqlTypeMixin:
    """Mixin providing ``sql_type`` computed field for RecordFindResult variants."""

    result_value: str | bool | DbInt64 | float

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


class EqFindResult(_SqlTypeMixin, SQLModel):
    """Equality search criterion — accepts any value type."""

    model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

    result_name: str = Field(min_length=1, max_length=255)
    result_value: str | bool | DbInt64 | float
    comparison_operator: Literal["eq"] = "eq"


class ContainsFindResult(_SqlTypeMixin, SQLModel):
    """Substring search criterion — string values only."""

    model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

    result_name: str = Field(min_length=1, max_length=255)
    result_value: str
    comparison_operator: Literal["contains"] = "contains"


class OrderFindResult(_SqlTypeMixin, SQLModel):
    """Ordering comparison criterion — no booleans."""

    model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

    result_name: str = Field(min_length=1, max_length=255)
    result_value: str | DbInt64 | float
    comparison_operator: Literal["lt", "gt"]


def _find_result_discriminator(v: dict[str, Any] | Any) -> str:
    """Extract discriminator tag, defaulting to 'eq' when absent."""
    if isinstance(v, dict):
        return v.get("comparison_operator") or "eq"
    return getattr(v, "comparison_operator", "eq")


RecordFindResult = Annotated[
    Annotated[EqFindResult, Tag("eq")]
    | Annotated[ContainsFindResult, Tag("contains")]
    | Annotated[OrderFindResult, Tag("lt")]
    | Annotated[OrderFindResult, Tag("gt")],
    Discriminator(_find_result_discriminator),
]


class RecordBase(BaseModel):
    """Base model for record data."""

    # Primary key (used in __hash__ and __eq__)
    id: int | None = None

    # Core fields
    context_info: str | None = Field(default=None, max_length=3000)
    status: RecordStatus = RecordStatus.pending

    # Foreign key fields
    study_uid: DicomUID | None = None
    series_uid: DicomUID | None = None
    record_type_name: str = Field(min_length=5, max_length=30)
    user_id: UUID | None = None
    patient_id: str = Field(min_length=1, max_length=64)

    # Parent record link
    parent_record_id: int | None = None

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

    __table_args__ = (
        Index("ix_record_changed_at_id_desc", "changed_at", "id", postgresql_using="btree"),
    )

    id: int | None = Field(default=None, primary_key=True)

    patient_id: str = Field(foreign_key="patient.id", ondelete="CASCADE")
    patient: Patient = Relationship(back_populates="records")

    study_uid: str | None = Field(default=None, foreign_key="study.study_uid", ondelete="CASCADE")
    study: Study = Relationship(back_populates="records")

    series_uid: str | None = Field(
        default=None, foreign_key="series.series_uid", ondelete="CASCADE"
    )
    series: Series | None = Relationship(back_populates="records")

    parent_record_id: int | None = Field(
        default=None,
        foreign_key="record.id",
        ondelete="SET NULL",
    )
    parent_record: Optional["Record"] = Relationship(
        back_populates="child_records",
        sa_relationship_kwargs={"remote_side": "Record.id"},
    )
    child_records: list["Record"] = Relationship(back_populates="parent_record")

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

    data: RecordData | None = Field(default_factory=dict, sa_column=Column(PortableJSON))
    viewer_study_uids: list[str] | None = Field(default=None, sa_column=Column(PortableJSON))
    viewer_series_uids: list[str] | None = Field(default=None, sa_column=Column(PortableJSON))

    # M2M relationship to FileDefinition via link table
    file_links: list[RecordFileLink] = Relationship(
        back_populates="record",
        cascade_delete=True,
    )

    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
    )
    changed_at: datetime | None = Field(
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
        sa_column_kwargs={"onupdate": func.now(), "server_default": func.now()},
    )

    started_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
    )

    @model_validator(mode="after")
    def validate_record_level(self) -> "Record":
        match (self.record_type.level, self.patient_id, self.study_uid, self.series_uid):
            case ("PATIENT", _, None, None) | ("STUDY", _, _, None) | ("SERIES", _, str(), str()):
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

    data: RecordData | None = None


class RecordOptional(SQLModel):
    """Pydantic model for partial record updates."""

    viewer_study_uids: list[str] | None = None
    viewer_series_uids: list[str] | None = None


class RecordContextInfoUpdate(SQLModel):
    """Body model for ``PATCH /records/{id}/context-info``."""

    context_info: str | None = Field(default=None, max_length=3000)


class RecordRead(RecordBase):
    """Pydantic model for reading record data with related entities."""

    id: int
    parent_record_id: int | None = None
    data: RecordData | None = None
    viewer_study_uids: list[str] | None = None
    viewer_series_uids: list[str] | None = None
    files: dict[str, str] | None = Field(default=None, schema_extra={"deprecated": True})
    file_checksums: dict[str, str] | None = Field(default=None, schema_extra={"deprecated": True})
    file_links: list[RecordFileLinkRead] | None = None
    created_at: datetime | None = None
    changed_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    patient: PatientInfo
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

    def _format_path_strict(
        self,
        unformatted_path: str,
        *,
        fallback_to_unanonymized: bool = False,
        **extra: Any,
    ) -> str:
        """Format a path template with values from this record.

        Raises on failure — use for system templates where all placeholders
        are guaranteed to exist (e.g. working_folder).

        Args:
            unformatted_path: Template string with ``{placeholder}`` tokens.
            fallback_to_unanonymized: If ``False`` (default — backend safe
                mode), missing ``anon_id``/``anon_uid`` raise
                ``AnonPathError`` instead of silently rendering against
                raw identifiers. UX callers (e.g. user-defined slicer args)
                pass ``True`` to keep the legacy fallback.
            **extra: Additional placeholder values (e.g. ``working_folder``).
        """
        patient_id = self._resolve_patient_id_for_path(fallback_to_unanonymized)
        study_anon_uid = self._resolve_study_anon_uid_for_path(fallback_to_unanonymized)
        series_anon_uid = self._resolve_series_anon_uid_for_path(fallback_to_unanonymized)
        return unformatted_path.format(
            patient_id=patient_id,
            patient_anon_name=self.patient.anon_name,
            study_uid=self.study_uid,
            study_anon_uid=study_anon_uid,
            series_uid=self.series_uid,
            series_anon_uid=series_anon_uid,
            user_id=self.user_id,
            clarinet_storage_path=self.clarinet_storage_path or settings.storage_path,
            **extra,
        )

    def _resolve_patient_id_for_path(self, fallback_to_unanonymized: bool) -> str:
        return require_anon_or_raw(
            anon=self.patient.anon_id,
            raw=self.patient_id,
            level=DicomQueryLevel.PATIENT,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )

    def _resolve_study_anon_uid_for_path(self, fallback_to_unanonymized: bool) -> str | None:
        if self.study_uid is None:
            return None  # PATIENT-level record — no study segment
        anon = self.study.anon_uid if self.study else self.study_anon_uid
        return require_anon_or_raw(
            anon=anon,
            raw=self.study_uid,
            level=DicomQueryLevel.STUDY,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )

    def _resolve_series_anon_uid_for_path(self, fallback_to_unanonymized: bool) -> str | None:
        if self.series_uid is None:
            return None  # PATIENT/STUDY-level record — no series segment
        anon = self.series.anon_uid if self.series else self.series_anon_uid
        return require_anon_or_raw(
            anon=anon,
            raw=self.series_uid,
            level=DicomQueryLevel.SERIES,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )

    def _format_path(self, unformatted_path: str, **extra: Any) -> str | None:
        """Format a path template, returning None on failure.

        Safe wrapper for user-defined templates (e.g. slicer kwargs)
        where unknown placeholders are expected. Uses ``fallback_to_unanonymized=True``
        because user templates target the UX layer (Slicer scripts shown
        to the doctor).

        Args:
            unformatted_path: Template string with ``{placeholder}`` tokens.
            **extra: Additional placeholder values (e.g. ``working_folder``).
        """
        try:
            return self._format_path_strict(
                unformatted_path, fallback_to_unanonymized=True, **extra
            )
        except (AttributeError, KeyError, AnonPathError):
            return None

    def _format_slicer_kwargs(
        self, slicer_kwargs: SlicerArgs, extra_vars: dict[str, Any] | None = None
    ) -> SlicerArgs:
        """Format Slicer script arguments with values from this record.

        Args:
            slicer_kwargs: Dict of arg_name -> template string.
            extra_vars: Additional placeholder values (e.g. ``working_folder``).
        """
        if slicer_kwargs is None:
            return {}
        extra = extra_vars or {}
        result: SlicerArgs = {}
        for k, v in slicer_kwargs.items():
            formatted = self._format_path(v, **extra)
            if formatted is not None:
                result[k] = formatted
            else:
                logger.warning(f"Slicer arg '{k}': could not resolve template '{v}'")
        return result

    @computed_field
    def slicer_args_formatted(self) -> SlicerArgs | None:
        """Get formatted Slicer script arguments."""
        if self.record_type.slicer_script_args is None:
            return None
        extra = {"working_folder": self._get_working_folder(fallback_to_unanonymized=True)}
        return self._format_slicer_kwargs(self.record_type.slicer_script_args, extra)

    @computed_field
    def slicer_validator_args_formatted(self) -> SlicerArgs | None:
        """Get formatted Slicer validator arguments."""
        if self.record_type.slicer_result_validator_args is None:
            return None
        extra = {"working_folder": self._get_working_folder(fallback_to_unanonymized=True)}
        return self._format_slicer_kwargs(self.record_type.slicer_result_validator_args, extra)

    def _get_working_folder(self, *, fallback_to_unanonymized: bool = False) -> str:
        """Get the working folder path for this record.

        Rendered from ``settings.disk_path_template`` at the appropriate
        DICOM level. The template segments are applied as follows::

            PATIENT -> storage / <patient_segment>
            STUDY   -> storage / <patient_segment> / <study_segment>
            SERIES  -> storage / <patient_segment> / <study_segment> / <series_segment>

        Backend callers (file validation, anonymization writer) keep the
        default ``fallback_to_unanonymized=False`` and surface
        ``AnonPathError`` when the record has not been anonymized yet. The
        ``working_folder`` computed field opts into the UX fallback so API
        responses never 500 just because anonymization is still pending.
        """
        from pathlib import Path

        from clarinet.services.common.storage_paths import build_context, render_working_folder

        try:
            level = DicomQueryLevel(self.record_type.level)
        except ValueError as exc:
            raise ConfigurationError(
                f"Unknown record type level '{self.record_type.level}' — "
                "expected SERIES, STUDY, or PATIENT."
            ) from exc
        ctx = build_context(
            patient=self.patient,
            study=self.study,
            series=self.series,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        # Per-record override only lives on Record (Series has no
        # clarinet_storage_path field). Series.working_folder always uses
        # settings.storage_path — that's an intentional asymmetry, not a bug.
        storage = Path(self.clarinet_storage_path or settings.storage_path)
        return str(render_working_folder(settings.disk_path_template, level, ctx, storage))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def working_folder(self) -> str:
        """Get the working folder path for this record.

        Serialised into API responses, so falls back to raw UIDs for
        records that have not been anonymized yet — otherwise reads of an
        in-flight study would 500. Backend callers should call
        ``FileResolver.build_working_dirs(record)`` (or
        ``_get_working_folder()`` with the default safe mode) instead.
        """
        return self._get_working_folder(fallback_to_unanonymized=True)

    @computed_field
    def slicer_all_args_formatted(self) -> SlicerArgs:
        """Get all formatted Slicer arguments."""
        wf = self._get_working_folder(fallback_to_unanonymized=True)
        extra = {"working_folder": wf}
        all_args: SlicerArgs = {"working_folder": wf}

        if self.record_type.slicer_script_args is not None:
            all_args.update(self._format_slicer_kwargs(self.record_type.slicer_script_args, extra))

        if self.record_type.slicer_result_validator_args is not None:
            all_args.update(
                self._format_slicer_kwargs(self.record_type.slicer_result_validator_args, extra)
            )

        return all_args

    @computed_field  # type: ignore[prop-decorator]
    @property
    def context_info_html(self) -> str | None:
        """Render ``context_info`` (markdown) to sanitized HTML for the frontend."""
        from clarinet.utils.markdown import markdown_to_safe_html

        return markdown_to_safe_html(self.context_info)


class RecordFind(SQLModel):
    """Criteria for filtering series by their records."""

    record_type_name: str
    status: RecordStatus | None = None
    user_id: UUID | None = None
    is_absent: bool = False


class RecordSearchFilter(SQLModel):
    """Filter criteria for record search (shared by paginated and random endpoints)."""

    model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

    patient_id: str | None = Field(default=None, min_length=1)
    patient_anon_id: Annotated[str, StringConstraints(pattern=r"^.+_\d+$")] | None = None
    series_uid: str | None = Field(default=None, min_length=1)
    anon_series_uid: str | None = Field(default=None, min_length=1)
    study_uid: str | None = Field(default=None, min_length=1)
    anon_study_uid: str | None = Field(default=None, min_length=1)
    user_id: UUID | None = None
    record_type_name: str | None = Field(default=None, min_length=1)
    record_status: RecordStatus | None = None
    parent_record_id: DbPositiveInt32 | None = Field(default=None)
    wo_user: bool | None = None
    data_queries: list[RecordFindResult] = Field(default_factory=list)


class RecordSearchQuery(RecordSearchFilter):
    """Search query with cursor-based pagination."""

    cursor: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    sort: Literal["changed_at_desc", "id_asc", "id_desc"] = "changed_at_desc"


class RecordPage(SQLModel):
    """Paginated page of records with cursor for next page."""

    model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

    items: list[RecordRead]
    next_cursor: str | None = None
    limit: int
    sort: Literal["changed_at_desc", "id_asc", "id_desc"] = "changed_at_desc"


SeriesFind.model_rebuild()
