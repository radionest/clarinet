"""
File schema models for the Clarinet framework.

This module provides models for defining file requirements in RecordTypes,
including input and output file definitions with pattern-based validation.
"""

from sqlmodel import SQLModel


class FileDefinition(SQLModel):
    """Definition of a file for RecordType.

    Attributes:
        name: Unique identifier for this file definition
        pattern: Pattern with placeholders {field} for file name matching/generation.
            Supports placeholders: {id}, {user_id}, {patient_id}, {study_uid},
            {series_uid}, {data.FIELD}, {record_type.FIELD}
        description: Optional description of the file purpose
        required: Whether this file is required (default True)

    Examples:
        Static filename:
            FileDefinition(name="master", pattern="master_model.nrrd")

        Dynamic with record ID:
            FileDefinition(name="result", pattern="result_{id}.json")

        Dynamic with data field:
            FileDefinition(name="birads", pattern="birads_{data.BIRADS_R}.txt")

        Combined placeholders:
            FileDefinition(name="seg", pattern="seg_{study_uid}_{id}.seg.nrrd")
    """

    name: str
    pattern: str
    description: str | None = None
    required: bool = True
