# clarinet/files/facade.py
"""Single public facade for on-disk path resolution and file access."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from clarinet.files import _patterns, _resolver, _template
from clarinet.models.base import DicomQueryLevel

if TYPE_CHECKING:
    from clarinet.config.primitives import FileDef
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.patient import PatientRead
    from clarinet.models.record import RecordRead
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
    def render_template(pattern: str, fields: dict[str, Any], *, strict: bool = False) -> str:
        mode = _template.RenderMode.STRICT if strict else _template.RenderMode.LENIENT
        return _template.render_template(pattern, fields, mode=mode)
