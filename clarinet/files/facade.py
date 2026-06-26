# clarinet/files/facade.py
"""Single public facade for on-disk path resolution and file access."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import AnonPathError
from clarinet.files import _checksums, _fs, _patterns, _resolver, _storage, _template
from clarinet.models.base import DicomQueryLevel
from clarinet.settings import settings

if TYPE_CHECKING:
    from clarinet.config.primitives import FileDef
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.patient import PatientRead
    from clarinet.models.record import RecordBase, RecordRead
    from clarinet.models.study import SeriesRead, StudyRead

    type FileDefArg = FileDefinitionRead | FileDef | str
    type Entity = RecordRead | SeriesRead | StudyRead | PatientRead

__all__ = ["Files"]


class Files:
    """The sole public entry point for path resolution and file access.

    Construct from any of ``RecordRead`` / ``SeriesRead`` / ``StudyRead`` /
    ``PatientRead``. Strict by default (``AnonPathError`` for not-yet-anonymized
    records when the template references ``{anon_*}``); pass ``fallback=True``
    for UX call sites. ``parent`` supplies fallback values for pattern fields
    (``{user_id}``, inverted ``{origin_type}``, …).
    """

    def __init__(
        self,
        entity: Entity,
        *,
        parent: RecordRead | None = None,
        fallback: bool = False,
    ) -> None:
        from clarinet.models.patient import PatientRead
        from clarinet.models.record import RecordRead
        from clarinet.models.study import SeriesRead, StudyRead

        self._parent = parent
        self._accessed: dict[str, Path] = {}

        if isinstance(entity, RecordRead):
            self._dirs = _resolver.build_working_dirs(entity, fallback_to_unanonymized=fallback)
            self._level = DicomQueryLevel(entity.record_type.level)
            self._registry = {fd.name: fd for fd in (entity.record_type.file_registry or [])}
            self._fields = _patterns.fields_from(entity, parent)
        elif isinstance(entity, SeriesRead):
            self._dirs = _resolver.build_working_dirs_from_series(
                entity, fallback_to_unanonymized=fallback
            )
            self._level = DicomQueryLevel.SERIES
            self._registry = {}
            self._fields = {}
        elif isinstance(entity, StudyRead):
            self._dirs = _resolver.build_working_dirs_from_study(
                entity, fallback_to_unanonymized=fallback
            )
            self._level = DicomQueryLevel.STUDY
            self._registry = {}
            self._fields = {}
        elif isinstance(entity, PatientRead):
            self._dirs = _resolver.build_working_dirs_from_patient(
                entity, fallback_to_unanonymized=fallback
            )
            self._level = DicomQueryLevel.PATIENT
            self._registry = {}
            self._fields = {}
        else:
            raise TypeError(
                "Files accepts RecordRead/SeriesRead/StudyRead/PatientRead, "
                f"got {type(entity).__name__}"
            )

    @classmethod
    def empty(cls) -> Files:
        """Degenerate resolver for ``build_task_context``'s no-entity branch."""
        self = cls.__new__(cls)
        self._dirs = {}
        self._level = DicomQueryLevel.SERIES
        self._registry = {}
        self._fields = {}
        self._parent = None
        self._accessed = {}
        return self

    @classmethod
    def for_reader(cls, record: RecordRead, *, parent: RecordRead | None = None) -> Files:
        """Strict first; on ``AnonPathError`` rebuild with raw-UID fallback. Optional
        ``parent`` supplies pattern-field fallback (e.g. ``{user_id}``)."""
        try:
            return cls(record, parent=parent)
        except AnonPathError:
            return cls(record, parent=parent, fallback=True)

    @classmethod
    def working_dirs(
        cls,
        *,
        patient: Any,
        study: Any,
        series: Any,
        storage_path: Path | None = None,
        template: str | None = None,
        fallback: bool = False,
        anon_patient_id: str | None = None,
        anon_study_uid: str | None = None,
        anon_series_uid: str | None = None,
    ) -> dict[DicomQueryLevel, Path]:
        """Stateless all-levels renderer from explicit entities (caller indexes by level)."""
        return _storage.render_all_levels(
            patient=patient,
            study=study,
            series=series,
            storage_path=storage_path or Path(settings.storage_path),
            template=template,
            fallback_to_unanonymized=fallback,
            anon_patient_id=anon_patient_id,
            anon_study_uid=anon_study_uid,
            anon_series_uid=anon_series_uid,
        )

    # ── working dirs ──
    def dir(self, level: DicomQueryLevel | None = None) -> Path:
        return self._dirs[level or self._level]

    def dirs(self) -> dict[DicomQueryLevel, Path]:
        return dict(self._dirs)

    # ── internal ──
    def _lookup(self, file_def: FileDefArg) -> Any:
        if isinstance(file_def, str):
            return self._registry[file_def]
        return file_def

    def resolve(self, file_def: FileDefArg, **overrides: Any) -> Path:
        fd = self._lookup(file_def)
        working_dir = self._dirs[fd.level or self._level]
        filename = _template.render_template(
            fd.pattern, {**self._fields, **overrides}, mode=_template.RenderMode.LENIENT
        )
        path = working_dir / filename
        self._accessed.setdefault(fd.name, path)
        return path

    def exists(self, file_def: FileDefArg, **overrides: Any) -> bool:
        return self.resolve(file_def, **overrides).is_file()

    def glob(self, file_def: FileDefArg) -> list[Path]:
        fd = self._lookup(file_def)
        working_dir = self._dirs[fd.level or self._level]
        paths = _patterns.glob_file_paths(fd, working_dir)
        self._accessed.setdefault(fd.name, paths[0] if paths else working_dir)
        return paths

    @property
    def accessed(self) -> dict[str, Path]:
        return dict(self._accessed)

    def render(self, pattern: str) -> str:
        return _template.render_template(pattern, self._fields, mode=_template.RenderMode.LENIENT)

    @staticmethod
    def render_for(record: RecordBase, pattern: str, *, parent: RecordBase | None = None) -> str:
        """Render *pattern* for a record WITHOUT building working dirs or hitting
        the entity-type gate. Use for pattern-only resolution that must tolerate
        not-yet-anonymized records (no ``AnonPathError``) and duck-typed records.
        Equivalent to the old ``resolve_pattern(pattern, record, parent)``."""
        return _template.render_template(
            pattern, _patterns.fields_from(record, parent), mode=_template.RenderMode.LENIENT  # type: ignore[arg-type]
        )

    @staticmethod
    def render_template(pattern: str, fields: dict[str, Any], *, strict: bool = False) -> str:
        mode = _template.RenderMode.STRICT if strict else _template.RenderMode.LENIENT
        return _template.render_template(pattern, fields, mode=mode)

    async def checksums(self, defs: list[FileDefinitionRead] | None = None) -> dict[str, str]:
        """SHA256 of registered files, keyed by name (singular) / ``name:filename``
        (collections). Resolves each def at its own ``level``; missing files are
        omitted. Replaces both ``snapshot_checksums`` and ``compute_checksums``."""
        targets = defs if defs is not None else list(self._registry.values())
        out: dict[str, str] = {}
        for fd in targets:
            working_dir = self._dirs.get(fd.level or self._level)
            if working_dir is None:
                continue
            if fd.multiple:
                for p in await _fs.run_in_fs_thread(_patterns.glob_file_paths, fd, working_dir):
                    c = await _checksums.compute_file_checksum(p)
                    if c is not None:
                        out[f"{fd.name}:{p.name}"] = c
            else:
                filename = _template.render_template(
                    fd.pattern, self._fields, mode=_template.RenderMode.LENIENT
                )
                c = await _checksums.compute_file_checksum(working_dir / filename)
                if c is not None:
                    out[fd.name] = c
        return out

    @staticmethod
    async def checksum(path: Path) -> str | None:
        return await _checksums.compute_file_checksum(path)

    @staticmethod
    def checksums_changed(old: dict[str, str] | None, new: dict[str, str]) -> set[str]:
        return _checksums.checksums_changed(old, new)

    @staticmethod
    def origin_type(record: RecordRead, parent: RecordRead | None = None) -> str:
        return _patterns.resolve_origin_type(record, parent)

    @staticmethod
    def display_anon_id(study_uid: str | None, study_anon_uid: str | None) -> str | None:
        return _storage.compute_display_anon_id(study_uid, study_anon_uid)

    @staticmethod
    def per_study_patient_id(study_uid: str) -> str:
        """Deterministic per-study anonymized PatientID hash (per-study anon mode)."""
        return _storage.per_study_patient_id(study_uid)

    @staticmethod
    def validate_template(template: str) -> str:
        return _template.validate_template(template)

    @staticmethod
    async def in_thread(fn: Any, *args: Any) -> Any:
        return await _fs.run_in_fs_thread(fn, *args)

    @staticmethod
    def shutdown_io() -> None:
        _fs.shutdown_fs_executor()
