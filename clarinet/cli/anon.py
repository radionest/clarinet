"""CLI helpers for anonymization-related operations.

``clarinet anon migrate-paths`` relocates anonymized files when
``settings.disk_path_template`` changes.

Default mode moves only ``series_dir/dcm_anon/`` for each anonymized Series.

With ``--include-working-folder`` it migrates the entire working_folder
tree in bottom-up order (SERIES → STUDY → PATIENT):

* SERIES pass: move full ``series_dir`` (pipeline outputs + dcm_anon).
* STUDY pass: for each STUDY-level Record, merge remaining children of
  ``old_study_dir`` into ``new_study_dir`` (which already contains
  series_dirs from the SERIES pass).
* PATIENT pass: same, for PATIENT-level Records.

The DB is not touched — paths derive from Study/Patient/Series + RecordType.level.

Supported placeholders for the ``--from``/``--to`` templates are listed in
``SUPPORTED_PLACEHOLDERS`` in :mod:`clarinet.utils.path_template`.
"""

import argparse
import asyncio
import contextlib
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.models.base import DicomQueryLevel
from clarinet.models.patient import Patient
from clarinet.models.record import Record
from clarinet.models.record_type import RecordType
from clarinet.models.study import Series, Study
from clarinet.services.dicom.anon_path import (
    AnonPathError,
    build_context,
    render_working_folder,
    validate_template,
)
from clarinet.settings import settings
from clarinet.utils.db_manager import db_manager
from clarinet.utils.logger import logger

MoveOutcome = Literal["moved", "same", "missing", "collision", "failed"]


def _is_deep_empty(path: Path) -> bool:
    """True if ``path`` is a directory whose subtree holds no files/symlinks.

    Used by :func:`_merge_dir` to spot SERIES-pass leftovers: after the
    SERIES pass renames ``old_anon_study/old_anon_series/`` out from under
    ``old_anon_patient/``, the bare ``old_anon_study/`` directory becomes
    deep-empty. We must not propagate it into the new layout — otherwise
    every new patient_dir gets a stray ``<old_anon_study_uid>/`` subdir.
    """
    if not path.is_dir():
        return False
    return not any(
        descendant.is_file() or descendant.is_symlink() for descendant in path.rglob("*")
    )


def _remove_deep_empty(path: Path) -> None:
    """Recursively ``rmdir`` an empty subtree rooted at ``path``. Best-effort."""
    if not path.is_dir():
        return
    for child in list(path.iterdir()):
        if child.is_dir():
            _remove_deep_empty(child)
    with contextlib.suppress(OSError):
        path.rmdir()


def _move_dir_atomic(old: Path, new: Path, dry_run: bool, *, label: str) -> MoveOutcome:
    """Rename ``old`` to ``new``. Skip whole entry on collision.

    Used at SERIES level (leaf dir). Two caveats:

    1. **TOCTOU race**: there is a window between ``new.exists()`` and the
       ``shutil.move`` call. Safe for the single-operator CLI use case
       this command serves; not safe for concurrent invocations.
    2. **Cross-filesystem**: ``shutil.move`` is atomic only when ``old`` and
       ``new`` share a filesystem; otherwise it degrades to copy+unlink.
       The silent ``shutil.move`` quirk of nesting source inside an
       existing target dir is guarded by the explicit ``new.exists()``
       check — collision is reported with a WARNING.
    """
    if old == new:
        return "same"
    if not old.is_dir():
        logger.debug(f"{label}: source {old} missing; skipping.")
        return "missing"
    if new.exists():
        logger.warning(f"{label}: target {new} already exists; skipping.")
        return "collision"
    if dry_run:
        logger.info(f"[dry-run] {label}: {old} -> {new}")
        return "moved"
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))
    except OSError as exc:
        logger.error(f"{label}: failed to move {old} -> {new}: {exc}")
        return "failed"
    logger.info(f"{label}: moved {old} -> {new}")
    return "moved"


