"""
RecordType-related models for the Clarinet framework.

This module provides models for record types — templates that define
what kind of records can be created, their file requirements, and
Slicer integration settings.
"""

import json as json_lib
from typing import TYPE_CHECKING, Any

from pydantic import field_validator, model_validator
from sqlalchemy import text as sa_text
from sqlalchemy.sql import expression as sql_expression
from sqlmodel import Column, Field, Relationship, SQLModel

from clarinet.types import PortableJSON, RecordSchema, SlicerArgs, SlicerHydratorNames
from clarinet.utils.validators import validate_json_safe, validate_slug

from .base import DicomQueryLevel
from .file_schema import FileDefinitionRead, RecordTypeFileLink
from .user import UserRole

if TYPE_CHECKING:
    from .record import Record


class SlicerSettings(SQLModel):
    """Settings for Slicer workspace and validation scripts."""

    workspace_setup_script: str | None = None
    workspace_setup_script_args: dict[str, str] | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: dict[str, str] | None = None


_VIEWER_MODES = {"single_series", "all_series"}


class RecordTypeBase(SQLModel):
    """Base model for record type data.

    ``file_registry`` is NOT defined here to avoid SQLModel creating a DB
    column on the ``RecordType`` table model. Instead:
    - ``RecordType``: populates ``file_registry`` from M2M ``file_links``
    - ``RecordTypeCreate`` / ``RecordTypeOptional``: defines it as a regular field
    """

    # min_length/max_length enforce total length; schema_extra.pattern is
    # OpenAPI-only metadata for schemathesis; field_validator is the actual
    # format check (stable across SQLModel/Pydantic versions).
    name: str = Field(
        min_length=1,
        max_length=30,
        schema_extra={"pattern": r"^[a-z][-a-z0-9]{0,29}$"},
    )

    @field_validator("name")
    @classmethod
    def validate_name_slug(cls, v: str) -> str:
        """Enforce lowercase slug format: ``[a-z][a-z0-9]*(-[a-z0-9]+)*``."""
        return validate_slug(v)

    description: str | None = Field(default=None, max_length=500)
    label: str | None = Field(default=None, max_length=100)
    slicer_script: str | None = None
    slicer_script_args: SlicerArgs | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: SlicerArgs | None = None

    role_name: str | None = Field(default=None)
    max_records: int | None = Field(default=None, ge=0, le=10000)
    min_records: int | None = Field(default=1, ge=0, le=10000)
    # ``server_default=sql_expression.false()`` lets alembic safely add this
    # column to populated tables. See ``mask_patient_data`` below for the
    # full rationale.
    unique_per_user: bool = Field(
        default=False,
        sa_column_kwargs={"server_default": sql_expression.false()},
    )
    level: DicomQueryLevel = Field(default=DicomQueryLevel.SERIES)

    data_schema: RecordSchema | None = None
    slicer_context_hydrators: SlicerHydratorNames | None = None

    # ``server_default`` is required so alembic autogenerate emits
    # ``ALTER TABLE recordtype ADD COLUMN ... NOT NULL DEFAULT true`` instead
    # of the unsafe ``... NOT NULL`` form. Without it PostgreSQL rejects the
    # migration on populated tables: ``column "mask_patient_data" of relation
    # "recordtype" contains null values``. SQLite was lenient and silently
    # allowed the bad form, which is how the bug originally slipped through
    # tests (PR #144, fixed in PR #149).
    #
    # Use ``sql_expression.true()`` (NOT ``text("1")`` or ``text("true")``):
    # it is the only dialect-aware Boolean literal in SQLAlchemy — renders as
    # ``true`` on PostgreSQL (required: PG has no implicit int→bool cast, so
    # ``DEFAULT 1`` fails in both CREATE TABLE and ALTER TABLE with "default
    # expression is of type integer") and as ``1`` on SQLite (which stores
    # BOOLEAN as INTEGER). A raw ``text("1")`` looks portable but bypasses
    # the dialect visitor and produces the broken ``DEFAULT 1`` on PG.
    mask_patient_data: bool = Field(
        default=True,
        sa_column_kwargs={"server_default": sql_expression.true()},
        description=(
            "Whether to mask patient/study/series identifiers for non-superusers "
            "when the patient has been anonymized. Set to False for record types "
            "filled by clinicians who need real patient IDs (surgery, pathology, MDK)."
        ),
    )

    viewer_mode: str = Field(
        default="single_series",
        max_length=20,
        sa_column_kwargs={"server_default": sa_text("'single_series'")},
        schema_extra={"enum": sorted(_VIEWER_MODES)},
        description=(
            "Controls viewer series loading: 'single_series' passes series_uid "
            "to viewer adapters (default), 'all_series' omits it so all study "
            "series are loaded."
        ),
    )

    @field_validator("viewer_mode")
    @classmethod
    def validate_viewer_mode(cls, v: str) -> str:
        if v not in _VIEWER_MODES:
            msg = f"viewer_mode must be one of {_VIEWER_MODES}, got '{v}'"
            raise ValueError(msg)
        return v

    @field_validator("data_schema", mode="after")
    @classmethod
    def validate_data_schema_safe(cls, v: RecordSchema | None) -> RecordSchema | None:
        if v is not None:
            validate_json_safe(v)
        return v


