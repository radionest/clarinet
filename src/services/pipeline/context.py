"""
TaskContext system for pipeline tasks.

Provides FileResolver (sync file ops), RecordQuery (async record lookup),
and TaskContext (container) to eliminate boilerplate in pipeline tasks.

Example:
    @pipeline_task()
    async def my_task(msg: PipelineMessage, ctx: TaskContext):
        if ctx.files.exists(master_model):
            return
        seg_path = await ctx.records.file_path(
            "segment_CT_single", file="segmentation_single",
            series_uid=msg.series_uid,
        )
        img.save(result, ctx.files.resolve(master_model))
        await ctx.client.update_record_data(msg.record_id, {...})
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.exceptions.domain import PipelineStepError
from src.models.base import DicomQueryLevel
from src.settings import settings
from src.utils.file_patterns import PLACEHOLDER_REGEX, glob_file_paths
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.client import ClarinetClient
    from src.models.file_schema import FileDefinitionRead
    from src.models.record import RecordRead
    from src.models.study import SeriesRead, StudyRead

    from .message import PipelineMessage


def _resolve_pattern_from_dict(pattern: str, fields: dict[str, Any]) -> str:
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

    # ── Static factories (used by build_task_context & RecordQuery) ──

    @staticmethod
    def build_working_dirs(record: RecordRead) -> dict[DicomQueryLevel, Path]:
        """Build working-directory map from a ``RecordRead``.

        Replicates ``RecordRead._get_working_folder()`` logic for all three
        DICOM levels so that cross-level file access is possible.

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
        if record.study is not None:
            study_dir_name = record.study.anon_uid or record.study_uid or ""
            dirs[DicomQueryLevel.STUDY] = dirs[DicomQueryLevel.PATIENT] / study_dir_name
            if record.series is not None:
                series_dir_name = record.series.anon_uid or record.series_uid or ""
                dirs[DicomQueryLevel.SERIES] = dirs[DicomQueryLevel.STUDY] / series_dir_name
        return dirs

    @staticmethod
    def build_working_dirs_from_series(series: SeriesRead) -> dict[DicomQueryLevel, Path]:
        """Build working-directory map from a ``SeriesRead``.

        Args:
            series: Fully-loaded series (study, patient relations).

        Returns:
            Dict mapping each available level to its ``Path``.
        """
        base = settings.storage_path
        patient = series.study.patient
        patient_dir_name = patient.anon_id if patient.anon_id is not None else patient.id
        study_dir_name: str = series.study.anon_uid or series.study.study_uid
        series_dir_name: str = series.anon_uid or series.series_uid or ""
        dirs: dict[DicomQueryLevel, Path] = {
            DicomQueryLevel.PATIENT: Path(base) / patient_dir_name,
        }
        dirs[DicomQueryLevel.STUDY] = dirs[DicomQueryLevel.PATIENT] / study_dir_name
        dirs[DicomQueryLevel.SERIES] = dirs[DicomQueryLevel.STUDY] / series_dir_name
        return dirs

    @staticmethod
    def build_working_dirs_from_study(study: StudyRead) -> dict[DicomQueryLevel, Path]:
        """Build working-directory map from a ``StudyRead``.

        Args:
            study: Fully-loaded study (patient relation).

        Returns:
            Dict mapping available levels to their ``Path``.
        """
        base = settings.storage_path
        patient = study.patient
        patient_dir_name = patient.anon_id if patient.anon_id is not None else patient.id
        study_dir_name: str = study.anon_uid or study.study_uid
        patient_path = Path(base) / patient_dir_name
        dirs: dict[DicomQueryLevel, Path] = {
            DicomQueryLevel.PATIENT: patient_path,
            DicomQueryLevel.STUDY: patient_path / study_dir_name,
        }
        return dirs

    @staticmethod
    def build_fields(record: RecordRead) -> dict[str, Any]:
        """Extract placeholder values from a ``RecordRead``.

        Args:
            record: Fully-loaded record.

        Returns:
            Flat dict suitable for ``_resolve_pattern_from_dict``.
        """
        fields: dict[str, Any] = {
            "id": record.id,
            "user_id": record.user_id,
            "patient_id": record.patient_id,
            "study_uid": record.study_uid,
            "series_uid": record.series_uid,
            "record_type": {"name": record.record_type.name},
            "data": record.data or {},
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

    def _lookup(self, file_def: FileDefinitionRead | str) -> FileDefinitionRead:
        """Resolve a file definition by name or pass-through.

        Args:
            file_def: ``FileDefinitionRead`` instance or its ``name``.

        Returns:
            The resolved ``FileDefinitionRead``.

        Raises:
            KeyError: If string name is not in the registry.
        """
        if isinstance(file_def, str):
            return self._registry[file_def]
        return file_def

    def resolve(self, file_def: FileDefinitionRead | str, **overrides: Any) -> Path:
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
        filename = _resolve_pattern_from_dict(fd.pattern, merged)
        path = working_dir / filename
        if fd.name not in self._accessed_files:
            self._accessed_files[fd.name] = path
        return path

    def exists(self, file_def: FileDefinitionRead | str, **overrides: Any) -> bool:
        """Check whether a resolved file exists on disk.

        Args:
            file_def: ``FileDefinitionRead`` or its ``name``.
            **overrides: Extra placeholder values.

        Returns:
            ``True`` if the file exists.
        """
        return self.resolve(file_def, **overrides).is_file()

    def glob(self, file_def: FileDefinitionRead | str) -> list[Path]:
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
        from src.utils.file_checksums import compute_file_checksum

        checksums: dict[str, str | None] = {}
        for fd in self._registry.values():
            try:
                level = fd.level or self._record_type_level
                working_dir = self._working_dirs[level]
                merged = dict(self._fields)
                filename = _resolve_pattern_from_dict(fd.pattern, merged)
                path = working_dir / filename
                checksums[fd.name] = await compute_file_checksum(path)
            except (KeyError, ValueError):
                checksums[fd.name] = None
        return checksums


class RecordQuery:
    """Async record lookup via the Clarinet HTTP API.

    Args:
        client: Authenticated ``ClarinetClient``.
        files: ``FileResolver`` for the current task context.
    """

    def __init__(self, client: ClarinetClient, files: FileResolver) -> None:
        self._client = client
        self._files = files

    async def find(
        self,
        type_name: str,
        *,
        series_uid: str | None = None,
        study_uid: str | None = None,
        patient_id: str | None = None,
        status: Any | None = None,
        limit: int = 100,
    ) -> list[RecordRead]:
        """Find records by criteria.

        Args:
            type_name: Record type name filter.
            series_uid: Optional series UID filter.
            study_uid: Optional study UID filter.
            patient_id: Optional patient ID filter.
            status: Optional ``RecordStatus`` filter.
            limit: Max results (default 100).

        Returns:
            List of matching ``RecordRead`` objects.
        """
        return await self._client.find_records_advanced(
            record_type_name=type_name,
            series_uid=series_uid,
            study_uid=study_uid,
            patient_id=patient_id,
            record_status=status,
            limit=limit,
        )

    async def file_path(
        self,
        type_name: str,
        *,
        file: str,
        series_uid: str | None = None,
        study_uid: str | None = None,
        patient_id: str | None = None,
        status: Any | None = None,
    ) -> Path:
        """Find a record and resolve a file path from it.

        Sugar method: finds a single record, then resolves the named file
        to an absolute ``Path``.

        Args:
            type_name: Record type name to search for.
            file: File definition name to resolve.
            series_uid: Optional series UID filter.
            study_uid: Optional study UID filter.
            patient_id: Optional patient ID filter.
            status: Optional ``RecordStatus`` filter.

        Returns:
            Absolute path to the resolved file.

        Raises:
            PipelineStepError: If no record is found.
        """
        records = await self.find(
            type_name,
            series_uid=series_uid,
            study_uid=study_uid,
            patient_id=patient_id,
            status=status,
            limit=1,
        )
        if not records:
            raise PipelineStepError(
                type_name,
                f"No record found (series_uid={series_uid}, study_uid={study_uid}, "
                f"patient_id={patient_id})",
            )
        record = records[0]

        # Check file_links first — if a link with matching name exists, use its filename
        if record.file_links:
            for link in record.file_links:
                if link.name == file:
                    working_dirs = FileResolver.build_working_dirs(record)
                    file_registry = record.record_type.file_registry or []
                    fd_map = {fd.name: fd for fd in file_registry}
                    fd = fd_map.get(file)
                    level = (fd.level if fd else None) or record.record_type.level
                    return working_dirs[level] / link.filename

        # Fallback to pattern resolution
        working_dirs = FileResolver.build_working_dirs(record)
        fields = FileResolver.build_fields(record)
        file_registry = record.record_type.file_registry or []
        fd_map = {fd.name: fd for fd in file_registry}
        if file not in fd_map:
            raise PipelineStepError(
                type_name,
                f"File definition '{file}' not found in record type '{record.record_type.name}'",
            )
        fd = fd_map[file]
        level = fd.level or record.record_type.level
        filename = _resolve_pattern_from_dict(fd.pattern, fields)
        return working_dirs[level] / filename


@dataclass
class TaskContext:
    """Container for pipeline task context.

    Attributes:
        files: Sync file path resolver.
        records: Async record query helper.
        client: Authenticated HTTP client.
        msg: The parsed pipeline message.
    """

    files: FileResolver
    records: RecordQuery
    client: ClarinetClient
    msg: PipelineMessage


async def build_task_context(msg: PipelineMessage, client: ClarinetClient) -> TaskContext:
    """Build a ``TaskContext`` from a pipeline message.

    Makes at most one HTTP call to fetch the primary entity, then builds
    all context objects from it.

    Fallback chain:
    1. ``msg.record_id`` → ``client.get_record()``
    2. ``msg.series_uid`` → ``client.get_series()``
    3. ``msg.study_uid``  → ``client.get_study()``
    4. Nothing → minimal empty context

    Args:
        msg: The pipeline message.
        client: Authenticated ``ClarinetClient``.

    Returns:
        Fully initialised ``TaskContext``.
    """
    working_dirs: dict[DicomQueryLevel, Path] = {}
    file_registry: list[FileDefinitionRead] = []
    fields: dict[str, Any] = {}
    record_type_level = DicomQueryLevel.SERIES

    if msg.record_id is not None:
        record = await client.get_record(msg.record_id)
        working_dirs = FileResolver.build_working_dirs(record)
        fields = FileResolver.build_fields(record)
        file_registry = record.record_type.file_registry or []
        record_type_level = record.record_type.level
        logger.debug(f"TaskContext built from record_id={msg.record_id}")

    elif msg.series_uid is not None:
        series = await client.get_series(msg.series_uid)
        working_dirs = FileResolver.build_working_dirs_from_series(series)
        logger.debug(f"TaskContext built from series_uid={msg.series_uid}")

    elif msg.study_uid:
        study = await client.get_study(msg.study_uid)
        working_dirs = FileResolver.build_working_dirs_from_study(study)
        logger.debug(f"TaskContext built from study_uid={msg.study_uid}")

    else:
        logger.debug("TaskContext built with minimal empty context")

    files = FileResolver(
        working_dirs=working_dirs,
        record_type_level=record_type_level,
        file_registry=file_registry,
        fields=fields,
    )
    records = RecordQuery(client=client, files=files)
    return TaskContext(files=files, records=records, client=client, msg=msg)