def _merge_dir(old: Path, new: Path, dry_run: bool, *, label: str) -> MoveOutcome:
    """Move ``old`` into ``new``, merging per-child when ``new`` already exists.

    Used at STUDY and PATIENT levels: the new dir is typically created
    earlier in the bottom-up pass (by SERIES mkdir-parents), so the
    entire dir cannot be renamed atomically — we move loose children
    one-by-one. Per-child name collisions are skipped with WARNING.

    Returns the dominant outcome for the level:
      * ``same``       — ``old == new`` or ``old`` is empty
      * ``missing``    — ``old`` doesn't exist on disk
      * ``moved``      — at least one child moved (or whole dir renamed)
      * ``collision``  — children all collided, nothing moved
      * ``failed``     — OSError mid-flight
    """
    if old == new:
        return "same"
    if not old.is_dir():
        logger.debug(f"{label}: source {old} missing; skipping.")
        return "missing"

    if not new.exists():
        # Fast path: no merge needed.
        if dry_run:
            logger.info(f"[dry-run] {label}: {old} -> {new}")
            return "moved"
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
        except OSError as exc:
            logger.error(f"{label}: failed to move {old} -> {new}: {exc}")
            return "failed"
        logger.info(f"{label}: moved {old} -> {new}")
        return "moved"

    children = list(old.iterdir())
    if not children:
        logger.debug(f"{label}: source {old} is empty; skipping.")
        return "same"

    moved_any = False
    collided_any = False
    for child in children:
        dest = new / child.name
        if dest.exists():
            logger.warning(f"{label}: target {dest} exists; skipping {child.name}.")
            collided_any = True
            continue
        if child.is_dir() and _is_deep_empty(child):
            # SERIES-pass leftover: bare directory whose content has already
            # moved. Drop it instead of propagating to the new layout.
            logger.debug(f"{label}: dropping deep-empty {child.name}.")
            if not dry_run:
                _remove_deep_empty(child)
            continue
        if dry_run:
            logger.info(f"[dry-run] {label}: {child} -> {dest}")
            moved_any = True
            continue
        try:
            shutil.move(str(child), str(dest))
        except OSError as exc:
            logger.error(f"{label}: failed to move {child} -> {dest}: {exc}")
            return "failed"
        logger.info(f"{label}: moved {child} -> {dest}")
        moved_any = True

    if not dry_run:
        # Best-effort: drop ``old`` if it's now empty (deep-empty children
        # dropped, real children moved, no surviving collisions).
        with contextlib.suppress(OSError):
            old.rmdir()

    if collided_any and not moved_any:
        return "collision"
    if not moved_any:
        # All children were dropped as deep-empty leftovers; effectively
        # nothing was migrated for this record.
        return "same"
    return "moved"


def _render_old_new_dirs(
    level: DicomQueryLevel,
    *,
    patient: Patient | None,
    study: Study | None,
    series: Series | None,
    from_template: str,
    to_template: str,
    storage_path: Path,
    counters: dict[str, int],
    label: str,
) -> tuple[Path, Path] | None:
    """Render ``(old_dir, new_dir)`` for ``level``.

    On :exc:`AnonPathError`, logs and bumps ``counters['failed']`` then
    returns ``None``. Callers handle ``None`` by ``continue``. Eager-load
    validation for relations is the caller's responsibility — this helper
    only renders.
    """
    try:
        ctx = build_context(patient=patient, study=study, series=series)
        old_dir = render_working_folder(from_template, level, ctx, storage_path)
        new_dir = render_working_folder(to_template, level, ctx, storage_path)
    except AnonPathError as exc:
        logger.error(f"{label}: template render failed ({exc}); skipping.")
        counters["failed"] += 1
        return None
    return old_dir, new_dir


