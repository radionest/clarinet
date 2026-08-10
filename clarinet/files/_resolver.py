"""Working-directory builders for ``Files`` (formerly ``FileResolver``).

Module-level functions that compute per-DICOM-level working directories
from a ``RecordRead``, ``SeriesRead``, ``StudyRead``, or ``PatientRead``.
They delegate to :func:`clarinet.files._storage.render_all_levels` — the
single rendering point shared with the writer and all other readers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.exceptions.domain import UnsafePathError
from clarinet.files._storage import render_all_levels
from clarinet.models.base import DicomQueryLevel
from clarinet.settings import settings

if TYPE_CHECKING:
    from clarinet.models.patient import PatientRead
    from clarinet.models.record import RecordRead
    from clarinet.models.study import SeriesRead, StudyRead


@dataclass(frozen=True)
class _StudyLazySnapshot:
    """Lightweight stub for ``build_context`` when ``record.study`` is lazy.

    Carries only the fields available from the record-level snapshot
    columns (``study_uid``, ``study_anon_uid``). Template placeholders
    that reference ``{study_date}`` or ``{study_modalities}`` render as
    ``"unknown"``; eager-load ``record.study`` if you need them.
    """

    study_uid: str
    anon_uid: str | None
    date: object | None = None
    modalities_in_study: str | None = None


@dataclass(frozen=True)
class _SeriesLazySnapshot:
    """Lightweight stub for ``build_context`` when ``record.series`` is lazy."""

    series_uid: str
    anon_uid: str | None
    modality: str | None = None
    series_number: int | None = None


def _resolve_storage_base(clarinet_storage_path: str | None) -> Path:
    """Validate the per-record storage-path override, or fall back to the default.

    Purely lexical (``os.path.normpath``, no filesystem access) — this is the
    resolution choke point, so the check here covers every consumer
    (``Files``, the Slicer context builder) and every row regardless of how
    it was created, including rows already in a deployed database.

    ``join_within``'s containment proof is purely lexical too, so it is only
    as trustworthy as its own ``base`` argument: a relative or
    ``..``-carrying base such as ``"../../../../etc"`` satisfies
    ``Path.is_relative_to(base)`` for every filename joined onto it, making
    the proof vacuous. Rejecting a malformed base here is what makes every
    other guard added in this change mean something.

    Deliberately does NOT require containment under ``settings.storage_path``
    — ``clarinet_storage_path`` is a real, admin-only per-record storage-root
    override (enforced at write time in ``api/routers/record.py``), not a
    subdirectory selector. Multiple currently-passing tests
    (``tests/integration/test_record_working_folder.py``,
    ``tests/test_file_repository.py``, ``tests/test_pipeline_context.py``)
    pin an intentionally disjoint root as correct behavior.
    """
    if not clarinet_storage_path:
        return Path(settings.storage_path)
    normalized = os.path.normpath(clarinet_storage_path)
    if not os.path.isabs(clarinet_storage_path) or normalized != clarinet_storage_path:
        raise UnsafePathError(
            "clarinet_storage_path must be an absolute, normalized path",
            value=clarinet_storage_path,
        )
    return Path(clarinet_storage_path)


def build_working_dirs(
    record: RecordRead,
    *,
    fallback_to_unanonymized: bool = False,
) -> dict[DicomQueryLevel, Path]:
    """Build working-directory map from a ``RecordRead``.

    Renders ``settings.disk_path_template`` against the record's
    patient/study/series for all three DICOM levels so that
    cross-level file access is possible. Delegates to
    :func:`clarinet.files._storage.render_all_levels`
    — the single rendering point shared with the writer and other
    readers, so a custom ``disk_path_template`` yields one path
    across the whole stack.

    Lazy-load adapter: when ``record.study`` / ``record.series`` is
    ``None`` (relationship not eager-loaded) but the raw UID column
    is present, a lightweight stub is built from the record-level
    snapshot columns (``study_anon_uid``, ``series_anon_uid``). The
    stub only carries the identifier — template placeholders that
    reference ``{study_date}`` / ``{study_modalities}`` /
    ``{series_modality}`` will render as ``"unknown"`` (eager-load
    the relation if you need them).

    Args:
        record: Record with patient eagerly loaded; study / series
            may be eager or lazy.
        fallback_to_unanonymized: If ``False`` (default — backend safe
            mode), missing anonymized identifiers raise
            ``AnonPathError`` instead of silently rendering a path
            against raw UIDs. UX callers may pass ``True`` to keep the
            legacy fallback.

    Returns:
        Dict mapping each available level to its ``Path``.
    """
    base = _resolve_storage_base(record.clarinet_storage_path)

    study = record.study
    if study is None and record.study_uid is not None:
        study = _StudyLazySnapshot(  # type: ignore[assignment]
            study_uid=record.study_uid,
            anon_uid=record.study_anon_uid,
        )
    series = record.series
    if series is None and record.series_uid is not None:
        series = _SeriesLazySnapshot(  # type: ignore[assignment]
            series_uid=record.series_uid,
            anon_uid=record.series_anon_uid,
        )

    return render_all_levels(
        patient=record.patient,
        study=study,
        series=series,
        storage_path=base,
        fallback_to_unanonymized=fallback_to_unanonymized,
    )


def build_working_dirs_from_series(
    series: SeriesRead,
    *,
    fallback_to_unanonymized: bool = False,
) -> dict[DicomQueryLevel, Path]:
    """Build working-directory map from a ``SeriesRead``.

    Delegates to
    :func:`clarinet.files._storage.render_all_levels`
    (single rendering point).

    Args:
        series: Fully-loaded series (study, patient relations).
        fallback_to_unanonymized: see :func:`build_working_dirs`.

    Returns:
        Dict mapping each available level to its ``Path``.
    """
    return render_all_levels(
        patient=series.study.patient,
        study=series.study,
        series=series,
        storage_path=Path(settings.storage_path),
        fallback_to_unanonymized=fallback_to_unanonymized,
    )


def build_working_dirs_from_study(
    study: StudyRead,
    *,
    fallback_to_unanonymized: bool = False,
) -> dict[DicomQueryLevel, Path]:
    """Build working-directory map from a ``StudyRead``.

    Delegates to
    :func:`clarinet.files._storage.render_all_levels`
    (single rendering point).

    Args:
        study: Fully-loaded study (patient relation).
        fallback_to_unanonymized: see :func:`build_working_dirs`.

    Returns:
        Dict mapping available levels to their ``Path``.
    """
    return render_all_levels(
        patient=study.patient,
        study=study,
        series=None,
        storage_path=Path(settings.storage_path),
        fallback_to_unanonymized=fallback_to_unanonymized,
    )


def build_working_dirs_from_patient(
    patient: PatientRead,
    *,
    fallback_to_unanonymized: bool = False,
) -> dict[DicomQueryLevel, Path]:
    """Build working-directory map from a ``PatientRead``.

    Delegates to
    :func:`clarinet.files._storage.render_all_levels`
    (single rendering point). Only the PATIENT-level directory is
    returned, since no study anchor is available.

    Args:
        patient: Fully-loaded patient.
        fallback_to_unanonymized: see :func:`build_working_dirs`.

    Returns:
        Dict mapping ``DicomQueryLevel.PATIENT`` to its ``Path``.
    """
    return render_all_levels(
        patient=patient,
        study=None,
        series=None,
        storage_path=Path(settings.storage_path),
        fallback_to_unanonymized=fallback_to_unanonymized,
    )
