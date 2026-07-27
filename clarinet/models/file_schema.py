"""
File schema models for the Clarinet framework.

This module provides models for defining file requirements in RecordTypes,
including input and output file definitions with pattern-based validation.

FileDefinition is a DB table with globally unique names.
RecordTypeFileLink is a M2M link table binding FileDefinition to RecordType
with per-binding properties (role, required).
FileDefinitionRead is a flat DTO merging identity + binding for API responses.
"""

import re
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import StringConstraints, field_validator, model_validator
from sqlalchemy.sql import expression as sql_expression
from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint

from clarinet.models.base import DicomQueryLevel

if TYPE_CHECKING:
    from clarinet.models.record import Record
    from clarinet.models.record_type import RecordType


class FileRole(str, Enum):
    """Role of a file in the processing pipeline."""

    INPUT = "input"
    OUTPUT = "output"
    INTERMEDIATE = "intermediate"


# DB column stays a plain string (additive downstream migrations); config and
# API payloads are constrained to these values.
type GridMismatchAction = Literal["conform", "delete", "reject"]
"""What to do with an OUTPUT file whose grid does not match its reference.

Consulted only for OUTPUT files at submit time — an INPUT mismatch never
repairs or deletes a file the record does not own; it blocks the record, or
raises a 422 if a submission's own re-check catches it first.
``conform`` repairs an exactly-repairable (``REARRANGED``) pair and still
rejects a ``FOREIGN`` one; ``delete`` removes the offending file for either
verdict; ``reject`` leaves it untouched. An unset action on a file that
declares ``grid_conform_to`` means ``reject`` — declaring a reference must
never fail open.
"""


