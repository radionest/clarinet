"""
RecordType-related models for the Clarinet framework.

This module provides models for record types — templates that define
what kind of records can be created, their file requirements, and
Slicer integration settings.
"""

import json as json_lib
from typing import TYPE_CHECKING, Any

from pydantic import field_validator, model_validator
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from clarinet.types import RecordSchema, SlicerArgs, SlicerHydratorNames
from clarinet.utils.validators import validate_slug

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


class RecordTypeBase(SQLModel):
    """Base model for record type data.

    ``file_registry`` is NOT defined here to avoid SQLModel creating a DB
    column on the ``RecordType`` table model. Instead:
    - ``RecordType``: populates ``file_registry`` from M2M ``file_links``
    - ``RecordTypeCreate`` / ``RecordTypeOptional``: defines it as a regular field
    """

    name: str = Field(
        min_length=5,
        max_length=30,
        schema_extra={"pattern": r"^[a-z][a-z0-9]{4,29}(-[a-z0-9]+)*$"},
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
    unique_per_user: bool = Field(default=False)
    level: DicomQueryLevel = Field(default=DicomQueryLevel.SERIES)

    data_schema: RecordSchema | None = None
    slicer_context_hydrators: SlicerHydratorNames | None = None


class RecordType(RecordTypeBase, table=True):
    """Model representing a type of record that can be created.

    ``file_registry`` is a ``@property`` (not a DB column) that builds
    ``list[FileDefinitionRead]`` from the M2M ``file_links`` relationship.
    Requires eager loading of ``file_links``; returns ``[]`` otherwise.
    """

    name: str = Field(min_length=5, max_length=30, primary_key=True)
    data_schema: RecordSchema | None = Field(default_factory=dict, sa_column=Column(JSON))

    slicer_script_args: SlicerArgs | None = Field(default_factory=dict, sa_column=Column(JSON))
    slicer_result_validator_args: SlicerArgs | None = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    slicer_context_hydrators: SlicerHydratorNames | None = Field(
        default=None, sa_column=Column(JSON)
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
        min_length=5,
        max_length=30,
        schema_extra={"pattern": r"^[a-z][a-z0-9]{4,29}(-[a-z0-9]+)*$"},
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


class RecordTypeFind(SQLModel):
    """Pydantic model for searching record types."""

    name: str | None = Field(default=None)
    constraint_role: str | None = Field(default=None)
