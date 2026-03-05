"""
File validation service for Clarinet framework.

This module provides file validation functionality for Records,
checking that required files exist and match defined patterns.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.utils.file_patterns import resolve_pattern

if TYPE_CHECKING:
    from src.models.file_schema import FileDefinitionRead
    from src.models.record import RecordBase


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
    ) -> FileValidationResult:
        """Validate files against the file definitions.

        Args:
            record: Record to validate files for
            directory: Directory where files should be located

        Returns:
            FileValidationResult with validation status and matched files
        """
        if not self._file_definitions:
            return FileValidationResult(valid=True)

        errors: list[FileValidationError] = []
        matched: dict[str, str] = {}

        for file_def in self._file_definitions:
            resolved = resolve_pattern(file_def.pattern, record)
            filename = resolved if (directory / resolved).is_file() else None

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