class FileDefinition(SQLModel, table=True):
    """Persistent file definition stored in DB.

    Attributes:
        id: Auto-increment primary key.
        name: Globally unique identifier (valid Python identifier).
        pattern: Pattern with placeholders {field} for file name matching/generation.
            Supports placeholders: {id}, {user_id}, {patient_id}, {study_uid},
            {series_uid}, {data.FIELD} (temporarily rejected — see
            https://github.com/radionest/clarinet/issues/552), {record_type.FIELD}
        description: Optional description of the file purpose.
        multiple: Whether this is a collection (glob) vs singular file.
        grid_conform_to: Name of another FileDefinition whose on-disk voxel
            grid this file must match. ``None`` disables the check.
        on_grid_mismatch: What to do with a mismatched OUTPUT file —
            ``conform`` / ``delete`` / ``reject``. ``None`` means ``reject``.
    """

    __tablename__ = "filedefinition"
    __table_args__ = (UniqueConstraint("name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(
        index=True,
        min_length=1,
        max_length=100,
        schema_extra={"pattern": r"^[a-zA-Z_][a-zA-Z0-9_]*$"},
    )
    pattern: str = Field(max_length=500)
    description: str | None = None
    multiple: bool = Field(default=False)
    level: DicomQueryLevel | None = None
    grid_conform_to: str | None = Field(default=None, max_length=100)
    on_grid_mismatch: str | None = Field(default=None, max_length=20)

    record_type_links: list["RecordTypeFileLink"] = Relationship(
        back_populates="file_definition",
    )
    record_file_links: list["RecordFileLink"] = Relationship(
        back_populates="file_definition",
    )

    @field_validator("name")
    @classmethod
    def validate_name_is_identifier(cls, v: str) -> str:
        """Validate that name is a valid Python identifier."""
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError(f"File definition name must be a valid Python identifier, got: {v!r}")
        return v


class RecordTypeFileLink(SQLModel, table=True):
    """M2M link between RecordType and FileDefinition.

    Carries per-binding properties: role and required.

    Attributes:
        record_type_name: FK to RecordType.name.
        file_definition_id: FK to FileDefinition.id.
        role: File role in the processing pipeline (input/output/intermediate).
        required: Whether this file is required.
        allow_path_collision: Whether this binding may share its resolved path
            with another file of the record (opt-out of the default collision
            guard).
    """

    __tablename__ = "recordtype_file_link"

    record_type_name: str = Field(
        foreign_key="recordtype.name",
        primary_key=True,
        ondelete="CASCADE",
    )
    file_definition_id: int = Field(
        foreign_key="filedefinition.id",
        primary_key=True,
        ondelete="CASCADE",
    )
    role: FileRole = Field(default=FileRole.OUTPUT)
    required: bool = Field(default=True)
    allow_path_collision: bool = Field(
        default=False,
        sa_column_kwargs={"server_default": sql_expression.false()},
    )

    record_type: "RecordType" = Relationship(back_populates="file_links")
    file_definition: FileDefinition = Relationship(back_populates="record_type_links")


class RecordFileLink(SQLModel, table=True):
    """M2M link between Record and FileDefinition.

    Stores the actual matched filename and optional SHA256 checksum.

    Attributes:
        record_id: FK to Record.id.
        file_definition_id: FK to FileDefinition.id.
        filename: Actual matched filename.
        checksum: Optional SHA256 checksum of the file.
    """

    __tablename__ = "record_file_link"

    record_id: int = Field(foreign_key="record.id", primary_key=True, ondelete="CASCADE")
    file_definition_id: int = Field(
        foreign_key="filedefinition.id", primary_key=True, ondelete="CASCADE"
    )
    filename: str
    checksum: str | None = None

    record: "Record" = Relationship(back_populates="file_links")
    file_definition: FileDefinition = Relationship(back_populates="record_file_links")


class FileDefinitionRead(SQLModel):
    """Flat file definition merging identity + binding for API responses.

    Compatible with the old FileDefinition shape so that API consumers
    see the same JSON structure they always did.

    Attributes:
        name: Unique identifier for this file definition.
        pattern: Pattern with placeholders for file name matching/generation.
        description: Optional description of the file purpose.
        required: Whether this file is required (from binding).
        multiple: Whether this is a collection (glob) vs singular file.
        role: File role in the processing pipeline (from binding).
        allow_path_collision: Whether this binding may share its resolved path
            with another file of the record (from binding).
    """

    name: Annotated[
        str,
        StringConstraints(min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"),
    ]
    pattern: str
    description: str | None = None
    required: bool = True
    multiple: bool = False
    role: FileRole = FileRole.OUTPUT
    level: DicomQueryLevel | None = None
    allow_path_collision: bool = False
    grid_conform_to: str | None = None
    on_grid_mismatch: GridMismatchAction | None = None

    @field_validator("name")
    @classmethod
    def validate_name_is_identifier(cls, v: str) -> str:
        """Validate that name is a valid Python identifier."""
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError(f"File definition name must be a valid Python identifier, got: {v!r}")
        return v

    @model_validator(mode="after")
    def validate_pattern_is_path_safe(self) -> "FileDefinitionRead":
        """Reject patterns that could resolve outside the working directory.

        Attached here and NOT to ``FileDefinition``: that model is
        ``table=True``, so SQLModel skips Pydantic validation on it and a
        validator there would never fire. Every real entry point —
        ``config/primitives.py`` (``FileDef``, via ``fileref_to_file_definition``),
        ``utils/file_registry_resolver.py`` (``FileRegistryEntry``, via
        ``resolve_file_references``) and the API POST/PATCH paths — constructs
        ``FileDefinitionRead``.

        A *model* validator rather than a ``@field_validator("pattern")``
        because the rules differ for a collection, and only the whole model
        knows ``multiple``. A field validator saw the pattern alone and so
        rejected ``{parent_id}.nrrd`` even with ``multiple=True``, where it
        globs to ``*.nrrd`` rather than being rendered.
        """
        from clarinet.files import validate_file_pattern

        validate_file_pattern(self.pattern, is_collection=bool(self.multiple))
        return self

    @field_validator("grid_conform_to", mode="before")
    @classmethod
    def _reference_to_name(cls, v: Any) -> Any:
        """Accept a file definition object in place of its name.

        Runs eagerly because every construction site of this DTO builds it from
        an already-named source. ``FileDef`` in the config layer cannot do this
        (its names are assigned after module import) — see Task 2.
        """
        name = getattr(v, "name", None)
        return name if isinstance(name, str) else v


class RecordFileLinkRead(SQLModel):
    """DTO for Record -> FileDefinition link in API responses."""

    name: str
    filename: str
    checksum: str | None = None
