"""Config-level models for RecordType definitions.

These primitives are used in Python config files (``record_types.py``) to
define RecordTypes in a declarative, type-safe way.
"""

import warnings
from typing import Any

from pydantic import BaseModel, Field, field_validator

from clarinet.models.base import DicomQueryLevel, ViewerMode
from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.models.uniqueness import (
    DEFAULT_UNIQUE_BY,
    canonical_unique_by,
    legacy_unique_per_user,
)
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


def _coerce_viewer_mode(value: Any) -> Any:
    """Coerce a string to ViewerMode if possible."""
    if isinstance(value, str) and not isinstance(value, ViewerMode):
        return ViewerMode(value)
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
        allow_path_collision: Opt out of the default path-collision guard —
            this binding may share its resolved path with another file of
            the record.
    """

    file: FileDef
    role: FileRole = FileRole.OUTPUT
    required: bool = True
    allow_path_collision: bool = False

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
        ui_schema: formosh ui-schema dict or path to .json file. Presentation hints
            (widgets, ordering, placeholders) layered on top of data_schema.
        slicer_script: Inline script or path to .py file.
        slicer_script_args: Arguments for slicer script.
        slicer_result_validator: Inline validator or path to .py file.
        slicer_result_validator_args: Arguments for result validator.
        slicer_context_hydrators: Names of Slicer context hydrators to run.
        data_validators: Names of cross-field RecordData validators to run.
        mask_patient_data: Mask patient/study/series identifiers for non-superusers
            once the patient is anonymized.
        unique_by: Uniqueness partition — at most one record of this type per
            unique combination of these scopes (subset of {"user", "parent"}).
            None disables the constraint. Default {"user", "parent"}. The
            deprecated ``unique_per_user`` kwarg (True/False) still works —
            it translates to {"user"}/None and emits a DeprecationWarning; an
            explicit unique_by wins over it.
        parent_required: Require a parent record at creation.
        inherit_user_from_parent: Child inherits user_id from its parent record.
        editable: Whether non-superusers may change a submitted (finished) record.
        edit_window_days: Days a finished record stays editable; None = no limit.
        shared_editing: Any role-holder may edit any record of this type;
            each edit reassigns ownership to the editor. Requires
            'user' not in unique_by.
        viewer_mode: How many series the viewer loads (single vs all series).
        allowed_viewers: Restrict the DICOM viewers shown for this type to these
            viewer names (matching ``ViewerInfo.name``); None/empty = all
            configured viewers.
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
    ui_schema: dict[str, Any] | str | None = None
    slicer_script: str | None = None
    slicer_script_args: dict[str, str] | None = None
    slicer_result_validator: str | None = None
    slicer_result_validator_args: dict[str, str] | None = None
    slicer_context_hydrators: list[str] | None = None
    data_validators: list[str] | None = None
    mask_patient_data: bool = True
    unique_by: frozenset[str] | None = Field(default_factory=lambda: frozenset(DEFAULT_UNIQUE_BY))
    parent_required: bool = False
    inherit_user_from_parent: bool = False
    editable: bool = True
    edit_window_days: int | None = None
    shared_editing: bool = False
    viewer_mode: ViewerMode = ViewerMode.SINGLE_SERIES
    allowed_viewers: list[str] | None = None

    def __init__(
        self,
        *,
        role: str | None = None,
        unique_per_user: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Accept ``role`` as a user-friendly alias for ``role_name`` and
        translate the deprecated ``unique_per_user`` flag into ``unique_by``.
        """
        if role is not None and "role_name" not in kwargs:
            kwargs["role_name"] = role
        if unique_per_user is not None:
            warnings.warn(
                "unique_per_user is deprecated; use unique_by",
                DeprecationWarning,
                stacklevel=2,
            )
            if "unique_by" not in kwargs:  # explicit unique_by wins
                kwargs["unique_by"] = legacy_unique_per_user(unique_per_user)
        super().__init__(**kwargs)

    @field_validator("level", mode="before")
    @classmethod
    def _coerce_level(cls, v: Any) -> Any:
        """Accept string literals like ``"SERIES"`` for level."""
        return _coerce_dicom_level(v)

    @field_validator("viewer_mode", mode="before")
    @classmethod
    def _coerce_viewer_mode(cls, v: Any) -> Any:
        """Accept string literals like ``"all_series"`` for viewer_mode."""
        return _coerce_viewer_mode(v)

    @field_validator("unique_by", mode="before")
    @classmethod
    def _canonical_unique_by(cls, v: Any) -> Any:
        """Canonicalize unique_by via the shared partition validator."""
        return canonical_unique_by(v)


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
        allow_path_collision=ref.allow_path_collision,
    )


# Backward compatibility aliases
File = FileDef
RecordTypeDef = RecordDef
