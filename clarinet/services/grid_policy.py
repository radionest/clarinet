"""Submit-time enforcement of OUTPUT grid-conformance declarations.

Runs after a ``slicer_result_validator`` has written its output and before the
record data is committed — the only point in the submit flow that can still
reject. ``_sync_output_files`` runs post-commit and never raises, so it cannot
serve as the guard.
"""

from pathlib import Path

from clarinet.exceptions.domain import BusinessRuleViolationError, ImageError
from clarinet.files import Files
from clarinet.models.file_schema import FileRole, GridMismatchAction
from clarinet.models.record import RecordRead
from clarinet.services.image.grid import GridRelation, RelationKind, grid_relation
from clarinet.services.image.grid_io import read_grid
from clarinet.services.image.segmentation import conform_seg_to_grid
from clarinet.utils.logger import logger


def _repair(subject: Path, reference: Path) -> None:
    """Conform *subject* onto *reference*'s grid in place (exact re-index)."""
    conform_seg_to_grid(subject, reference, out_path=subject)


def _delete(subject: Path) -> None:
    """Remove *subject*; tolerate a concurrent delete already having won the race."""
    subject.unlink(missing_ok=True)


async def enforce_output_grids(record: RecordRead, *, parent: RecordRead | None = None) -> None:
    """Enforce every declared OUTPUT grid pair, applying each file's action.

    ``conform`` repairs an exactly-repairable pair and lets the submission
    proceed; ``delete`` removes the file; both still reject anything that is
    not repairable. An unset action is ``reject`` — declaring a reference must
    never fail open. A present-but-unreadable file (e.g. a truncated write)
    is folded into the same 409 rather than surfacing as an unhandled 500.

    Raises:
        BusinessRuleViolationError: When any declared OUTPUT pair cannot be
            made to conform, or cannot be read (→ 409).
    """
    registry = record.record_type.file_registry or []
    declared = [fd for fd in registry if fd.role == FileRole.OUTPUT and fd.grid_conform_to]
    if not declared:
        return

    by_name = {fd.name: fd for fd in registry}
    files = Files.for_reader(record, parent=parent)

    for fd in declared:
        ref_def = by_name.get(fd.grid_conform_to or "")
        if ref_def is None:
            # Reachable: RecordTypeCreate.file_registry defaults to None and many
            # call sites attach file links separately, so validate_grid_conformance
            # is a documented no-op for those — a dangling grid_conform_to can and
            # does reach runtime.
            raise BusinessRuleViolationError(
                f"Grid reference '{fd.grid_conform_to}' for '{fd.name}' is not "
                f"bound to this record type"
            )

        subject = files.resolve(fd)
        reference = files.resolve(ref_def)
        if not await Files.in_thread(subject.is_file):
            continue  # conformance is conditional on existence
        if not await Files.in_thread(reference.is_file):
            raise BusinessRuleViolationError(
                f"Grid reference '{ref_def.name}' for output '{fd.name}' is not "
                f"on disk — cannot verify the output's grid"
            )

        try:
            relation: GridRelation = await Files.in_thread(
                lambda s=subject, r=reference: grid_relation(read_grid(r), read_grid(s))
            )
        except ImageError as e:
            raise BusinessRuleViolationError(
                f"Cannot read the grid of output '{fd.name}' or its reference '{ref_def.name}': {e}"
            ) from e
        if relation.kind is RelationKind.SAME:
            continue

        action: GridMismatchAction = fd.on_grid_mismatch or "reject"

        if action == "conform" and relation.kind is RelationKind.REARRANGED:
            try:
                await Files.in_thread(_repair, subject, reference)
                recheck: GridRelation = await Files.in_thread(
                    lambda s=subject, r=reference: grid_relation(read_grid(r), read_grid(s))
                )
            except ImageError as e:
                raise BusinessRuleViolationError(
                    f"Failed to conform output '{fd.name}' onto '{ref_def.name}': {e}"
                ) from e
            if recheck.kind is RelationKind.SAME:
                logger.info(
                    f"Record {record.id}: conformed output '{fd.name}' onto "
                    f"'{ref_def.name}' grid ({relation.kind.value})"
                )
                continue
            raise BusinessRuleViolationError(
                f"Output '{fd.name}' still does not match '{ref_def.name}' after "
                f"conforming ({recheck.kind.value})"
            )

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
            )

        raise BusinessRuleViolationError(
            f"Output '{fd.name}' does not share '{ref_def.name}'s grid "
            f"({relation.kind.value}). Re-export it conformed to the reference."
        )
