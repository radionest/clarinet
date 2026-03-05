"""
File validation service for Clarinet framework.

This module provides file validation functionality for Records,
checking that required files exist and match defined patterns.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.utils.file_patterns import find_matching_file, resolve_pattern

if TYPE_CHECKING:
    from src.models.file_schema import FileDefinitionRead
    from src.models.record import RecordBase, RecordTypeBase


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
    directory and match the patterns defined in the RecordType.

    Args:
        record_type: RecordType containing file definitions

    Examples:
        >>> validator = FileValidator(record_type)
        >>> result = validator.validate_input_files(record, Path("/data/study"))
        >>> if not result.valid:
        ...     for error in result.errors:
        ...         print(f"Error: {error.message}")
    """

    def __init__(self, record_type: RecordTypeBase):
        self.record_type = record_type

    def validate_files(
        self,
        record: RecordBase,
        file_definitions: list[FileDefinitionRead] | None,
        directory: Path,
    ) -> FileValidationResult:
        """Validate files against a list of file definitions.

        Args:
            record: Record to validate files for
            file_definitions: List of FileDefinitionRead objects to validate
            directory: Directory where files should be located

        Returns:
            FileValidationResult with validation status and matched files
        """
        if not file_definitions:
            return FileValidationResult(valid=True)

        errors: list[FileValidationError] = []
        matched: dict[str, str] = {}

        for file_def in file_definitions:
            filename = find_matching_file(directory, file_def.pattern, record)

            if filename:
                matched[file_def.name] = filename
            elif file_def.required:
                expected_name = resolve_pattern(file_def.pattern, record)
                errors.append(
                    FileValidationError(
                        file_name=file_def.name,
                        error_type="missing",
                        message=f"Required file '{file_def.name}' not found "
                        f"(expected: {expected_name}, pattern: {file_def.pattern})",
                    )
                )

        return FileValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            matched_files=matched,
        )

    def validate_input_files(
        self,
        record: RecordBase,
        directory: Path,
    ) -> FileValidationResult:
        """Validate input files for a record.

        Uses input_files computed from file_registry (role=input).

        Args:
            record: Record to validate input files for
            directory: Directory where input files should be located

        Returns:
            FileValidationResult with validation status
        """
        return self.validate_files(record, self.record_type.input_files, directory)

    def validate_output_files(
        self,
        record: RecordBase,
        directory: Path,
    ) -> FileValidationResult:
        """Validate output files for a record.

        Uses output_files computed from file_registry (role=output).

        Args:
            record: Record to validate output files for
            directory: Directory where output files should be located

        Returns:
            FileValidationResult with validation status
        """
        return self.validate_files(record, self.record_type.output_files, directory)
