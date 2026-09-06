"""One-time repair of NRRD headers that omit ``space``.

Disk-side counterpart of :mod:`clarinet.services.image.nrrd_space`: the resolver
there refuses geometry it cannot place, and :func:`declare_nrrd_space` here is the
sanctioned way to stamp the missing label onto a legacy file. Reads and writes go
through pynrrd directly, since ``Image`` is exactly what raises on such a file.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import nrrd

from clarinet.exceptions.domain import ImageError, ImageReadError, ImageWriteError
from clarinet.services.image.nrrd_space import canonical_nrrd_space
from clarinet.utils.logger import logger


def _nrrd_header_non_ascii(path: Path) -> str | None:
    """First non-ASCII header line of a NRRD, or ``None`` if the header is pure ASCII.

    Reads the raw bytes rather than pynrrd's parsed header, because pynrrd has already
    replaced the offending values with ``''`` by the time it returns them (#577). The
    NRRD header ends at the first blank line, so this never touches the voxel data.
    """
    with path.open("rb") as fh:
        for raw in fh:
            if not raw.strip():
                break
            try:
                raw.decode("ascii")
            except UnicodeDecodeError:
                return raw.decode("utf-8", errors="replace").strip()
    return None


def declare_nrrd_space(
    path: Path | str,
    space: str,
    *,
    out_path: Path | str | None = None,
) -> Path:
    """Stamp the NRRD ``space`` field on a file whose header omits it.

    One-time repair for legacy files — e.g. clarinet-written NRRDs from before
    2026-03-08 — that carry ``space directions``/``space origin`` without a
    ``space`` label, which the strict readers
    (:meth:`~clarinet.services.image.image.Image.read_nrrd`,
    :meth:`~clarinet.services.image.layered_segmentation.LayeredSegmentation.read_header`,
    ``read_grid``) refuse. The geometry is never touched or interpreted: the caller
    *declares* which coordinate system the numbers already are in, and the next read
    converts from it as usual — so declaring RAS on a RAS-native file is the explicit
    conversion path. Works on 3-D volumes and 4-D layered ``.seg.nrrd`` alike:
    segment metadata is carried through, with pynrrd's caveat that header values
    are ASCII-only, so non-ASCII bytes (e.g. a Cyrillic segment name) are dropped
    on any read/write round-trip (#577). Attached-data files only — a detached
    ``.nhdr`` header (or a header naming a ``data file``) is refused with
    ``ImageError`` before anything is written, since pynrrd would repoint it at a
    new data file and orphan the original. Reads and writes through pynrrd
    directly, since ``Image`` is exactly what raises on such a file; the write is
    atomic (per-call temp file + ``os.replace``), so a failed in-place stamp
    leaves the original bytes intact.

    Args:
        path: NRRD to repair.
        space: LPS, RAS or LAS — full name or abbreviation, case-insensitive; the
            header receives the canonical full spelling.
        out_path: Write the stamped copy here instead of rewriting *path* in place.

    Returns:
        The written path (*out_path* if given, else *path*). An in-place call on a
        file that already declares the same space returns without rewriting.

    Raises:
        ImageError: *space* is not LPS/RAS/LAS; the file is a detached-header NRRD;
            or the header already declares a *different* space — relabeling would
            change the meaning of the geometry rather than fill in a missing label,
            so that is refused. A blank ``space`` value counts as missing, exactly
            as the readers treat it.
        ImageReadError: the file cannot be read.
        ImageWriteError: the stamped file cannot be written.
    """
    path = Path(path)
    canonical = canonical_nrrd_space(space)
    if canonical is None:
        raise ImageError(
            f"Cannot declare NRRD space {space!r}: expected left-posterior-superior/LPS, "
            "right-anterior-superior/RAS, or left-anterior-superior/LAS"
        )
    target = path if out_path is None else Path(out_path)
    if ".nhdr" in (path.suffix.lower(), target.suffix.lower()):
        raise ImageError(
            f"{path}: detached-header NRRD (.nhdr) is not supported by declare_nrrd_space — "
            "pynrrd would repoint the header at a new data file and orphan the original; "
            "attached-data .nrrd files only"
        )
    try:
        header = nrrd.read_header(str(path))
    except Exception as e:
        raise ImageReadError(f"Failed to read NRRD header: {path}") from e
    if header.keys() & {"data file", "datafile"}:
        raise ImageError(
            f"{path}: header names a separate data file; detached-header NRRD is not "
            "supported by declare_nrrd_space — attached-data .nrrd files only"
        )

    # A blank `space:` value reads back as '' — missing, as the readers treat it.
    existing = (header.get("space") or "").strip()
    if existing:
        existing_canonical = canonical_nrrd_space(existing)
        if existing_canonical is None:
            raise ImageError(
                f"{path}: header declares space {existing!r}, which clarinet does not "
                f"support (LPS/RAS/LAS only). This helper fills in a *missing* label; it "
                f"will not reinterpret a declared one as {canonical!r}. If you know the "
                "geometry, clear the `space` field first (see the migration guide), then "
                "stamp it."
            )
        if existing_canonical != canonical:
            raise ImageError(
                f"{path}: header already declares space {existing!r}; refusing to relabel "
                f"it as {canonical!r} — that would change what its geometry means, not "
                "fill in a missing label"
            )
        if out_path is None:
            return path  # already declared: nothing to rewrite

    # pynrrd decodes header values as ASCII, so a non-ASCII segment name round-trips
    # to '' (#577). Refusing beats silently emptying it — the same standard the
    # detached-header guard above applies, and for the same reason: this helper
    # rewrites the user's only copy.
    non_ascii = _nrrd_header_non_ascii(path)
    if non_ascii is not None:
        raise ImageError(
            f"{path}: header contains non-ASCII bytes ({non_ascii!r}), which pynrrd drops "
            "on a read/write round-trip (#577) — declare_nrrd_space would empty them "
            "(e.g. a Cyrillic segment name) while stamping `space`. Add the `space` line "
            "with a text editor instead, or rename the segments to ASCII first."
        )

    try:
        data, header = nrrd.read(str(path))
    except Exception as e:
        raise ImageReadError(f"Failed to read NRRD file: {path}") from e
    header["space"] = canonical
    # Write beside the target, then atomically move over it: pynrrd truncates
    # its destination before writing, and for an in-place stamp the in-memory
    # `data` is the only other copy of the file — a failed write must never
    # have been aimed at the original. Same per-call-token idea as
    # grid_policy's repair temp (naming differs: that one keeps every suffix
    # and uses a `.repair.` prefix): a per-call token, so two concurrent
    # stamps of one file can neither install each other's partial bytes nor
    # unlink each other's temp. Keeps the real suffix so pynrrd treats the
    # temp file identically.
    tmp = target.with_name(f".{target.stem}.tmp-{uuid.uuid4().hex[:12]}{target.suffix}")
    try:
        nrrd.write(str(tmp), data, header)
        os.replace(tmp, target)
    except Exception as e:
        raise ImageWriteError(f"Failed to write NRRD file: {target}") from e
    finally:
        tmp.unlink(missing_ok=True)  # no-op once os.replace has moved it
    logger.info(f"Declared NRRD space {canonical!r}: {target}")
    return target
