"""Synchronous file path resolver shared by API, pipeline, and Slicer.

``FileResolver`` pre-computes per-DICOM-level working directories from a
``RecordRead`` (or a ``StudyRead``/``SeriesRead``) and resolves
``FileDefinitionRead`` patterns to absolute paths. It is used by:

- ``clarinet/api/routers/record.py`` (via ``RecordService`` / file validation)
- ``clarinet/services/file_validation.py``
- ``clarinet/services/record_service.py``
- ``clarinet/services/slicer/context.py``
- ``clarinet/services/pipeline/context.py`` (re-exports for backward
  compatibility; pipeline tasks reach the same class via
  ``ctx.files``).

Living in ``services/common`` keeps the dependency direction sane —
``file_validation`` no longer has to import from the pipeline package
to share path-rendering logic, and the import chain stays free of
TaskIQ / aio-pika / broker initialisation for callers that only need
``FileResolver``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import AnonPathError
from clarinet.models.base import DicomQueryLevel
from clarinet.settings import settings
from clarinet.utils.file_patterns import PLACEHOLDER_REGEX, glob_file_paths

if TYPE_CHECKING:
    from clarinet.config.primitives import FileDef
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.record import RecordRead
    from clarinet.models.study import SeriesRead, StudyRead

    type FileDefArg = FileDefinitionRead | FileDef | str


__all__ = ["FileResolver", "resolve_pattern_from_dict"]


def resolve_pattern_from_dict(pattern: str, fields: dict[str, Any]) -> str:
    """Replace {placeholder} tokens in *pattern* using a flat dict.

    Supports dotted paths (``{data.BIRADS_R}``) by splitting the key on ``"."``
    and walking nested dicts.

    Args:
        pattern: Pattern string with ``{field}`` placeholders.
        fields: Flat or nested dict of replacement values.

    Returns:
        Pattern with all recognised placeholders replaced.
        Unknown placeholders are left as-is.
    """

    def _replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        parts = key.split(".")
        obj: Any = fields
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return match.group(0)
            if obj is None:
                return ""
        return str(obj) if obj is not None else ""

    return PLACEHOLDER_REGEX.sub(_replacer, pattern)


def _resolve_patient_dir(
    *,
    anon_id: str | None,
    raw_id: str,
    fallback_to_unanonymized: bool,
) -> str:
    """Pick the patient directory name (``anon_id`` or raw id)."""
    if anon_id is not None:
        return anon_id
    if fallback_to_unanonymized:
        return raw_id
    raise AnonPathError(
        f"Patient has no anon_id (patient_id={raw_id!r}); "
        "pass fallback_to_unanonymized=True for UX call sites"
    )


def _resolve_required_anon(
    *,
    anon: str | None,
    raw: str | None,
    kind: str,
    fallback_to_unanonymized: bool,
) -> str:
    """Pick the anonymized UID when the entity is required (from_series / from_study)."""
    if anon:
        return anon
    if fallback_to_unanonymized:
        return raw or ""
    raise AnonPathError(
        f"{kind.capitalize()} has no anon_uid ({kind}_uid={raw!r}); "
        "pass fallback_to_unanonymized=True for UX call sites"
    )


def _resolve_dir_with_snapshot(
    *,
    entity: Any,
    snapshot_anon: str | None,
    raw: str | None,
    kind: str,
    fallback_to_unanonymized: bool,
) -> str | None:
    """Pick a study/series dir for ``build_working_dirs``.

    Returns ``None`` when the record lacks the corresponding raw UID
    altogether (PATIENT-level → no study dir, STUDY-level → no series dir).

    ``entity`` is the loaded relationship (``record.study`` /
    ``record.series``) or ``None`` when the relation was not eager-loaded.
    When loaded, its ``anon_uid`` is authoritative — the ``study_anon_uid``
    snapshot column on the record is only consulted for the lazy-load case.
    """
    if entity is not None:
        anon: str | None = entity.anon_uid
        if anon:
            return anon
        if fallback_to_unanonymized:
            return raw or None
        raise AnonPathError(
            f"{kind.capitalize()} has no anon_uid ({kind}_uid={raw!r}); "
            "pass fallback_to_unanonymized=True for UX call sites"
        )
    # Relationship not loaded — consult the record-level snapshot, then raw
    if raw is None:
        return None
    if snapshot_anon:
        return snapshot_anon
    if fallback_to_unanonymized:
        return raw
    raise AnonPathError(
        f"{kind.capitalize()} has no anon_uid ({kind}_uid={raw!r}); "
        "pass fallback_to_unanonymized=True for UX call sites"
    )


class FileResolver:
    """Sync-only file path resolver.

    Pre-computes working directories for all DICOM levels from a ``RecordRead``
    and resolves ``FileDefinitionRead`` patterns to absolute paths.

    Args:
        working_dirs: Pre-computed dirs keyed by ``DicomQueryLevel``.
        record_type_level: Default DICOM level of the record type.
        file_registry: File definitions from the record type.
        fields: Placeholder values for pattern resolution.
    """

    def __init__(
        self,
        working_dirs: dict[DicomQueryLevel, Path],
        record_type_level: DicomQueryLevel,
        file_registry: list[FileDefinitionRead],
        fields: dict[str, Any],
    ) -> None:
        self._working_dirs = working_dirs
        self._record_type_level = record_type_level
        self._registry: dict[str, FileDefinitionRead] = {fd.name: fd for fd in file_registry}
        self._fields = fields
        self._accessed_files: dict[str, Path] = {}

    # ── Static factories ──

    @staticmethod
    def build_working_dirs(
        record: RecordRead,
        *,
        fallback_to_unanonymized: bool = False,
    ) -> dict[DicomQueryLevel, Path]:
        """Build working-directory map from a ``RecordRead``.

        Replicates ``RecordRead._get_working_folder()`` logic for all three
        DICOM levels so that cross-level file access is possible.

        Args:
            record: Fully-loaded record (patient, study, series relations).
            fallback_to_unanonymized: If ``False`` (default — backend safe
                mode), missing anonymized identifiers raise
                ``AnonPathError`` instead of silently rendering a path
                against raw UIDs. UX callers may pass ``True`` to keep the
                legacy fallback.

        Returns:
            Dict mapping each available level to its ``Path``.
        """
        base = record.clarinet_storage_path or settings.storage_path
        patient_dir_name = _resolve_patient_dir(
            anon_id=record.patient.anon_id,
            raw_id=record.patient_id,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        dirs: dict[DicomQueryLevel, Path] = {
            DicomQueryLevel.PATIENT: Path(base) / patient_dir_name,
        }

        study_dir_name = _resolve_dir_with_snapshot(
            entity=record.study,
            snapshot_anon=record.study_anon_uid,
            raw=record.study_uid,
            kind="study",
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        if study_dir_name is not None:
            dirs[DicomQueryLevel.STUDY] = dirs[DicomQueryLevel.PATIENT] / study_dir_name

            series_dir_name = _resolve_dir_with_snapshot(
                entity=record.series,
                snapshot_anon=record.series_anon_uid,
                raw=record.series_uid,
                kind="series",
                fallback_to_unanonymized=fallback_to_unanonymized,
            )
            if series_dir_name is not None:
                dirs[DicomQueryLevel.SERIES] = dirs[DicomQueryLevel.STUDY] / series_dir_name

        return dirs

    @staticmethod
    def build_working_dirs_from_series(
        series: SeriesRead,
        *,
        fallback_to_unanonymized: bool = False,
    ) -> dict[DicomQueryLevel, Path]:
        """Build working-directory map from a ``SeriesRead``.

        Args:
            series: Fully-loaded series (study, patient relations).
            fallback_to_unanonymized: see :meth:`build_working_dirs`.

        Returns:
            Dict mapping each available level to its ``Path``.
        """
        base = settings.storage_path
        patient = series.study.patient
        patient_dir_name = _resolve_patient_dir(
            anon_id=patient.anon_id,
            raw_id=patient.id,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        study_dir_name = _resolve_required_anon(
            anon=series.study.anon_uid,
            raw=series.study.study_uid,
            kind="study",
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        series_dir_name = _resolve_required_anon(
            anon=series.anon_uid,
            raw=series.series_uid,
            kind="series",
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        patient_path = Path(base) / patient_dir_name
        return {
            DicomQueryLevel.PATIENT: patient_path,
            DicomQueryLevel.STUDY: patient_path / study_dir_name,
            DicomQueryLevel.SERIES: patient_path / study_dir_name / series_dir_name,
        }

    @staticmethod
    def build_working_dirs_from_study(
        study: StudyRead,
        *,
        fallback_to_unanonymized: bool = False,
    ) -> dict[DicomQueryLevel, Path]:
        """Build working-directory map from a ``StudyRead``.

        Args:
            study: Fully-loaded study (patient relation).
            fallback_to_unanonymized: see :meth:`build_working_dirs`.

        Returns:
            Dict mapping available levels to their ``Path``.
        """
        base = settings.storage_path
        patient = study.patient
        patient_dir_name = _resolve_patient_dir(
            anon_id=patient.anon_id,
            raw_id=patient.id,
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        study_dir_name = _resolve_required_anon(
            anon=study.anon_uid,
            raw=study.study_uid,
            kind="study",
            fallback_to_unanonymized=fallback_to_unanonymized,
        )
        patient_path = Path(base) / patient_dir_name
        return {
            DicomQueryLevel.PATIENT: patient_path,
            DicomQueryLevel.STUDY: patient_path / study_dir_name,
        }

    @staticmethod
    def build_fields(record: RecordRead) -> dict[str, Any]:
        """Extract placeholder values from a ``RecordRead``.

        Args:
            record: Fully-loaded record.

        Returns:
            Flat dict suitable for ``resolve_pattern_from_dict``.
        """
        fields: dict[str, Any] = {
            "id": record.id,
            "user_id": record.user_id,
            "patient_id": record.patient_id,
            "study_uid": record.study_uid,
            "series_uid": record.series_uid,
            "record_type": {"name": record.record_type.name},
            "data": record.data or {},
            "origin_type": record.record_type.name,
        }
        return fields

    # ── Public methods ──

    def dir(self, level: DicomQueryLevel | None = None) -> Path:
        """Get working directory for the given DICOM level.

        Args:
            level: Target level (default: record type's level).

        Returns:
            Absolute directory path.

        Raises:
            KeyError: If the level is not available in working dirs.
        """
        level = level or self._record_type_level
        return self._working_dirs[level]

    def _lookup(self, file_def: FileDefArg) -> FileDefinitionRead | FileDef:
        """Resolve a file definition by name or pass-through.

        Accepts ``FileDefinitionRead``, ``FileDef`` (config primitive),
        or a string name. Non-string objects are returned as-is, enabling
        cross-record-type file access when passing ``FileDef`` objects
        that are not in this record type's registry.

        Args:
            file_def: ``FileDefinitionRead``, ``FileDef``, or name string.

        Returns:
            The resolved file definition object.

        Raises:
            KeyError: If string name is not in the registry.
        """
        if isinstance(file_def, str):
            return self._registry[file_def]
        return file_def

    def resolve(self, file_def: FileDefArg, **overrides: Any) -> Path:
        """Resolve a file definition pattern to an absolute path.

        Args:
            file_def: ``FileDefinitionRead`` or its ``name``.
            **overrides: Extra placeholder values merged on top of ``fields``.

        Returns:
            Absolute path to the resolved file.
        """
        fd = self._lookup(file_def)
        level = fd.level or self._record_type_level
        working_dir = self._working_dirs[level]
        merged = {**self._fields, **overrides}
        filename = resolve_pattern_from_dict(fd.pattern, merged)
        path = working_dir / filename
        if fd.name not in self._accessed_files:
            self._accessed_files[fd.name] = path
        return path

    def exists(self, file_def: FileDefArg, **overrides: Any) -> bool:
        """Check whether a resolved file exists on disk.

        Args:
            file_def: ``FileDefinitionRead`` or its ``name``.
            **overrides: Extra placeholder values.

        Returns:
            ``True`` if the file exists.
        """
        return self.resolve(file_def, **overrides).is_file()

    def glob(self, file_def: FileDefArg) -> list[Path]:
        """Glob a collection file definition (``multiple=True``).

        Replaces all placeholders with ``*`` and globs in the working dir.

        Args:
            file_def: ``FileDefinitionRead`` or its ``name``.

        Returns:
            Sorted list of matching paths.
        """
        fd = self._lookup(file_def)
        level = fd.level or self._record_type_level
        working_dir = self._working_dirs[level]
        paths = glob_file_paths(fd, working_dir)
        if fd.name not in self._accessed_files:
            self._accessed_files[fd.name] = paths[0] if paths else working_dir
        return paths

    @property
    def accessed_files(self) -> dict[str, Path]:
        """Return a copy of the accessed files mapping.

        Returns:
            Dict mapping file definition names to their resolved paths.
        """
        return dict(self._accessed_files)

    async def snapshot_checksums(self) -> dict[str, str | None]:
        """Compute checksums for all registered file definitions.

        Iterates the file registry and resolves each file definition to a path
        without tracking access. Used to capture pre-task state for change detection.

        Returns:
            Dict mapping file definition names to their SHA256 checksums (or None).
        """
        from clarinet.utils.file_checksums import compute_file_checksum

        checksums: dict[str, str | None] = {}
        for fd in self._registry.values():
            try:
                level = fd.level or self._record_type_level
                working_dir = self._working_dirs[level]
                merged = dict(self._fields)
                filename = resolve_pattern_from_dict(fd.pattern, merged)
                path = working_dir / filename
                checksums[fd.name] = await compute_file_checksum(path)
            except (KeyError, ValueError):
                checksums[fd.name] = None
        return checksums
