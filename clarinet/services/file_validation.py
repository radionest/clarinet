"""
File validation service for Clarinet framework.

This module provides file validation functionality for Records,
checking that required files exist and match defined patterns.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.exceptions.domain import ValidationError
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileRole
from clarinet.settings import settings
from clarinet.utils.file_patterns import resolve_pattern
from clarinet.utils.fs import run_in_fs_thread

if TYPE_CHECKING:
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.record import RecordBase, RecordRead


def _build_working_dirs(record: RecordRead) -> dict[DicomQueryLevel, Path]:
    """Build working-directory map from a ``RecordRead``.

    Replicates ``FileResolver.build_working_dirs()`` logic from the pipeline
    module to keep file_validation independent of the pipeline package.

    Args:
        record: Fully-loaded record (patient, study, series relations).

    Returns:
        Dict mapping each available level to its ``Path``.
    """
    base = record.clarinet_storage_path or settings.storage_path
    patient_dir_name = (
        record.patient.anon_id if record.patient.anon_id is not None else record.patient_id
    )
    dirs: dict[DicomQueryLevel, Path] = {
        DicomQueryLevel.PATIENT: Path(base) / patient_dir_name,
    }

    # STUDY directory — prefer relationship, fall back to record-level anon UID
    study_dir_name: str | None = None
    if record.study is not None:
        study_dir_name = record.study.anon_uid or record.study_uid or ""
    elif record.study_uid:
        study_dir_name = record.study_anon_uid or record.study_uid

    if study_dir_name:
        dirs[DicomQueryLevel.STUDY] = dirs[DicomQueryLevel.PATIENT] / study_dir_name

        # SERIES directory — same fallback pattern
        series_dir_name: str | None = None
        if record.series is not None:
            series_dir_name = record.series.anon_uid or record.series_uid or ""
        elif record.series_uid:
            series_dir_name = record.series_anon_uid or record.series_uid

        if series_dir_name:
            dirs[DicomQueryLevel.SERIES] = dirs[DicomQueryLevel.STUDY] / series_dir_name

    return dirs


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
    ) -> FileValidationResult:
        """Validate files against the file definitions.

        Args:
            record: Record to validate files for
            directory: Default directory where files should be located
            working_dirs: Optional level-to-directory map for cross-level
                file lookups.  When a file definition has a ``level``
                attribute, the corresponding directory from this map is
                used instead of *directory*.

        Returns:
            FileValidationResult with validation status and matched files
        """
        if not self._file_definitions:
            return FileValidationResult(valid=True)

        errors: list[FileValidationError] = []
        matched: dict[str, str] = {}

        for file_def in self._file_definitions:
            resolved = resolve_pattern(file_def.pattern, record)

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
) -> FileValidationResult | None:
    """Validate input files for a record.

    Accepts ``RecordRead`` (Pydantic) because ``working_folder`` and other
    computed fields are defined on ``RecordRead``, not on the ORM ``Record``.
    Callers should convert via ``RecordRead.model_validate(record)`` first.

    The blocking ``FileValidator.validate()`` call is offloaded to a
    dedicated FS thread pool to avoid blocking the event loop.

    Args:
        record: RecordRead instance with all relations populated
        raise_on_invalid: If True, raise ValidationError on missing files.

    Returns:
        FileValidationResult if validation was performed, None if no input files defined
    """
    input_defs = [
        fd for fd in (record.record_type.file_registry or []) if fd.role == FileRole.INPUT
    ]
    if not input_defs:
        return None

    directory = Path(record.working_folder)
    working_dirs = _build_working_dirs(record)
    validator = FileValidator(input_defs)
    result = await run_in_fs_thread(validator.validate, record, directory, working_dirs)
    if not result.valid and raise_on_invalid:
        errors = "; ".join(f"{e.file_name}: {e.message}" for e in result.errors)
        raise ValidationError(f"File validation failed: {errors}")
    return result
