"""
File schema models for the Clarinet framework.

This module provides models for defining file requirements in RecordTypes,
including input and output file definitions with pattern-based validation.
"""

import re
from enum import Enum

from pydantic import field_validator
from sqlmodel import SQLModel


class FileRole(str, Enum):
    """Role of a file in the processing pipeline."""

    INPUT = "input"
    OUTPUT = "output"
    INTERMEDIATE = "intermediate"


class FileDefinition(SQLModel):
    """Definition of a file for RecordType.

    Attributes:
        name: Unique identifier for this file definition (must be valid Python identifier)
        pattern: Pattern with placeholders {field} for file name matching/generation.
            Supports placeholders: {id}, {user_id}, {patient_id}, {study_uid},
            {series_uid}, {data.FIELD}, {record_type.FIELD}
        description: Optional description of the file purpose
        required: Whether this file is required (default True)
        multiple: Whether this is a collection (glob) vs singular file
        role: File role in the processing pipeline (input/output/intermediate)

    Examples:
        Static filename:
            FileDefinition(name="master", pattern="master_model.nrrd")

        Dynamic with record ID:
            FileDefinition(name="result", pattern="result_{id}.json")

        Dynamic with data field:
            FileDefinition(name="birads", pattern="birads_{data.BIRADS_R}.txt")

        Collection (multiple=True):
            FileDefinition(name="user_segs", pattern="lesions_{user_id}.seg.nrrd", multiple=True)
    """

    name: str
    pattern: str
    description: str | None = None
    required: bool = True
    multiple: bool = False
    role: FileRole = FileRole.OUTPUT

    @field_validator("name")
    @classmethod
    def validate_name_is_identifier(cls, v: str) -> str:
        """Validate that name is a valid Python identifier."""
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError(f"File definition name must be a valid Python identifier, got: {v!r}")
        return v
