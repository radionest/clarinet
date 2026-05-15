"""
File validation service for Clarinet framework.

This module provides file validation functionality for Records,
checking that required files exist and match defined patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.exceptions.domain import AnonPathError, ValidationError
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileRole
from clarinet.repositories.file_repository import FileRepository
from clarinet.services.common.file_resolver import FileResolver
from clarinet.utils.file_patterns import resolve_pattern
from clarinet.utils.fs import run_in_fs_thread

if TYPE_CHECKING:
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.record import RecordBase, RecordRead


@dataclass
class FileValidationError:
    """Represents a single file validation error.

    Attributes:
        file_name: Name of the file definition that failed validation
        error_type: Type of error ("missing", "pattern_mismatch")
        message: Human-readable error message
    """

    file_name: str
    error_type: str
    message: str


@dataclass
class FileValidationResult:
    """Result of file validation.

    Attributes:
        valid: True if all validations passed
        errors: List of validation errors (empty if valid)
        matched_files: Dict mapping file definition names to actual filenames
    """

    valid: bool
    errors: list[FileValidationError] = field(default_factory=list)
    matched_files: dict[str, str] = field(default_factory=dict)


class FileValidator:
    """Validator for files associated with Records.

    This validator checks that required files exist in the expected
    directory and match the patterns defined in the file definitions.

    Args:
        file_definitions: List of FileDefinitionRead objects to validate against

    Examples:
        >>> validator = FileValidator(input_file_defs)
        >>> result = validator.validate(record, Path("/data/study"))
        >>> if not result.valid:
        ...     for error in result.errors:
        ...         print(f"Error: {error.message}")
    """

    def __init__(self, file_definitions: list[FileDefinitionRead]):
        self._file_definitions = file_definitions

    def validate(
        self,
        record: RecordBase,
        directory: Path,
        working_dirs: dict[DicomQueryLevel, Path] | None = None,
        parent: RecordBase | None = None,
    ) -> FileValidationResult:
        """Validate files against the file definitions.

        Args:
            record: Record to validate files for
            directory: Default directory where files should be located
            working_dirs: Optional level-to-directory map for cross-level
                file lookups.  When a file definition has a ``level``
                attribute, the corresponding directory from this map is
                used instead of *directory*.
            parent: Optional parent record for fallback pattern resolution.

        Returns:
            FileValidationResult with validation status and matched files
        """
        if not self._file_definitions:
            return FileValidationResult(valid=True)

        errors: list[FileValidationError] = []
        matched: dict[str, str] = {}

        for file_def in self._file_definitions:
            resolved = resolve_pattern(file_def.pattern, record, parent)

            # Level-aware directory resolution
            if file_def.level and working_dirs and file_def.level in working_dirs:
                target_dir = working_dirs[file_def.level]
            else:
                target_dir = directory

            filename = resolved if (target_dir / resolved).is_file() else None

            if filename:
                matched[file_def.name] = filename
            elif file_def.required:
                errors.append(
                    FileValidationError(
                        file_name=file_def.name,
                        error_type="missing",
                        message=f"Required file '{file_def.name}' not found "
                        f"(expected: {resolved}, pattern: {file_def.pattern})",
                    )
                )

        return FileValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            matched_files=matched,
        )


async def validate_record_files(
    record: RecordRead,
    *,
    raise_on_invalid: bool = False,
    parent: RecordRead | None = None,
) -> FileValidationResult | None:
    """Validate input files for a record.

    Accepts ``RecordRead`` (Pydantic) — ``FileRepository`` requires the
    eager-loaded relationships (patient/study/series/record_type) that
    are populated on ``RecordRead`` via ``RecordRead.model_validate(record)``.

    The blocking ``FileValidator.validate()`` call is offloaded to a
    dedicated FS thread pool to avoid blocking the event loop.

    For records that have not been anonymized yet, ``FileRepository``
    raises ``AnonPathError``; we fall back to raw UIDs via
    ``FileResolver.build_working_dirs(..., fallback_to_unanonymized=True)``
    so validation still produces a verdict against the legacy path.

    Args:
        record: RecordRead instance with all relations populated
        raise_on_invalid: If True, raise ValidationError on missing files.
        parent: Optional parent record for fallback pattern resolution.

    Returns:
        FileValidationResult if validation was performed, None if no input files defined
    """
    input_defs = [
        fd for fd in (record.record_type.file_registry or []) if fd.role == FileRole.INPUT
    ]
    if not input_defs:
        return None

    try:
        repo = FileRepository(record)
        working_dirs = repo.working_dirs_all()
        directory = repo.working_dir
    except AnonPathError:
        # Record predates anonymization (or anonymization is in flight) —
        # fall back to raw UIDs so input files still get the same
        # validity verdict the writer would land on. Strict mode in
        # ``FileRepository`` keeps surfacing the asymmetric-anonymization
        # race for backend writers; this graceful path is only for
        # *readers* that must keep working through the legacy flow.
        working_dirs = FileResolver.build_working_dirs(record, fallback_to_unanonymized=True)
        directory = working_dirs[record.record_type.level]

    validator = FileValidator(input_defs)
    result = await run_in_fs_thread(validator.validate, record, directory, working_dirs, parent)
    if not result.valid and raise_on_invalid:
        errors = "; ".join(f"{e.file_name}: {e.message}" for e in result.errors)
        raise ValidationError(f"File validation failed: {errors}")
    return result
