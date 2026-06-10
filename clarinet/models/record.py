"""
Record-related models for the Clarinet framework.

This module provides models for records and record data.
RecordType models live in ``record_type.py`` and are re-exported here
for backward compatibility.
"""

from datetime import UTC, datetime, timedelta
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

from clarinet.types import DbInt64, DbPositiveInt32, PortableJSON, RecordData
from clarinet.utils.pagination import SortOrder

from ..exceptions import ValidationError
from ..settings import settings
from .base import BaseModel, DicomUID, RecordStatus
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
    "is_record_editable",
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

    # Anon UIDs — sibling-relationship snapshot used by FileRepository
    # when the study/series relations are not eager-loaded.
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


def is_record_editable(
    status: RecordStatus,
    finished_at: datetime | None,
    record_type: RecordTypeBase,
) -> bool:
    """Whether a record's submitted data may still be changed by non-superusers.

    Non-finished records are always editable — nothing has been submitted yet
    (POST submission paths gate on status separately). For finished records
    the verdict follows ``RecordType.editable`` and ``RecordType.edit_window_days``
    counted from ``finished_at``.

    Takes plain values (not a record object) so both ORM ``Record`` and
    ``RecordRead`` callers can share it.
    """
    if status != RecordStatus.finished:
        return True
    if not record_type.editable:
        return False
    if record_type.edit_window_days is None or finished_at is None:
        # finished_at is None only on legacy/imported rows (the status event
        # listener always sets it) — fail open rather than lock them forever.
        return True
    if finished_at.tzinfo is None:
        # SQLite returns naive datetimes; stored values are UTC.
        finished_at = finished_at.replace(tzinfo=UTC)
    return datetime.now(UTC) <= finished_at + timedelta(days=record_type.edit_window_days)


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
    display_anon_id: str | None = None

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

    @model_validator(mode="after")
    def populate_display_anon_id(self) -> "RecordRead":
        """Anon ID for table display: per-study hash when the option is enabled.

        Mirrors the masking rule in ``clarinet/api/masking.py`` — the hash is
        only valid once the study has been anonymized (``study.anon_uid`` set);
        until then fall back to the per-patient ``anon_id``.

        Computed at validation time (not a ``computed_field``): masking rewrites
        ``study_uid`` to the anon UID before FastAPI serializes the response, and
        a hash of the anon UID would not match the PatientID written into PACS.
        Skipped when already set — FastAPI re-validates response models after
        masking has run.
        """
        if self.display_anon_id is not None:
            return self
        if (
            settings.anon_per_study_patient_id
            and self.study_uid is not None
            and self.study is not None
            and self.study.anon_uid is not None
        ):
            from ..services.dicom.anonymizer import compute_per_study_patient_id

            self.display_anon_id = compute_per_study_patient_id(
                settings.anon_uid_salt,
                self.study_uid,
                settings.anon_per_study_patient_id_hex_length,
                prefix=settings.anon_id_prefix,
            )
        else:
            self.display_anon_id = self.patient.anon_id
        return self

    @computed_field
    def radiant(self) -> str | None:
        """Generate a radiant URL for this record."""
        if self.study is None:
            return None
        if self.study.anon_uid:
            return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={self.study.anon_uid}"
        return f"radiant://?n=paet&v=PACS_PETROVA&n=pstv&v=0020000D&v={self.study.study_uid}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def context_info_html(self) -> str | None:
        """Render ``context_info`` (markdown) to sanitized HTML for the frontend."""
        from clarinet.utils.markdown import markdown_to_safe_html

        return markdown_to_safe_html(self.context_info)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_editable(self) -> bool:
        """Whether the submitted data may still be changed by non-superusers.

        Server-side verdict for the frontend (form/Re-submit gating) — see
        :func:`is_record_editable`. Superuser bypass is the client's concern.
        """
        return is_record_editable(self.status, self.finished_at, self.record_type)


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
    sort: SortOrder = "changed_at_desc"


class RecordPage(SQLModel):
    """Paginated page of records with cursor for next page."""

    model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

    items: list[RecordRead]
    next_cursor: str | None = None
    limit: int
    sort: SortOrder = "changed_at_desc"


class RecordFilterOptions(SQLModel):
    """Distinct values for filter dropdowns on /records and /admin.

    The ``users`` list is prefixed with ``"__unassigned__"`` when the
    scope contains any record with ``user_id IS NULL``.
    """

    model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

    patients: list[str]
    record_types: list[str]
    users: list[str]


SeriesFind.model_rebuild()
