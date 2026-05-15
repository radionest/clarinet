"""File path resolution authority — thin wrapper over ``FileResolver``.

Sole entry point for backend code to resolve on-disk paths for records,
series, studies, patients, file definitions, and Slicer args. Replaces
ad-hoc usage of ``RecordRead.working_folder`` / ``SeriesRead.working_folder``
and the ``_format_path``/``_format_slicer_kwargs`` helpers on the models.

Stateless utility (no DB session). Accepts any of ``RecordRead``,
``SeriesRead``, ``StudyRead``, ``PatientRead`` — relationships must be
eager-loaded by the caller (``selectinload``).

The instance level (returned by ``working_dir``) is fixed per type:

- ``RecordRead``  → ``DicomQueryLevel(record.record_type.level)``
- ``SeriesRead``  → ``SERIES``
- ``StudyRead``   → ``STUDY``
- ``PatientRead`` → ``PATIENT``

``resolve_file`` and ``slicer_args`` require a ``RecordRead`` (the file
registry and Slicer kwargs live on ``record_type``). For other types they
raise ``TypeError`` with a clear message.

Strict by default for path resolution (``working_dir``, ``working_dirs_all``,
``resolve_file``): missing anonymized identifiers raise ``AnonPathError``.
After Phase 1.5 (pull-based context), templates without ``{anon_*}``
placeholders never invoke anon resolution, so no fallback flag is needed
at the repository level. UX routers should catch ``AnonPathError`` on
their side when serving non-anonymized records, and reader-side backend
services that must keep working through the legacy / pre-anon flow may
do the same (cf. ``RecordService._resolve_working_dirs_with_fallback``).

``slicer_args`` is also strict: it reads ``working_dir`` from this
instance, which is computed at ``__init__`` in strict mode. A
non-anonymized record cannot construct a ``FileRepository`` at all —
``__init__`` raises ``AnonPathError`` before ``slicer_args`` can be
called. This differs from the legacy ``RecordRead.slicer_*_args_formatted``
computed fields, which use ``fallback_to_unanonymized=True``. The fallback
is intentionally not preserved at the repository layer (see file-repo
roadmap §4); UX routers (roadmap Phase 4) must catch ``AnonPathError``
and serve ``null`` instead of degrading silently.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from clarinet.models.base import DicomQueryLevel
from clarinet.services.common.file_resolver import FileResolver

if TYPE_CHECKING:
    from clarinet.config.primitives import FileDef
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.patient import PatientRead
    from clarinet.models.record import RecordRead
    from clarinet.models.study import SeriesRead, StudyRead
    from clarinet.types import SlicerArgs


__all__ = ["FileRepository"]


class FileRepository:
    """Sole authority for file path resolution.

    Replaces:

    - ``RecordRead.working_folder``, ``SeriesRead.working_folder``
    - ``RecordRead._get_working_folder``, ``_format_path``, ``_format_path_strict``
    - ``RecordRead._format_slicer_kwargs`` and the three
      ``slicer_*_args_formatted`` computed fields.

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
        """Path at the record's DICOM level (replaces ``.working_folder``)."""
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

    # ── slicer args (RecordRead-only) ─────────────────────────────────

    def slicer_args(self, *, validator: bool = False) -> "SlicerArgs | None":
        """Formatted Slicer script arguments.

        ``validator=False`` (default) → from ``record_type.slicer_script_args``.
        ``validator=True``            → from ``record_type.slicer_result_validator_args``.

        Returns ``None`` when the relevant args field is ``None`` on the
        record type — matches legacy ``RecordRead.slicer_*_args_formatted``.

        Delegates to ``RecordRead._format_slicer_kwargs`` to guarantee
        byte-for-byte equality with the legacy computed field. The
        ``working_folder`` placeholder is injected from ``self.working_dir``.

        .. todo:: Move ``_format_slicer_kwargs`` logic into this module (or
            ``FileResolver``) before file-repo roadmap Phase 3 — that phase
            removes ``RecordRead._format_slicer_kwargs``, which Phase 5
            ``build_slicer_context`` relies on through this method.

        Requires a ``RecordRead``.
        """
        from clarinet.models.record import RecordRead

        if not isinstance(self._record, RecordRead):
            raise TypeError(f"slicer_args requires RecordRead, got {type(self._record).__name__}")
        source = (
            self._record.record_type.slicer_result_validator_args
            if validator
            else self._record.record_type.slicer_script_args
        )
        if source is None:
            return None
        extra = {"working_folder": str(self.working_dir)}
        return self._record._format_slicer_kwargs(source, extra)