async def _migrate_all_series(
    session: AsyncSession,
    args: argparse.Namespace,
    from_template: str,
    to_template: str,
    storage_path: Path,
    counters: dict[str, int],
    cleanup_candidates: set[Path],
    *,
    full_dir: bool,
) -> None:
    """Move SERIES-level dirs from old to new template.

    Streams ``Series WHERE anon_uid IS NOT NULL`` and, depending on
    ``full_dir``, moves either:

    * ``full_dir=False`` — only ``series_dir/dcm_anon/`` (default mode).
    * ``full_dir=True`` — the entire ``series_dir`` (working-folder mode,
      bundles dcm_anon with pipeline outputs).

    The cleanup root differs: for ``dcm_anon`` we collect the surviving
    parent ``series_dir`` (which still holds other files); for the full
    dir we collect ``series_dir.parent`` (study-level) so that
    ``--cleanup-empty`` can prune now-empty study_dirs.
    """
    stmt = (
        select(Series)
        .where(Series.anon_uid.is_not(None))  # type: ignore[union-attr]
        .options(
            selectinload(Series.study).selectinload(Study.patient)  # type: ignore[arg-type]
        )
        .execution_options(yield_per=500)
    )
    result = await session.stream_scalars(stmt)
    async for series in result:
        study = series.study
        patient = study.patient if study else None
        if study is None or patient is None:
            logger.warning(
                f"Series {series.series_uid} has no Study/Patient eager-loaded; skipping."
            )
            counters["failed"] += 1
            continue
        paths = _render_old_new_dirs(
            DicomQueryLevel.SERIES,
            patient=patient,
            study=study,
            series=series,
            from_template=from_template,
            to_template=to_template,
            storage_path=storage_path,
            counters=counters,
            label=f"Series {series.series_uid}",
        )
        if paths is None:
            continue
        old_series_dir, new_series_dir = paths
        if full_dir:
            old, new = old_series_dir, new_series_dir
            label = f"Series {series.series_uid} working_folder"
            cleanup_root = old_series_dir.parent
        else:
            old = old_series_dir / "dcm_anon"
            new = new_series_dir / "dcm_anon"
            label = f"Series {series.series_uid} dcm_anon"
            cleanup_root = old_series_dir
        outcome = await asyncio.to_thread(_move_dir_atomic, old, new, args.dry_run, label=label)
        counters[outcome] += 1
        if outcome == "moved" and not args.dry_run:
            cleanup_candidates.add(cleanup_root)


async def _migrate_records_by_level(
    session: AsyncSession,
    args: argparse.Namespace,
    level: DicomQueryLevel,
    from_template: str,
    to_template: str,
    storage_path: Path,
    counters: dict[str, int],
    cleanup_candidates: set[Path],
) -> None:
    """Move working_folder for each Record at the given STUDY or PATIENT level.

    Uses merge semantics: the new parent dir typically exists from the
    earlier SERIES pass, so loose study-/patient-level files are merged
    in alongside the already-moved series_dirs.
    """
    options = [selectinload(Record.patient)]  # type: ignore[arg-type]
    if level is DicomQueryLevel.STUDY:
        options.append(selectinload(Record.study))  # type: ignore[arg-type]
    stmt = (
        select(Record)
        .join(RecordType, Record.record_type_name == RecordType.name)  # type: ignore[arg-type]
        .where(RecordType.level == level.value)
        .options(*options)
        .execution_options(yield_per=200)
    )
    result = await session.stream_scalars(stmt)
    async for record in result:
        patient = record.patient
        study = record.study if level is DicomQueryLevel.STUDY else None
        if patient is None or (level is DicomQueryLevel.STUDY and study is None):
            logger.warning(
                f"Record {record.id} ({level.value}) missing eager-loaded relations; skipping."
            )
            counters["failed"] += 1
            continue
        label = f"Record {record.id} {level.value}"
        paths = _render_old_new_dirs(
            level,
            patient=patient,
            study=study,
            series=None,
            from_template=from_template,
            to_template=to_template,
            storage_path=storage_path,
            counters=counters,
            label=label,
        )
        if paths is None:
            continue
        old_dir, new_dir = paths
        outcome = await asyncio.to_thread(
            _merge_dir,
            old_dir,
            new_dir,
            args.dry_run,
            label=label,
        )
        counters[outcome] += 1
        if not args.dry_run:
            if outcome == "moved":
                cleanup_candidates.add(old_dir.parent)
            elif outcome == "same":
                # ``old_dir`` may have been emptied by the SERIES pass; let
                # cleanup walk up from it so ``--cleanup-empty`` can prune.
                cleanup_candidates.add(old_dir)


