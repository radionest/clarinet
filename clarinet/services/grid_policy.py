"""Submit-time enforcement of OUTPUT grid-conformance declarations.

Runs after a ``slicer_result_validator`` has written its output and before the
record data is committed — the only point in the submit flow that can still
reject. ``_sync_output_files`` runs post-commit and never raises, so it cannot
serve as the guard.
"""

from pathlib import Path

from clarinet.exceptions.http import CONFLICT
from clarinet.files import Files
from clarinet.models.file_schema import FileRole, GridMismatchAction
from clarinet.models.record import RecordRead
from clarinet.services.image.grid import RelationKind, grid_relation
from clarinet.services.image.grid_io import read_grid
from clarinet.services.image.segmentation import conform_seg_to_grid
from clarinet.utils.logger import logger


def _repair(subject: Path, reference: Path) -> None:
    """Conform *subject* onto *reference*'s grid in place (exact re-index)."""
    conform_seg_to_grid(subject, reference, out_path=subject)


async def enforce_output_grids(record: RecordRead, *, parent: RecordRead | None = None) -> None:
    """Enforce every declared OUTPUT grid pair, applying each file's action.

    ``conform`` repairs an exactly-repairable pair and lets the submission
    proceed; ``delete`` removes the file; both still reject anything that is
    not repairable. An unset action is ``reject`` — declaring a reference must
    never fail open.

    Raises:
        HTTPException: 409 when any declared OUTPUT pair cannot be made to
            conform.
    """
    registry = record.record_type.file_registry or []
    declared = [fd for fd in registry if fd.role == FileRole.OUTPUT and fd.grid_conform_to]
    if not declared:
        return

    by_name = {fd.name: fd for fd in registry}
    files = Files.for_reader(record, parent=parent)

    for fd in declared:
        ref_def = by_name.get(fd.grid_conform_to or "")
        if ref_def is None:  # unreachable: config load rejects this
            raise CONFLICT.with_context(
                f"Grid reference '{fd.grid_conform_to}' for '{fd.name}' is not "
                f"bound to this record type"
            )

        subject = files.resolve(fd)
        reference = files.resolve(ref_def)
        if not subject.is_file():
            continue  # conformance is conditional on existence
        if not reference.is_file():
            raise CONFLICT.with_context(
                f"Grid reference '{ref_def.name}' for output '{fd.name}' is not "
                f"on disk — cannot verify the output's grid"
            )

        relation = await Files.in_thread(
            lambda s=subject, r=reference: grid_relation(read_grid(r), read_grid(s))
        )
        if relation.kind is RelationKind.SAME:
            continue

        action: GridMismatchAction = fd.on_grid_mismatch or "reject"

        if action == "conform" and relation.kind is RelationKind.REARRANGED:
            await Files.in_thread(_repair, subject, reference)
            recheck = await Files.in_thread(
                lambda s=subject, r=reference: grid_relation(read_grid(r), read_grid(s))
            )
            if recheck.kind is RelationKind.SAME:
                logger.info(
                    f"Record {record.id}: conformed output '{fd.name}' onto "
                    f"'{ref_def.name}' grid ({relation.kind.value})"
                )
                continue
            raise CONFLICT.with_context(
                f"Output '{fd.name}' still does not match '{ref_def.name}' after "
                f"conforming ({recheck.kind.value})"
            )

        if action == "delete":
            await Files.in_thread(subject.unlink)
            logger.warning(
                f"Record {record.id}: deleted output '{fd.name}' — grid "
                f"{relation.kind.value} vs '{ref_def.name}'"
            )
            raise CONFLICT.with_context(
                f"Output '{fd.name}' did not match '{ref_def.name}'s grid "
                f"({relation.kind.value}) and was deleted per on_grid_mismatch="
                f"delete. Re-run the task to regenerate it."
            )

        raise CONFLICT.with_context(
            f"Output '{fd.name}' does not share '{ref_def.name}'s grid "
            f"({relation.kind.value}). Re-export it conformed to the reference."
        )
