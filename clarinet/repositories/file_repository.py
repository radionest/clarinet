"""File path resolution authority — thin wrapper over ``FileResolver``.

Sole entry point for backend code to resolve on-disk paths for records,
series, studies, patients, and file definitions. Replaces the
``working_folder`` field and ``_format_path`` helpers that used to live
on ``RecordRead`` / ``SeriesRead``.

Stateless utility (no DB session). Accepts any of ``RecordRead``,
``SeriesRead``, ``StudyRead``, ``PatientRead`` — relationships must be
eager-loaded by the caller (``selectinload``).

The instance level (returned by ``working_dir``) is fixed per type:

- ``RecordRead``  → ``DicomQueryLevel(record.record_type.level)``
- ``SeriesRead``  → ``SERIES``
- ``StudyRead``   → ``STUDY``
- ``PatientRead`` → ``PATIENT``

``resolve_file`` requires a ``RecordRead`` (the file registry lives on
``record_type``). For other types it raises ``TypeError``. Slicer-arg
rendering lives in ``clarinet.services.slicer.args.render_slicer_args``
— the Slicer concern is intentionally kept out of the path repository
(see file-repo roadmap §5).

Strict by default: missing anonymized identifiers raise ``AnonPathError``.
After Phase 1.5 (pull-based context), templates without ``{anon_*}``
placeholders never invoke anon resolution, so no fallback flag is needed
at the repository level. UX routers should catch ``AnonPathError`` on
their side when serving non-anonymized records. Reader-side backend
services that must keep working through the legacy / pre-anon flow use
``FileRepository.resolve_with_fallback`` instead of catching the
exception themselves.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import AnonPathError
from clarinet.models.base import DicomQueryLevel
from clarinet.services.common.file_resolver import FileResolver

if TYPE_CHECKING:
    from clarinet.config.primitives import FileDef
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.patient import PatientRead
    from clarinet.models.record import RecordRead
    from clarinet.models.study import SeriesRead, StudyRead


__all__ = ["FileRepository"]


class FileRepository:
    """Sole authority for file path resolution.

    Constructible from any of ``RecordRead``, ``SeriesRead``, ``StudyRead``,
    ``PatientRead`` — see module docstring for level semantics.
    """

    def __init__(
        self,
        record: "RecordRead | SeriesRead | StudyRead | PatientRead",
    ) -> None:
        from clarinet.models.patient import PatientRead
        from clarinet.models.record import RecordRead
        from clarinet.models.study import SeriesRead, StudyRead

        self._record = record
        if isinstance(record, RecordRead):
            self._working_dirs = FileResolver.build_working_dirs(record)
            self._level = DicomQueryLevel(record.record_type.level)
        elif isinstance(record, SeriesRead):
            self._working_dirs = FileResolver.build_working_dirs_from_series(record)
            self._level = DicomQueryLevel.SERIES
        elif isinstance(record, StudyRead):
            self._working_dirs = FileResolver.build_working_dirs_from_study(record)
            self._level = DicomQueryLevel.STUDY
        elif isinstance(record, PatientRead):
            self._working_dirs = FileResolver.build_working_dirs_from_patient(record)
            self._level = DicomQueryLevel.PATIENT
        else:
            raise TypeError(
                "FileRepository accepts RecordRead/SeriesRead/StudyRead/PatientRead, "
                f"got {type(record).__name__}"
            )

    # ── working dirs ──────────────────────────────────────────────────

    @property
    def working_dir(self) -> Path:
        """Path at the record's DICOM level (replaces the removed
        ``working_folder`` model field)."""
        return self._working_dirs[self._level]

    def working_dirs_all(self) -> dict[DicomQueryLevel, Path]:
        """Copy of all available DICOM levels → working dirs."""
        return dict(self._working_dirs)

    # ── file resolution (RecordRead-only) ─────────────────────────────

    def resolve_file(
        self,
        file_def: "FileDefinitionRead | FileDef | str",
        **overrides: Any,
    ) -> Path:
        """Resolve a single file definition to an absolute path.

        Requires a ``RecordRead`` (file registry lives on ``record_type``).
        """
        from clarinet.models.record import RecordRead

        if not isinstance(self._record, RecordRead):
            raise TypeError(f"resolve_file requires RecordRead, got {type(self._record).__name__}")
        resolver = FileResolver(
            working_dirs=self._working_dirs,
            record_type_level=self._level,
            file_registry=self._record.record_type.file_registry or [],
            fields=FileResolver.build_fields(self._record),
        )
        return resolver.resolve(file_def, **overrides)

    # ── reader-side fallback ──────────────────────────────────────────

    @staticmethod
    def resolve_with_fallback(
        record: "RecordRead",
    ) -> tuple[dict[DicomQueryLevel, Path], Path]:
        """Resolve working dirs + record-level dir, with raw-UID fallback.

        Strict ``FileRepository(record)`` first; on ``AnonPathError`` falls
        back to ``FileResolver.build_working_dirs(..., fallback_to_unanonymized=True)``.

        For reader-side backend services (cascade delete, output-file
        lookup, checksum compute, input file validation) that must keep
        working through the legacy / pre-anon flow. Writers stay strict
        on bare ``FileRepository(record)`` so the asymmetric-anonymization
        race surfaces: without strict mode, a writer racing ahead of the
        anonymization step would silently render the path against raw
        UIDs and land its file in a directory that the reader (using the
        anonymized template) would never find. Detailed discussion:
        ``clarinet/utils/anon_resolve.py``.

        Returns ``(working_dirs, default_dir)`` so callers that need
        cross-level dirs (eg. ``file_def.level`` lookups) and the
        record-level dir get both in one call.
        """
        try:
            repo = FileRepository(record)
            return repo.working_dirs_all(), repo.working_dir
        except AnonPathError:
            working_dirs = FileResolver.build_working_dirs(record, fallback_to_unanonymized=True)
            return working_dirs, working_dirs[record.record_type.level]