class RecordType(RecordTypeBase, table=True):
    """Model representing a type of record that can be created.

    ``file_registry`` is a ``@property`` (not a DB column) that builds
    ``list[FileDefinitionRead]`` from the M2M ``file_links`` relationship.
    Requires eager loading of ``file_links``; returns ``[]`` otherwise.
    """

    name: str = Field(primary_key=True)
    data_schema: RecordSchema | None = Field(default_factory=dict, sa_column=Column(PortableJSON))

    slicer_script_args: SlicerArgs | None = Field(
        default_factory=dict, sa_column=Column(PortableJSON)
    )
    slicer_result_validator_args: SlicerArgs | None = Field(
        default_factory=dict, sa_column=Column(PortableJSON)
    )
    slicer_context_hydrators: SlicerHydratorNames | None = Field(
        default=None, sa_column=Column(PortableJSON)
    )

    role_name: str | None = Field(foreign_key="userrole.name", default=None)
    constraint_role: UserRole | None = Relationship(back_populates="allowed_record_types")

    records: list["Record"] = Relationship(back_populates="record_type")

    # M2M relationship to FileDefinition via link table
    file_links: list[RecordTypeFileLink] = Relationship(
        back_populates="record_type",
        cascade_delete=True,
    )

    @property
    def file_registry(self) -> list[FileDefinitionRead]:
        """Build flat file definitions from M2M links.

        Converts ORM relationships (file_links → RecordTypeFileLink → FileDefinition)
        into flat DTOs (FileDefinitionRead) by merging identity fields (name, pattern,
        description, multiple) with per-binding fields (role, required).

        Used by ``RecordTypeRead.model_validator`` for API serialization.
        For DB operations that need ``FileDefinition`` ORM objects (e.g. creating
        RecordFileLink rows), access ``file_links`` directly instead.

        Raises ``RuntimeError`` if ``file_links`` is not eagerly loaded.
        """
        try:
            links = self.file_links
        except Exception as exc:
            raise RuntimeError(
                f"RecordType('{self.name}').file_links not eagerly loaded. "
                f"Use selectinload(RecordType.file_links)"
                f".selectinload(RecordTypeFileLink.file_definition)"
            ) from exc
        return [
            FileDefinitionRead(
                name=link.file_definition.name,
                pattern=link.file_definition.pattern,
                description=link.file_definition.description,
                multiple=link.file_definition.multiple,
                role=link.role,
                required=link.required,
                level=link.file_definition.level,
            )
            for link in (links or [])
        ]

    def __hash__(self) -> int:
        """Hash the RecordType by its name."""
        return hash(self.name)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.name == other.name


class RecordTypeRead(RecordTypeBase):
    """Pydantic model for reading a record type with file definitions.

    Adds ``file_registry`` (computed from M2M file_links) to the base fields.
    Used in API responses wherever the full file registry is needed.
    """

    file_registry: list[FileDefinitionRead] | None = None

    @model_validator(mode="before")
    @classmethod
    def populate_file_registry(cls, data: Any) -> Any:
        """Populate file_registry from file_links when validating from ORM."""
        if isinstance(data, RecordType):
            # ORM object — extract file_registry from file_links
            result: dict[str, Any] = {}
            for field_name in cls.model_fields:
                if field_name == "file_registry":
                    continue
                result[field_name] = getattr(data, field_name, None)
            try:
                result["file_registry"] = data.file_registry
            except Exception:
                result["file_registry"] = None
            return result
        return data


class RecordTypeCreate(RecordTypeBase):
    """Pydantic model for creating a new record type."""

    data_schema: RecordSchema | None = None
    file_registry: list[FileDefinitionRead] | None = None


class RecordTypeOptional(SQLModel):
    """Pydantic model for updating a record type with optional fields."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
        schema_extra={"pattern": r"^[a-z][-a-z0-9]{0,29}$"},
    )

    @field_validator("name")
    @classmethod
    def validate_name_slug(cls, v: str | None) -> str | None:
        """Enforce lowercase slug format when name is provided."""
        if v is not None:
            validate_slug(v)
        return v

    description: str | None = None
    label: str | None = None
    slicer_script: str | None = None
    slicer_script_args: SlicerArgs | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: SlicerArgs | None = None
    data_schema: RecordSchema | None = None
    slicer_context_hydrators: SlicerHydratorNames | None = None
    mask_patient_data: bool | None = Field(default=None)
    viewer_mode: str | None = Field(default=None)

    @field_validator("viewer_mode")
    @classmethod
    def validate_viewer_mode_optional(cls, v: str | None) -> str | None:
        if v is not None and v not in _VIEWER_MODES:
            msg = f"viewer_mode must be one of {_VIEWER_MODES}, got '{v}'"
            raise ValueError(msg)
        return v

    role_name: str | None = Field(default=None)
    max_records: int | None = Field(default=None)
    min_records: int | None = Field(default=None)
    unique_per_user: bool | None = Field(default=None)
    level: DicomQueryLevel | None = None

    # File schema fields
    file_registry: list[FileDefinitionRead] | None = None

    @field_validator(
        "data_schema",
        "slicer_script_args",
        "slicer_result_validator_args",
        "slicer_context_hydrators",
        mode="before",
    )
    @classmethod
    def parse_json_strings(cls, v: Any) -> Any:
        """Accept JSON strings for dict fields (from formosh textarea submission)."""
        if isinstance(v, str) and v:
            try:
                return json_lib.loads(v)
            except json_lib.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {e}") from e
        return v

    @field_validator("data_schema", mode="after")
    @classmethod
    def validate_data_schema_safe(cls, v: RecordSchema | None) -> RecordSchema | None:
        if v is not None:
            validate_json_safe(v)
        return v


class RecordTypeFind(SQLModel):
    """Pydantic model for searching record types."""

    name: str | None = Field(default=None)
    constraint_role: str | None = Field(default=None)
