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
        2. Stream all series with ``anon_uid IS NOT NULL`` from the DB
           (``yield_per=500`` + eagerly-loaded Study/Patient) so memory
           stays bounded on large databases.
        3. For each: render both paths, compare, optionally move.
        4. Optionally walk up from the parent of each successfully moved
           ``dcm_anon`` and remove empty ancestor dirs (capped at
           ``storage_path``). Stray empty dirs elsewhere are left alone.

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
    cleanup_candidates: set[Path] = set()

    async with db_manager.async_session_factory() as session:
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
                logger.error(
                    f"Series {series.series_uid}: template render failed ({exc}); skipping."
                )
                failed += 1
                continue

            old_dcm_anon = old_series_dir / "dcm_anon"
            new_dcm_anon = new_series_dir / "dcm_anon"

            if old_dcm_anon == new_dcm_anon:
                skipped_same += 1
                continue
            if not old_dcm_anon.is_dir():
                logger.debug(
                    f"Series {series.series_uid}: source {old_dcm_anon} missing; skipping."
                )
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
            cleanup_candidates.add(old_series_dir)

    logger.info(
        f"Done. moved={moved}, skipped_same={skipped_same}, "
        f"skipped_missing={skipped_missing}, failed={failed}"
    )

    if args.cleanup_empty and not args.dry_run and cleanup_candidates:
        removed = await asyncio.to_thread(_cleanup_empty_dirs, cleanup_candidates, storage_path)
        logger.info(f"Cleanup: removed {removed} empty directories left behind by migrated series.")

    if not args.dry_run:
        logger.info(
            "Restart the API server (and pipeline workers) to invalidate "
            "in-memory _dcm_anon_path_cache."
        )


def _cleanup_empty_dirs(roots: Iterable[Path], stop_at: Path) -> int:
    """Remove empty dirs in ``roots`` and walk up while empty, stopping at ``stop_at``.

    Only dirs that are ancestors of an actually-moved series are touched
    — stray empty dirs elsewhere under ``stop_at`` are left alone.
    Stops at the first non-empty ancestor and refuses to walk above
    ``stop_at``.
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