async def migrate_paths(args: argparse.Namespace) -> None:
    """Move anonymized files from ``--from`` template layout to ``--to`` layout.

    Default mode moves only ``series_dir/dcm_anon/``. With
    ``--include-working-folder``, performs three passes in bottom-up
    order (SERIES → STUDY → PATIENT) so that nested dirs are handled
    consistently. The DB is never modified — paths derive from
    Study/Patient/Series + RecordType.level.

    Run with ``--dry-run`` first to inspect the plan.
    """
    from_template = validate_template(args.from_template)
    to_template = validate_template(args.to_template)

    if from_template == to_template:
        logger.info("--from and --to templates are identical; nothing to do.")
        return

    include_wf = getattr(args, "include_working_folder", False)
    storage_path = Path(settings.storage_path)
    counters: dict[str, int] = {
        "moved": 0,
        "same": 0,
        "missing": 0,
        "collision": 0,
        "failed": 0,
    }
    cleanup_candidates: set[Path] = set()

    async with db_manager.async_session_factory() as session:
        # Bottom-up when include_wf=True: SERIES first so that new parent
        # dirs exist for STUDY/PATIENT passes to merge into.
        await _migrate_all_series(
            session,
            args,
            from_template,
            to_template,
            storage_path,
            counters,
            cleanup_candidates,
            full_dir=include_wf,
        )
        if include_wf:
            for level in (DicomQueryLevel.STUDY, DicomQueryLevel.PATIENT):
                await _migrate_records_by_level(
                    session,
                    args,
                    level,
                    from_template,
                    to_template,
                    storage_path,
                    counters,
                    cleanup_candidates,
                )

    logger.info(
        f"Done. moved={counters['moved']}, skipped_same={counters['same']}, "
        f"skipped_missing={counters['missing']}, "
        f"skipped_collision={counters['collision']}, failed={counters['failed']}"
    )

    if args.cleanup_empty and not args.dry_run and cleanup_candidates:
        removed = await asyncio.to_thread(_cleanup_empty_dirs, cleanup_candidates, storage_path)
        logger.info(f"Cleanup: removed {removed} empty directories left behind by migration.")

    if not args.dry_run:
        logger.info(
            "Restart the API server (and pipeline workers) to invalidate "
            "in-memory _dcm_anon_path_cache."
        )


def _cleanup_empty_dirs(roots: Iterable[Path], stop_at: Path) -> int:
    """Remove empty dirs in ``roots`` and walk up while empty, stopping at ``stop_at``.

    Only dirs that are ancestors of an actually-moved series/record are
    touched — stray empty dirs elsewhere under ``stop_at`` are left
    alone. Stops at the first non-empty ancestor and refuses to walk
    above ``stop_at``.
    """
    removed = 0
    stop_resolved = stop_at.resolve()
    seen: set[Path] = set()
    for start in roots:
        current = start.resolve()
        while current != stop_resolved:
            try:
                current.relative_to(stop_resolved)
            except ValueError:
                break  # don't escape above storage_path
            if current in seen:
                break
            seen.add(current)
            try:
                current.rmdir()
                removed += 1
            except OSError:
                break  # not empty (real content) or already gone
            current = current.parent
    return removed
