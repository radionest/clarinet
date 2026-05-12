"""CLI helpers for anonymization-related operations.

Currently provides ``clarinet anon migrate-paths`` — moves anonymized
dcm_anon directories from a source template layout to a target template
layout when ``settings.disk_path_template`` changes. The DB is not
touched (paths are pure-derive from Study/Patient/Series); only files
on disk are relocated.
"""

import argparse
import asyncio
import shutil
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.models.base import DicomQueryLevel
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


async def migrate_paths(args: argparse.Namespace) -> None:
    """Move anonymized dcm_anon dirs from ``--from`` template to ``--to`` template.

    Workflow:
        1. Validate both templates (same rules as ``settings``).
        2. Query all series with ``anon_uid IS NOT NULL`` (= already
           anonymized) and their eagerly-loaded Study/Patient.
        3. For each: render both paths, compare, optionally move.
        4. Optionally cleanup empty parent directories.

    Run with ``--dry-run`` first to inspect the plan.
    """
    from_template = validate_template(args.from_template)
    to_template = validate_template(args.to_template)

    if from_template == to_template:
        logger.info("--from and --to templates are identical; nothing to do.")
        return

    storage_path = Path(settings.storage_path)
    moved = 0
    skipped_missing = 0
    skipped_same = 0
    failed = 0

    async with db_manager.async_session_factory() as session:
        stmt = (
            select(Series)
            .where(Series.anon_uid.is_not(None))  # type: ignore[union-attr]
            .options(
                selectinload(Series.study).selectinload(Study.patient)  # type: ignore[arg-type]
            )
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    logger.info(f"Found {len(rows)} anonymized series to inspect.")

    for series in rows:
        study = series.study
        patient = study.patient if study else None
        if study is None or patient is None:
            logger.warning(
                f"Series {series.series_uid} has no Study/Patient eager-loaded; skipping."
            )
            failed += 1
            continue

        try:
            ctx = build_context(patient=patient, study=study, series=series)
            old_series_dir = render_working_folder(
                from_template, DicomQueryLevel.SERIES, ctx, storage_path
            )
            new_series_dir = render_working_folder(
                to_template, DicomQueryLevel.SERIES, ctx, storage_path
            )
        except AnonPathError as exc:
            logger.error(f"Series {series.series_uid}: template render failed ({exc}); skipping.")
            failed += 1
            continue

        old_dcm_anon = old_series_dir / "dcm_anon"
        new_dcm_anon = new_series_dir / "dcm_anon"

        if old_dcm_anon == new_dcm_anon:
            skipped_same += 1
            continue
        if not old_dcm_anon.is_dir():
            logger.debug(f"Series {series.series_uid}: source {old_dcm_anon} missing; skipping.")
            skipped_missing += 1
            continue

        if args.dry_run:
            logger.info(f"[dry-run] {old_dcm_anon} -> {new_dcm_anon}")
            moved += 1
            continue

        try:
            await asyncio.to_thread(new_dcm_anon.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, str(old_dcm_anon), str(new_dcm_anon))
        except OSError as exc:
            logger.error(f"Failed to move {old_dcm_anon} -> {new_dcm_anon}: {exc}")
            failed += 1
            continue
        logger.info(f"Moved {old_dcm_anon} -> {new_dcm_anon}")
        moved += 1

    logger.info(
        f"Done. moved={moved}, skipped_same={skipped_same}, "
        f"skipped_missing={skipped_missing}, failed={failed}"
    )

    if args.cleanup_empty and not args.dry_run:
        removed = await asyncio.to_thread(_cleanup_empty_dirs, storage_path)
        logger.info(f"Cleanup: removed {removed} empty parent directories under storage_path.")

    if not args.dry_run:
        logger.info(
            "Restart the API server (and pipeline workers) to invalidate "
            "in-memory _dcm_anon_path_cache."
        )


def _cleanup_empty_dirs(root: Path) -> int:
    """Recursively remove empty directories under ``root`` (excluding ``root`` itself)."""
    removed = 0
    # Walk bottom-up so we remove leaves before their parents.
    all_dirs: list[Path] = []
    for path in _walk_dirs(root):
        all_dirs.append(path)
    for path in reversed(all_dirs):
        if path == root:
            continue
        try:
            path.rmdir()  # only succeeds on empty dirs
            removed += 1
        except OSError:
            continue
    return removed


def _walk_dirs(root: Path) -> Iterable[Path]:
    """Yield ``root`` and all subdirectories (top-down)."""
    yield root
    for path in root.rglob("*"):
        if path.is_dir():
            yield path
