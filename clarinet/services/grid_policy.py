"""Submit-time enforcement of OUTPUT grid-conformance declarations.

Runs after a ``slicer_result_validator`` has written its output and before the
record data is committed — the only point in the submit flow that can still
reject. ``sync_output_files`` runs post-commit and never raises, so it cannot
serve as the guard.

The policy itself is :func:`decide` — a pure function of the pair's relation
kind, the declared action and whether the writer can repair the subject
exactly. Everything else here resolves the pair, probes disk and carries the
verdict out.
"""

import enum
import os
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, assert_never

from clarinet.exceptions.domain import BusinessRuleViolationError, ImageError
from clarinet.files import Files
from clarinet.models.file_schema import FileDefinitionRead, FileRole, GridMismatchAction
from clarinet.models.record import RecordRead
from clarinet.services.image.grid import RelationKind
from clarinet.services.image.grid_io import PairVerdict, classify_pair
from clarinet.services.image.segmentation import conform_seg_to_grid, is_conform_repairable
from clarinet.utils.logger import logger


class Verdict(enum.Enum):
    """What the OUTPUT policy does with one declared pair."""

    PASS = "pass"
    REJECT = "reject"
    REPAIR = "repair"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class Decision:
    """A verdict plus, for a REJECT, the clause saying why no repair was possible.

    ``reason`` is folded into the 409 text; empty keeps the generic wording.
    """

    verdict: Verdict
    reason: str = ""


_NOT_8BIT_REASON = "not an 8-bit mask on disk, conform would quantize it"


def decide(action: GridMismatchAction | None, kind: RelationKind, *, repairable: bool) -> Decision:
    """The whole OUTPUT grid policy. Pure: no I/O, no logging.

    ``conform`` repairs only a REARRANGED pair whose subject is
    ``is_conform_repairable`` (an 8-bit or layered mask, so the exact index
    rearrangement cannot quantize it); anything else falls back to
    ``reject``'s advice. An unset action is ``reject`` — declaring a reference
    must never fail open.
    """
    match kind:
        case RelationKind.SAME:
            return Decision(Verdict.PASS)
        case RelationKind.REARRANGED | RelationKind.FOREIGN:
            match action:
                case None | "reject":
                    return Decision(Verdict.REJECT)
                case "delete":
                    return Decision(Verdict.DELETE)
                case "conform" if kind is RelationKind.FOREIGN:
                    return Decision(Verdict.REJECT)
                case "conform" if not repairable:
                    return Decision(Verdict.REJECT, reason=_NOT_8BIT_REASON)
                case "conform":
                    return Decision(Verdict.REPAIR)
                case _:
                    assert_never(action)
        case _:
            assert_never(kind)


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


@dataclass(frozen=True, slots=True)
class OutputPair:
    """One declared OUTPUT file resolved on disk and classified against its reference."""

    fd: FileDefinitionRead
    ref_def: FileDefinitionRead
    subject: Path
    reference: Path
    verdict: PairVerdict


def _warn_and_raise(record_id: int, msg: str, *, cause: ImageError | None = None) -> NoReturn:
    """Log the guard's refusal and turn it into the 409.

    Chains ``from cause`` only when one is given: an unconditional
    ``from None`` would suppress the context a bare raise keeps.
    """
    logger.warning(f"Record {record_id}: OUTPUT grid guard — {msg}")
    if cause is None:
        raise BusinessRuleViolationError(msg)
    raise BusinessRuleViolationError(msg) from cause


