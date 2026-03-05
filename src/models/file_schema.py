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
from typing import TYPE_CHECKING

from pydantic import field_validator
from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint

from src.models.base import DicomQueryLevel

if TYPE_CHECKING:
    from src.models.record import Record
    from src.models.record_type import RecordType


class FileRole(str, Enum):
    """Role of a file in the processing pipeline."""

    INPUT = "input"
    OUTPUT = "output"
    INTERMEDIATE = "intermediate"


class FileDefinition(SQLModel, table=True):
    """Persistent file definition stored in DB.

    Attributes:
        id: Auto-increment primary key.
        name: Globally unique identifier (valid Python identifier).
        pattern: Pattern with placeholders {field} for file name matching/generation.
            Supports placeholders: {id}, {user_id}, {patient_id}, {study_uid},
            {series_uid}, {data.FIELD}, {record_type.FIELD}
        description: Optional description of the file purpose.
        multiple: Whether this is a collection (glob) vs singular file.
    """

    __tablename__ = "filedefinition"
    __table_args__ = (UniqueConstraint("name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=100)
    pattern: str = Field(max_length=500)
    description: str | None = None
    multiple: bool = Field(default=False)
    level: DicomQueryLevel | None = None

    record_type_links: list[RecordTypeFileLink] = Relationship(
        back_populates="file_definition",
    )
    record_file_links: list[RecordFileLink] = Relationship(
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

    record_type: RecordType = Relationship(back_populates="file_links")
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

    record: Record = Relationship(back_populates="file_links")
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
    """

    name: str
    pattern: str
    description: str | None = None
    required: bool = True
    multiple: bool = False
    role: FileRole = FileRole.OUTPUT
    level: DicomQueryLevel | None = None

    @field_validator("name")
    @classmethod
    def validate_name_is_identifier(cls, v: str) -> str:
        """Validate that name is a valid Python identifier."""
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError(f"File definition name must be a valid Python identifier, got: {v!r}")
        return v


class RecordFileLinkRead(SQLModel):
    """DTO for Record -> FileDefinition link in API responses."""

    name: str
    filename: str
    checksum: str | None = None
