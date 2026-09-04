"""
File validation service for Clarinet framework.

This module provides file validation functionality for Records,
checking that required files exist and match defined patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from clarinet.exceptions.domain import ImageError, InputGridMismatchError, ValidationError
from clarinet.files import Files, join_within
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileRole
from clarinet.services.image.grid import RelationKind
from clarinet.services.image.grid_io import classify_pair

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
        registry: Full file registry used to resolve ``grid_conform_to``
            references. Defaults to *file_definitions* when omitted. Reference
            lookup spans every role, not just the validated set: a caller may
            pass only the INPUT subset to validate while a declared reference
            is bound as OUTPUT or INTERMEDIATE on the same record type —
            Task 3's config-load validator accepts any role pairing, so
            runtime resolution must too.

    Examples:
        >>> validator = FileValidator(input_file_defs)
        >>> result = validator.validate(record, Path("/data/study"))
        >>> if not result.valid:
        ...     for error in result.errors:
        ...         print(f"Error: {error.message}")
    """

    def __init__(
        self,
        file_definitions: list[FileDefinitionRead],
        registry: list[FileDefinitionRead] | None = None,
    ):
        self._file_definitions = file_definitions
        # Reference lookup spans every role — the validated set stays as passed.
        self._by_name = {
            fd.name: fd for fd in (registry if registry is not None else file_definitions)
        }

    def _target_path(
        self,
        file_def: FileDefinitionRead,
        record: RecordBase,
        directory: Path,
        working_dirs: dict[DicomQueryLevel, Path] | None,
        parent: RecordBase | None,
    ) -> tuple[str, Path]:
        """Resolve one file definition to its rendered filename and absolute path.

        Returns both halves — not just the path — because the rendered
        filename (as opposed to ``path.name``) is what ``matched_files``
        must carry: a pattern is free to render a subdirectory-bearing
        string (nothing constrains it to a bare basename), and that value
        round-trips through ``RecordFileLink.filename`` into a later
        ``working_dir / filename`` join elsewhere. Reducing it to ``.name``
        here would silently drop the subdirectory for such a pattern.

        join_within raises UnsafePathError uncaught rather than being folded
        into the caller's ``errors``. It enforces containment only, and both
        halves that could feed it a non-contained name are covered upstream:
        Files.render_for guards every substituted *value* (path_safe=True
        rejects "/", "\\", NUL and a bare "."/"..", and raises UnsafePathError
        itself for a degenerate stored identity value — a patient_id of ".." is
        legal per PATIENT_ID_REGEX), and validate_file_pattern guards the
        pattern's literal text and its worst-case render. That validator also
        runs on every stored row when RecordType.file_registry is read — a
        failing legacy row is skipped with a WARNING, never handed on — so a
        definition that arrives here through validate_record_files has passed
        it. The join is a backstop for a caller that hands FileValidator an
        unvalidated definition directly (none in-tree). Either way the
        violation is a server-side fact — a stored value or a definition —
        never data the current caller submitted. On the principle that a
        violation surfaces according to who caused it, that makes it a
        server-side failure, not a per-record 422.
        """
        resolved = Files.render_for(record, file_def.pattern, parent=parent)
        if file_def.level and working_dirs and file_def.level in working_dirs:
            return resolved, join_within(working_dirs[file_def.level], resolved)
        return resolved, join_within(directory, resolved)

    def _grid_error(
        self,
        file_def: FileDefinitionRead,
        subject: Path,
        record: RecordBase,
        directory: Path,
        working_dirs: dict[DicomQueryLevel, Path] | None,
        parent: RecordBase | None,
    ) -> FileValidationError | None:
        """Classify *subject* against its declared reference; None when clean.

        Reads both grids off disk — an in-memory or in-scene comparison cannot
        see a mirror, because a viewer canonicalizes both sides identically at
        load time.
        """
        ref_def = self._by_name.get(file_def.grid_conform_to or "")
        if ref_def is None:
            return FileValidationError(
                file_name=file_def.name,
                error_type="grid_mismatch",
                message=(
                    f"'{file_def.name}' declares grid_conform_to="
                    f"'{file_def.grid_conform_to}', which is not bound to this "
                    f"record type"
                ),
            )

        _, reference = self._target_path(ref_def, record, directory, working_dirs, parent)
        if not reference.is_file():
            return FileValidationError(
                file_name=file_def.name,
                error_type="grid_mismatch",
                message=(
                    f"Grid reference '{ref_def.name}' for '{file_def.name}' is "
                    f"not on disk (expected: {reference.name})"
                ),
            )

        try:
            verdict = classify_pair(subject, reference)
        except ImageError as e:
            return FileValidationError(
                file_name=file_def.name,
                error_type="grid_mismatch",
                message=f"Cannot read grid for '{file_def.name}' or its reference: {e}",
            )

        if verdict.kind is RelationKind.SAME:
            return None
        return FileValidationError(
            file_name=file_def.name,
            error_type="grid_mismatch",
            message=(
                f"'{file_def.name}' does not share '{ref_def.name}'s grid "
                f"({verdict.kind.value}):" + verdict.describe(file_def.name, ref_def.name)
            ),
        )

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

        Raises:
            UnsafePathError: if a singular definition's rendered name would
                escape *target_dir*, or a substituted value could alter the
                path (``Files.render_for``'s value guard). Propagates uncaught
                rather than becoming a ``FileValidationError`` entry — see
                ``_target_path``.

        A ``multiple=True`` definition is never rendered here. A required one
        is reported as ``missing`` without touching the disk — matching a
        collection by glob is issue #562.
        """
        if not self._file_definitions:
            return FileValidationResult(valid=True)

        errors: list[FileValidationError] = []
        matched: dict[str, str] = {}

        for file_def in self._file_definitions:
            if file_def.multiple:
                # Never rendered. A collection's placeholders are wildcards,
                # and validate_file_pattern skips both render-time rules for
                # it on exactly that premise — so `{study_uid}/slice_{n}.dcm`
                # is a *legal* collection which, rendered for a patient-level
                # record, is `/slice_.dcm`: join_within in _target_path would
                # rightly refuse it, turning a config-load-legal definition
                # into a 500. Matching a collection by glob is issue #562;
                # until then a required one is reported missing without
                # touching the disk — the verdict rendering reached anyway,
                # since a wildcard rendered to nothing and the probe never
                # found a file.
                if file_def.required:
                    errors.append(
                        FileValidationError(
                            file_name=file_def.name,
                            error_type="missing",
                            message=f"Required file '{file_def.name}' is a collection "
                            f"(multiple=True), which file validation cannot match yet "
                            f"(pattern: {file_def.pattern}; see issue #562)",
                        )
                    )
                continue

            resolved, target_path = self._target_path(
                file_def, record, directory, working_dirs, parent
            )
            filename = resolved if target_path.is_file() else None

            if filename:
                matched[file_def.name] = filename
                if file_def.grid_conform_to:
                    error = self._grid_error(
                        file_def, target_path, record, directory, working_dirs, parent
                    )
                    if error is not None:
                        errors.append(error)
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

    Accepts ``RecordRead`` (Pydantic) with eager-loaded relationships
    (patient/study/series/record_type).

    The blocking ``FileValidator.validate()`` call is offloaded to a
    dedicated FS thread pool via ``Files.in_thread`` to avoid blocking
    the event loop.

    For records that have not been anonymized yet, ``Files.for_reader``
    transparently falls back to raw UIDs so validation still produces a
    verdict against the legacy path.

    Args:
        record: RecordRead instance with all relations populated
        raise_on_invalid: If True, raise ``ValidationError`` on an invalid set —
            its ``InputGridMismatchError`` subclass (``code: GRID_MISMATCH``)
            when any error is a grid mismatch, so a client can tell it
            from a missing file.
        parent: Optional parent record for fallback pattern resolution.

    Returns:
        FileValidationResult if validation was performed, None if no input files defined
    """
    input_defs = [
        fd for fd in (record.record_type.file_registry or []) if fd.role == FileRole.INPUT
    ]
    if not input_defs:
        return None

    f = Files.for_reader(record)
    working_dirs, directory = f.dirs(), f.dir()
    validator = FileValidator(input_defs, registry=record.record_type.file_registry)
    result = cast(
        FileValidationResult,
        await Files.in_thread(validator.validate, record, directory, working_dirs, parent),
    )
    if not result.valid and raise_on_invalid:
        errors = "; ".join(f"{e.file_name}: {e.message}" for e in result.errors)
        if any(e.error_type == "grid_mismatch" for e in result.errors):
            raise InputGridMismatchError(f"File validation failed: {errors}")
        raise ValidationError(f"File validation failed: {errors}")
    return result
