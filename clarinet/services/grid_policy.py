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
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, assert_never

from clarinet.exceptions.domain import ImageError, OutputGridMismatchError
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
    """Hidden sibling temp target for a conform repair, unique per call.

    The dot-prefix and the token both sit *before* the original name
    (``.repair.<token>.seg.nii``): ``Path.suffixes`` drops a leading dot and
    every format probe tests suffix membership, so detection is unchanged.
    Unique because two concurrent repairs of one record must not share a
    file — one request's re-check/replace window would otherwise pick up
    the other's partial rewrite and install it over the original.
    """
    return subject.with_name(f".repair.{uuid.uuid4().hex[:12]}.{subject.name}")


def _repair_to_temp(subject: Path, reference: Path, tmp: Path) -> None:
    """Conform *subject* onto *reference*'s grid into *tmp*.

    The caller verifies the result and ``os.replace``s it over *subject* —
    a failed repair never touches the original bytes.
    """
    conform_seg_to_grid(subject, reference, out_path=tmp)


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
    repairable: bool


@dataclass(frozen=True, slots=True)
class Unclassifiable:
    """Why a declared pair could not even be classified.

    The guard turns it into the 409, the preview reports it — same message.
    """

    message: str
    cause: ImageError | None = None


@dataclass(frozen=True, slots=True)
class PreviewEntry:
    """One declared OUTPUT pair the submit guard would refuse, as the preview reports it."""

    file_name: str
    message: str


def _declared(registry: Sequence[FileDefinitionRead]) -> list[FileDefinitionRead]:
    """The OUTPUT definitions that declare a grid reference."""
    return [fd for fd in registry if fd.role == FileRole.OUTPUT and fd.grid_conform_to]


async def _resolve_pair(
    fd: FileDefinitionRead, by_name: Mapping[str, FileDefinitionRead], files: Files
) -> OutputPair | Unclassifiable | None:
    """Resolve one declared OUTPUT pair on disk and classify it.

    ``None`` when the subject is absent — conformance is conditional on the
    declaring file's existence. :class:`Unclassifiable` when the pair cannot
    be classified at all: the reference is unbound or not on disk, or a grid
    cannot be read. Shared by :func:`enforce_output_grids` and
    :func:`preview_output_grids`, so both see exactly the same pairs.
    """
    ref_def = by_name.get(fd.grid_conform_to or "")
    if ref_def is None:
        # Reachable: RecordTypeCreate.file_registry defaults to None and many
        # call sites attach file links separately, so validate_grid_conformance
        # is a documented no-op for those — a dangling grid_conform_to can and
        # does reach runtime.
        return Unclassifiable(
            f"Grid reference '{fd.grid_conform_to}' for '{fd.name}' is not "
            f"bound to this record type"
        )

    subject = files.resolve(fd)
    reference = files.resolve(ref_def)
    if not await Files.in_thread(subject.is_file):
        return None
    if not await Files.in_thread(reference.is_file):
        return Unclassifiable(
            f"Grid reference '{ref_def.name}' for output '{fd.name}' is not "
            f"on disk — cannot verify the output's grid"
        )

    try:
        verdict: PairVerdict = await Files.in_thread(classify_pair, subject, reference)
        repairable = verdict.kind is not RelationKind.SAME and await Files.in_thread(
            is_conform_repairable, subject
        )
    except ImageError as e:
        return Unclassifiable(
            f"Cannot read the grid of output '{fd.name}' or its reference '{ref_def.name}': {e}",
            cause=e,
        )
    return OutputPair(
        fd=fd,
        ref_def=ref_def,
        subject=subject,
        reference=reference,
        verdict=verdict,
        repairable=bool(repairable),
    )


def _warn_and_raise(record_id: int, msg: str, *, cause: ImageError | None = None) -> NoReturn:
    """Log the guard's refusal and turn it into the 409 (``code: GRID_MISMATCH``).

    Every refusal — a classified mismatch, a deleted file, an unreadable one,
    a reference missing or unbound — raises the same class, because the
    client's next step is the same. Chains ``from cause`` only when one is
    given: an unconditional ``from None`` would suppress the context a bare
    raise keeps.
    """
    logger.warning(f"Record {record_id}: OUTPUT grid guard — {msg}")
    if cause is None:
        raise OutputGridMismatchError(msg)
    raise OutputGridMismatchError(msg) from cause


async def _repair_and_recheck(pair: OutputPair, tmp: Path, record_id: int) -> PairVerdict:
    """Write the repair to *tmp* and classify it from disk, never trusting the writer."""
    try:
        await Files.in_thread(_repair_to_temp, pair.subject, pair.reference, tmp)
        recheck: PairVerdict = await Files.in_thread(classify_pair, tmp, pair.reference)
        return recheck
    except ImageError as e:
        _warn_and_raise(
            record_id,
            f"Failed to conform output '{pair.fd.name}' onto '{pair.ref_def.name}': {e}",
            cause=e,
        )


