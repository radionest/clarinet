"""Config-level models for RecordType definitions.

These primitives are used in Python config files (``record_types.py``) to
define RecordTypes in a declarative, type-safe way.
"""

from typing import Any, Literal

from pydantic import BaseModel, field_validator

from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.utils.validators import validate_slug


def _coerce_dicom_level(value: Any) -> Any:
    """Coerce a string to DicomQueryLevel if possible."""
    if isinstance(value, str) and not isinstance(value, DicomQueryLevel):
        return DicomQueryLevel(value.upper())
    return value


def _coerce_file_role(value: Any) -> Any:
    """Coerce a string to FileRole if possible."""
    if isinstance(value, str) and not isinstance(value, FileRole):
        return FileRole(value.lower())
    return value


class FileDef(BaseModel):
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
    level: DicomQueryLevel
    description: str | None = None
    name: str = ""

    @field_validator("level", mode="before")
    @classmethod
    def _coerce_level(cls, v: Any) -> Any:
        """Accept string literals like ``"PATIENT"`` for level."""
        return _coerce_dicom_level(v)


class FileRef(BaseModel, frozen=True):
    """Binds a FileDef to a RecordDef with a role.

    Supports positional arguments for convenience::

        FileRef(seg_mask, "input")
        FileRef(seg_mask, role=FileRole.INPUT)

    Attributes:
        file: Reference to a FileDef instance.
        role: File role in the processing pipeline.
        required: Whether this file is required.
    """

    file: FileDef
    role: FileRole = FileRole.OUTPUT
    required: bool = True

    def __init__(
        self, file: FileDef, /, role: FileRole | str = FileRole.OUTPUT, **kwargs: Any
    ) -> None:
        super().__init__(file=file, role=role, **kwargs)

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role(cls, v: Any) -> Any:
        """Accept string literals like ``"input"`` for role."""
        return _coerce_file_role(v)


class RecordDef(BaseModel):
    """RecordType definition — maps to RecordTypeCreate fields.

    Attributes:
        name: Unique name for the record type (5-30 chars, lowercase slug).
        level: DICOM query level.
        description: Human-readable description.
        label: Short display label.
        role: Required user role name (alias for role_name).
        role_name: Required user role name.
        min_records: Minimum number of records.
        max_records: Maximum number of records.
        files: List of FileRef bindings.
        data_schema: JSON Schema dict or path to .json file.
        slicer_script: Inline script or path to .py file.
        slicer_script_args: Arguments for slicer script.
        slicer_result_validator: Inline validator or path to .py file.
        slicer_result_validator_args: Arguments for result validator.
    """

    name: str

    @field_validator("name")
    @classmethod
    def validate_name_slug(cls, v: str) -> str:
        """Enforce lowercase slug format: ``[a-z][a-z0-9]*(-[a-z0-9]+)*``."""
        return validate_slug(v)

    level: DicomQueryLevel = DicomQueryLevel.SERIES
    description: str | None = None
    label: str | None = None
    role_name: str | None = None
    min_records: int | None = 1
    max_records: int | None = None
    files: list[FileRef] = []
    data_schema: dict[str, Any] | str | None = None
    slicer_script: str | None = None
    slicer_script_args: dict[str, str] | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: dict[str, str] | None = None
    slicer_context_hydrators: list[str] | None = None
    mask_patient_data: bool = True
    viewer_mode: Literal["single_series", "all_series"] = "single_series"

    def __init__(self, *, role: str | None = None, **kwargs: Any) -> None:
        """Accept ``role`` as a user-friendly alias for ``role_name``."""
        if role is not None and "role_name" not in kwargs:
            kwargs["role_name"] = role
        super().__init__(**kwargs)

    @field_validator("level", mode="before")
    @classmethod
    def _coerce_level(cls, v: Any) -> Any:
        """Accept string literals like ``"SERIES"`` for level."""
        return _coerce_dicom_level(v)


def fileref_to_file_definition(ref: FileRef) -> FileDefinitionRead:
    """Convert a FileRef to a FileDefinitionRead for config processing.

    Args:
        ref: FileRef binding a FileDef to a role.

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


# Backward compatibility aliases
File = FileDef
RecordTypeDef = RecordDef
