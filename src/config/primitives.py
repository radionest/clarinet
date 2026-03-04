"""Config-level dataclasses for RecordType definitions.

These primitives are used in Python config files (``record_types.py``) to
define RecordTypes in a declarative, type-safe way.
"""

from dataclasses import dataclass, field
from typing import Any

from src.models.base import DicomQueryLevel
from src.models.file_schema import FileDefinition, FileRole


@dataclass
class File:
    """Shared file definition (equivalent to file_registry entry).

    Attributes:
        pattern: Pattern with placeholders for file name matching/generation.
        multiple: Whether this is a collection (glob) vs singular file.
        level: Informational DICOM level (PATIENT/STUDY/SERIES).
        description: Optional description of the file purpose.
        name: Derived from variable name in files_catalog module.
    """

    pattern: str
    multiple: bool = False
    level: str | None = None
    description: str | None = None
    name: str = ""


@dataclass(frozen=True)
class FileRef:
    """Binds a File to a RecordType with a role.

    Attributes:
        file: Reference to a File instance.
        role: File role in the processing pipeline.
        required: Whether this file is required.
    """

    file: File
    role: FileRole = FileRole.OUTPUT
    required: bool = True


@dataclass
class RecordTypeDef:
    """RecordType definition — maps to RecordTypeCreate fields.

    Attributes:
        name: Unique name for the record type (5-30 chars).
        level: DICOM query level.
        description: Human-readable description.
        label: Short display label.
        role_name: Required user role name.
        min_users: Minimum number of users.
        max_users: Maximum number of users.
        files: List of FileRef bindings.
        data_schema: JSON Schema dict or path to .json file.
        slicer_script: Inline script or path to .py file.
        slicer_script_args: Arguments for slicer script.
        slicer_result_validator: Inline validator or path to .py file.
        slicer_result_validator_args: Arguments for result validator.
    """

    name: str
    level: DicomQueryLevel = DicomQueryLevel.SERIES
    description: str | None = None
    label: str | None = None
    role_name: str | None = None
    min_users: int | None = 1
    max_users: int | None = None
    files: list[FileRef] = field(default_factory=list)
    data_schema: dict[str, Any] | str | None = None
    slicer_script: str | None = None
    slicer_script_args: dict[str, str] | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: dict[str, str] | None = None


def fileref_to_file_definition(ref: FileRef) -> FileDefinition:
    """Convert a FileRef to a FileDefinition for DB storage.

    Args:
        ref: FileRef binding a File to a role.

    Returns:
        FileDefinition ready for RecordType.file_registry.
    """
    return FileDefinition(
        name=ref.file.name,
        pattern=ref.file.pattern,
        description=ref.file.description,
        required=ref.required,
        multiple=ref.file.multiple,
        role=ref.role,
    )