async def _repair_verified(pair: OutputPair, record_id: int) -> None:
    """Conform the subject onto its reference through a hidden sibling temp file.

    The result is re-verified from disk and only then atomically moved over
    the original — a failed repair or re-check leaves the original bytes
    untouched. The temp file is removed whatever happens: a ``finally``
    rather than per-branch deletes, because a writer failure outside
    ``ImageError`` (an unwrapped reader error, a ``MemoryError``) is a 500
    either way, but an orphaned dotfile would be matched by ``Path.glob`` in
    any overlapping collection pattern for good.
    """
    fd, ref_def = pair.fd, pair.ref_def
    tmp = _repair_tmp_path(pair.subject)
    try:
        recheck = await _repair_and_recheck(pair, tmp, record_id)
        if recheck.kind is not RelationKind.SAME:
            _warn_and_raise(
                record_id,
                f"Output '{fd.name}' still does not match '{ref_def.name}' after "
                f"conforming ({recheck.kind.value})." + recheck.describe(fd.name, ref_def.name),
            )
        await Files.in_thread(os.replace, tmp, pair.subject)
    finally:
        # A no-op once os.replace has moved the file away.
        await Files.in_thread(_delete, tmp)
    logger.info(
        f"Record {record_id}: conformed output '{fd.name}' onto "
        f"'{ref_def.name}' grid ({pair.verdict.kind.value})"
    )


async def _apply(decision: Decision, pair: OutputPair, record_id: int) -> bool:
    """Carry *decision* out on disk. Returns True when the subject was repaired."""
    fd, ref_def, kind = pair.fd, pair.ref_def, pair.verdict.kind.value
    outcome = decision.verdict
    match outcome:
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
            assert_never(outcome)


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
        OutputGridMismatchError: When any declared OUTPUT pair cannot be
            made to conform, or cannot be read (→ 409, ``code: GRID_MISMATCH``).
    """
    registry = record.record_type.file_registry or []
    declared = _declared(registry)
    if not declared:
        return []

    by_name = {fd.name: fd for fd in registry}
    files = Files.for_reader(record, parent=parent)
    repaired: list[str] = []

    for fd in declared:
        pair = await _resolve_pair(fd, by_name, files)
        if pair is None:
            continue  # conformance is conditional on existence
        if isinstance(pair, Unclassifiable):
            _warn_and_raise(record.id, pair.message, cause=pair.cause)
        decision = decide(fd.on_grid_mismatch, pair.verdict.kind, repairable=pair.repairable)
        if await _apply(decision, pair, record.id):
            repaired.append(fd.name)

    return repaired


def _preview_message(decision: Decision, pair: OutputPair) -> str | None:
    """The preview's wording for a verdict the guard would refuse; None for PASS and REPAIR."""
    fd, ref_def, kind = pair.fd, pair.ref_def, pair.verdict.kind.value
    action = fd.on_grid_mismatch or "reject"
    outcome = decision.verdict
    match outcome:
        case Verdict.PASS | Verdict.REPAIR:
            return None
        case Verdict.DELETE:
            return (
                f"Output '{fd.name}' does not share '{ref_def.name}'s grid ({kind}); "
                f"on_grid_mismatch={action} deletes it at submission (409)."
                + pair.verdict.describe(fd.name, ref_def.name)
            )
        case Verdict.REJECT:
            because = f" and cannot be conformed: {decision.reason}" if decision.reason else ""
            return (
                f"Output '{fd.name}' does not share '{ref_def.name}'s grid ({kind}){because}; "
                f"on_grid_mismatch={action} rejects the submission (409)."
                + pair.verdict.describe(fd.name, ref_def.name)
            )
        case _:
            assert_never(outcome)


async def preview_output_grids(
    record: RecordRead, *, parent: RecordRead | None = None
) -> list[PreviewEntry]:
    """Dry run of :func:`enforce_output_grids`: what it would refuse, touching nothing.

    Resolves and classifies the same pairs and consults the same
    :func:`decide` table, but carries nothing out. A pair the guard would
    repair (``conform`` on an exactly-repairable REARRANGED subject) passes
    here as it does there; a pair it would reject or delete is reported with
    the declared action and, for a reject, the reason; a pair that cannot be
    classified — unbound or missing reference, unreadable grid — is reported
    with the message its 409 would carry. Backs the read-only
    ``validate-files`` report (``report_record_files``).
    """
    registry = record.record_type.file_registry or []
    declared = _declared(registry)
    if not declared:
        return []

    by_name = {fd.name: fd for fd in registry}
    files = Files.for_reader(record, parent=parent)
    entries: list[PreviewEntry] = []

    for fd in declared:
        pair = await _resolve_pair(fd, by_name, files)
        if pair is None:
            continue
        if isinstance(pair, Unclassifiable):
            entries.append(PreviewEntry(fd.name, pair.message))
            continue
        decision = decide(fd.on_grid_mismatch, pair.verdict.kind, repairable=pair.repairable)
        message = _preview_message(decision, pair)
        if message is not None:
            entries.append(PreviewEntry(fd.name, message))

    return entries
