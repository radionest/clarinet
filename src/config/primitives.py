"""Config-level models for RecordType definitions.

These primitives are used in Python config files (``record_types.py``) to
define RecordTypes in a declarative, type-safe way.
"""

from typing import Any

from pydantic import BaseModel

from src.models.base import DicomQueryLevel
from src.models.file_schema import FileDefinitionRead, FileRole


class File(BaseModel):
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
    level: DicomQueryLevel | None = None
    description: str | None = None
    name: str = ""


class FileRef(BaseModel, frozen=True):
    """Binds a File to a RecordType with a role.

    Supports positional first argument for backward compatibility::

        FileRef(seg_mask, role=FileRole.INPUT)

    Attributes:
        file: Reference to a File instance.
        role: File role in the processing pipeline.
        required: Whether this file is required.
    """

    file: File
    role: FileRole = FileRole.OUTPUT
    required: bool = True

    def __init__(self, file: File, /, **kwargs: Any) -> None:
        super().__init__(file=file, **kwargs)


class RecordTypeDef(BaseModel):
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
    parent_type_name: str | None = None
    role_name: str | None = None
    min_users: int | None = 1
    max_users: int | None = None
    files: list[FileRef] = []
    data_schema: dict[str, Any] | str | None = None
    slicer_script: str | None = None
    slicer_script_args: dict[str, str] | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: dict[str, str] | None = None


def fileref_to_file_definition(ref: FileRef) -> FileDefinitionRead:
    """Convert a FileRef to a FileDefinitionRead for config processing.

    Args:
        ref: FileRef binding a File to a role.

    Returns:
        FileDefinitionRead ready for reconciler consumption.
    """
    return FileDefinitionRead(
        name=ref.file.name,
        pattern=ref.file.pattern,
        description=ref.file.description,
        required=ref.required,
        multiple=ref.file.multiple,
        role=ref.role,
        level=ref.file.level,
    )
