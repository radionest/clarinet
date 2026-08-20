"""Submit-time enforcement of OUTPUT grid-conformance declarations.

Runs after a ``slicer_result_validator`` has written its output and before the
record data is committed — the only point in the submit flow that can still
reject. ``sync_output_files`` runs post-commit and never raises, so it cannot
serve as the guard.
"""

import os
from pathlib import Path

from clarinet.exceptions.domain import BusinessRuleViolationError, ImageError
from clarinet.files import Files
from clarinet.models.file_schema import FileRole, GridMismatchAction
from clarinet.models.record import RecordRead
from clarinet.services.image.grid import Grid, GridRelation, RelationKind, grid_relation
from clarinet.services.image.grid_io import read_grid
from clarinet.services.image.segmentation import conform_seg_to_grid
from clarinet.utils.logger import logger


def _relate(subject: Path, reference: Path) -> tuple[GridRelation, Grid, Grid]:
    """Classify *subject* against *reference*, returning both grids for diagnostics."""
    reference_grid = read_grid(reference)
    subject_grid = read_grid(subject)
    return grid_relation(reference_grid, subject_grid), reference_grid, subject_grid


def _repair_tmp_path(subject: Path) -> Path:
    """Hidden sibling temp target for a conform repair.

    The dot-prefix keeps the extension chain intact (``.repair.seg.nii``), so
    format detection by suffix still works.
    """
    return subject.with_name(".repair." + subject.name)


def _repair_to_temp(subject: Path, reference: Path) -> Path:
    """Conform *subject* onto *reference*'s grid into a hidden sibling temp file.

    The caller must verify the result and ``os.replace`` it over *subject* —
    a failed repair never touches the original bytes.
    """
    tmp = _repair_tmp_path(subject)
    conform_seg_to_grid(subject, reference, out_path=tmp)
    return tmp


def _delete(subject: Path) -> None:
    """Remove *subject*; tolerate a concurrent delete already having won the race."""
    subject.unlink(missing_ok=True)


def _summaries(subject_name: str, subject_grid: Grid, ref_name: str, ref_grid: Grid) -> str:
    return f"\n  {subject_name}: {subject_grid.summary()}\n  {ref_name}: {ref_grid.summary()}"


async def enforce_output_grids(
    record: RecordRead, *, parent: RecordRead | None = None
) -> list[str]:
    """Enforce every declared OUTPUT grid pair, applying each file's action.

    ``conform`` repairs an exactly-repairable pair and lets the submission
    proceed; ``delete`` removes the file; both still reject anything that is
    not repairable. An unset action is ``reject`` — declaring a reference must
    never fail open. A present-but-unreadable file (e.g. a truncated write)
    is folded into the same 409 rather than surfacing as an unhandled 500.

    A ``conform`` repair is written to a hidden sibling temp file, re-verified
    from disk, and only then atomically moved over the original — a failed
    repair or re-check leaves the original bytes untouched.

    Returns:
        Names of the OUTPUT definitions that were successfully conformed
        (empty when nothing was repaired). Callers on the update path use it
        to re-sync stored checksums, which ``submit_data`` does on its own.

    Raises:
        BusinessRuleViolationError: When any declared OUTPUT pair cannot be
            made to conform, or cannot be read (→ 409).
    """
    registry = record.record_type.file_registry or []
    declared = [fd for fd in registry if fd.role == FileRole.OUTPUT and fd.grid_conform_to]
    if not declared:
        return []

    by_name = {fd.name: fd for fd in registry}
    files = Files.for_reader(record, parent=parent)
    repaired: list[str] = []

    for fd in declared:
        ref_def = by_name.get(fd.grid_conform_to or "")
        if ref_def is None:
            # Reachable: RecordTypeCreate.file_registry defaults to None and many
            # call sites attach file links separately, so validate_grid_conformance
            # is a documented no-op for those — a dangling grid_conform_to can and
            # does reach runtime.
            msg = (
                f"Grid reference '{fd.grid_conform_to}' for '{fd.name}' is not "
                f"bound to this record type"
            )
            logger.warning(f"Record {record.id}: OUTPUT grid guard — {msg}")
            raise BusinessRuleViolationError(msg)

        subject = files.resolve(fd)
        reference = files.resolve(ref_def)
        if not await Files.in_thread(subject.is_file):
            continue  # conformance is conditional on existence
        if not await Files.in_thread(reference.is_file):
            msg = (
                f"Grid reference '{ref_def.name}' for output '{fd.name}' is not "
                f"on disk — cannot verify the output's grid"
            )
            logger.warning(f"Record {record.id}: OUTPUT grid guard — {msg}")
            raise BusinessRuleViolationError(msg)

        try:
            relation, reference_grid, subject_grid = await Files.in_thread(
                _relate, subject, reference
            )
        except ImageError as e:
            msg = (
                f"Cannot read the grid of output '{fd.name}' or its reference '{ref_def.name}': {e}"
            )
            logger.warning(f"Record {record.id}: OUTPUT grid guard — {msg}")
            raise BusinessRuleViolationError(msg) from e
        if relation.kind is RelationKind.SAME:
            continue

        action: GridMismatchAction = fd.on_grid_mismatch or "reject"

        if action == "conform" and relation.kind is RelationKind.REARRANGED:
            # Computed up front so the cleanup below also catches a repair
            # that raised after partially writing the temp file.
            tmp = _repair_tmp_path(subject)
            try:
                await Files.in_thread(_repair_to_temp, subject, reference)
                recheck, recheck_ref, recheck_subj = await Files.in_thread(_relate, tmp, reference)
            except ImageError as e:
                await Files.in_thread(_delete, tmp)
                msg = f"Failed to conform output '{fd.name}' onto '{ref_def.name}': {e}"
                logger.warning(f"Record {record.id}: OUTPUT grid guard — {msg}")
                raise BusinessRuleViolationError(msg) from e
            if recheck.kind is RelationKind.SAME:
                await Files.in_thread(os.replace, tmp, subject)
                logger.info(
                    f"Record {record.id}: conformed output '{fd.name}' onto "
                    f"'{ref_def.name}' grid ({relation.kind.value})"
                )
                repaired.append(fd.name)
                continue
            await Files.in_thread(_delete, tmp)
            msg = (
                f"Output '{fd.name}' still does not match '{ref_def.name}' after "
                f"conforming ({recheck.kind.value})."
                + _summaries(fd.name, recheck_subj, ref_def.name, recheck_ref)
            )
            logger.warning(f"Record {record.id}: OUTPUT grid guard — {msg}")
            raise BusinessRuleViolationError(msg)

        if action == "delete":
            await Files.in_thread(_delete, subject)
            logger.warning(
                f"Record {record.id}: deleted output '{fd.name}' — grid "
                f"{relation.kind.value} vs '{ref_def.name}'"
            )
            raise BusinessRuleViolationError(
                f"Output '{fd.name}' did not match '{ref_def.name}'s grid "
                f"({relation.kind.value}) and was deleted per on_grid_mismatch="
                f"delete. Re-run the task to regenerate it."
                + _summaries(fd.name, subject_grid, ref_def.name, reference_grid)
            )

        msg = (
            f"Output '{fd.name}' does not share '{ref_def.name}'s grid "
            f"({relation.kind.value}). Re-export it conformed to the reference."
            + _summaries(fd.name, subject_grid, ref_def.name, reference_grid)
        )
        logger.warning(f"Record {record.id}: OUTPUT grid guard — {msg}")
        raise BusinessRuleViolationError(msg)

    return repaired