async def _repair_verified(pair: OutputPair, record_id: int) -> None:
    """Conform the subject onto its reference through a hidden sibling temp file.

    The result is re-verified from disk and only then atomically moved over
    the original — a failed repair or re-check leaves the original bytes
    untouched.
    """
    fd, ref_def = pair.fd, pair.ref_def
    # Computed up front so the cleanup below also catches a repair
    # that raised after partially writing the temp file.
    tmp = _repair_tmp_path(pair.subject)
    try:
        await Files.in_thread(_repair_to_temp, pair.subject, pair.reference)
        recheck = await Files.in_thread(classify_pair, tmp, pair.reference)
    except ImageError as e:
        await Files.in_thread(_delete, tmp)
        _warn_and_raise(
            record_id, f"Failed to conform output '{fd.name}' onto '{ref_def.name}': {e}", cause=e
        )
    if recheck.kind is RelationKind.SAME:
        await Files.in_thread(os.replace, tmp, pair.subject)
        logger.info(
            f"Record {record_id}: conformed output '{fd.name}' onto "
            f"'{ref_def.name}' grid ({pair.verdict.kind.value})"
        )
        return
    await Files.in_thread(_delete, tmp)
    _warn_and_raise(
        record_id,
        f"Output '{fd.name}' still does not match '{ref_def.name}' after "
        f"conforming ({recheck.kind.value})." + recheck.describe(fd.name, ref_def.name),
    )


async def _apply(decision: Decision, pair: OutputPair, record_id: int) -> bool:
    """Carry *decision* out on disk. Returns True when the subject was repaired."""
    fd, ref_def, kind = pair.fd, pair.ref_def, pair.verdict.kind.value
    verdict = decision.verdict
    match verdict:
        case Verdict.PASS:
            return False
        case Verdict.REPAIR:
            await _repair_verified(pair, record_id)
            return True
        case Verdict.DELETE:
            await Files.in_thread(_delete, pair.subject)
            _warn_and_raise(
                record_id,
                f"Output '{fd.name}' did not match '{ref_def.name}'s grid "
                f"({kind}) and was deleted per on_grid_mismatch="
                f"delete. Re-run the task to regenerate it."
                + pair.verdict.describe(fd.name, ref_def.name),
            )
        case Verdict.REJECT:
            because = f" and cannot be conformed: {decision.reason}" if decision.reason else ""
            _warn_and_raise(
                record_id,
                f"Output '{fd.name}' does not share '{ref_def.name}'s grid ({kind}){because}. "
                f"Re-export it conformed to the reference."
                + pair.verdict.describe(fd.name, ref_def.name),
            )
        case _:
            assert_never(verdict)


async def enforce_output_grids(
    record: RecordRead, *, parent: RecordRead | None = None
) -> list[str]:
    """Enforce every declared OUTPUT grid pair, applying each file's action.

    The action for each pair is chosen by :func:`decide` and carried out by
    :func:`_apply`: ``conform`` repairs an exactly-repairable pair and lets the
    submission proceed; ``delete`` removes the file; both still reject anything
    that is not repairable. An unset action is ``reject`` — declaring a
    reference must never fail open. A present-but-unreadable file (e.g. a
    truncated write) is folded into the same 409 rather than surfacing as an
    unhandled 500.

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
            _warn_and_raise(
                record.id,
                f"Grid reference '{fd.grid_conform_to}' for '{fd.name}' is not "
                f"bound to this record type",
            )

        subject = files.resolve(fd)
        reference = files.resolve(ref_def)
        if not await Files.in_thread(subject.is_file):
            continue  # conformance is conditional on existence
        if not await Files.in_thread(reference.is_file):
            _warn_and_raise(
                record.id,
                f"Grid reference '{ref_def.name}' for output '{fd.name}' is not "
                f"on disk — cannot verify the output's grid",
            )

        try:
            verdict = await Files.in_thread(classify_pair, subject, reference)
            repairable = verdict.kind is not RelationKind.SAME and await Files.in_thread(
                is_conform_repairable, subject
            )
        except ImageError as e:
            _warn_and_raise(
                record.id,
                f"Cannot read the grid of output '{fd.name}' or its reference "
                f"'{ref_def.name}': {e}",
                cause=e,
            )

        pair = OutputPair(
            fd=fd,
            ref_def=ref_def,
            subject=subject,
            reference=reference,
            verdict=verdict,
        )
        decision = decide(fd.on_grid_mismatch, verdict.kind, repairable=repairable)
        if await _apply(decision, pair, record.id):
            repaired.append(fd.name)

    return repaired
