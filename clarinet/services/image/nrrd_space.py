"""NRRD ``space`` resolution: header fields to a voxel grid in LPS.

A pure function of a pynrrd header mapping: numpy + stdlib, no ``import nrrd``,
no disk. Both NRRD readers, :meth:`~clarinet.services.image.image.Image.read_nrrd`
and :meth:`~clarinet.services.image.layered_segmentation.LayeredSegmentation.read_header`,
resolve spacing, direction and origin through :func:`nrrd_grid_from_header`, the
one place the ``space`` rule is applied. The disk-side counterpart that stamps a
missing ``space`` onto a legacy file is :mod:`clarinet.services.image.nrrd_repair`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from clarinet.exceptions.domain import ImageReadError
from clarinet.services.image.grid import LAS_TO_LPS, LPS_TO_RAS

_IDENTITY = np.eye(3)
_IDENTITY.setflags(write=False)

_NRRD_SPACE_LPS = "left-posterior-superior"
_NRRD_SPACE_RAS = "right-anterior-superior"
_NRRD_SPACE_LAS = "left-anterior-superior"
# Accepted (lower-cased) spellings of the NRRD `space` field -> canonical full name.
_NRRD_SPACE_CANONICAL: Mapping[str, str] = {
    _NRRD_SPACE_LPS: _NRRD_SPACE_LPS,
    "lps": _NRRD_SPACE_LPS,
    _NRRD_SPACE_RAS: _NRRD_SPACE_RAS,
    "ras": _NRRD_SPACE_RAS,
    _NRRD_SPACE_LAS: _NRRD_SPACE_LAS,
    "las": _NRRD_SPACE_LAS,
}
# Canonical space -> 3x3 world transform into LPS (all three are self-inverse).
# The RAS/LAS entries are grid's shared constants, handed out by reference by
# nrrd_space_transform, which is why they are frozen there.
_NRRD_SPACE_TO_LPS: Mapping[str, npt.NDArray[np.float64]] = {
    _NRRD_SPACE_LPS: _IDENTITY,
    _NRRD_SPACE_RAS: LPS_TO_RAS,
    _NRRD_SPACE_LAS: LAS_TO_LPS,
}


def canonical_nrrd_space(space: str | None) -> str | None:
    """Canonical full name for an accepted ``space`` spelling; ``None`` if unsupported.

    Package-internal: reached through :func:`nrrd_grid_from_header`, the facade's
    public resolver. Not exported from ``clarinet.services.image``.
    """
    return _NRRD_SPACE_CANONICAL.get((space or "").strip().lower())


def nrrd_space_transform(space: str | None, source: Path | str | None = None) -> np.ndarray:
    """3x3 world-coordinate transform taking the header's ``space`` into LPS.

    Identity for LPS; the diagonal X/Y (RAS) or Y (LAS) sign flip otherwise.
    Package-internal: reached through :func:`nrrd_grid_from_header`, the facade's
    public resolver. Not exported from ``clarinet.services.image``.

    Returns a shared read-only constant, not a fresh array — matmul it, and
    copy first if you need to write into the result.

    Args:
        space: The header's ``space`` field (``None`` if the header omits it).
        source: File the header came from, named in the error. Optional only
            because the transform itself does not need it — pass it whenever a
            path is in hand, or an operator sweeping a storage tree gets a
            verdict with no filename.

    Raises:
        ImageReadError: ``space`` is missing or not one of LPS/RAS/LAS.
    """
    canonical = canonical_nrrd_space(space)
    if canonical is not None:
        return _NRRD_SPACE_TO_LPS[canonical]
    where = f"{source}: " if source is not None else ""
    if not (space or "").strip():
        raise ImageReadError(
            f"{where}NRRD header has no `space` field, so its `space directions`/`space "
            "origin` cannot be placed in LPS. If the file is known to be LPS (every "
            "clarinet- or Slicer-written NRRD is), stamp it once with "
            "declare_nrrd_space(path, 'left-posterior-superior')"
        )
    raise ImageReadError(
        f"{where}Unsupported NRRD space {space!r}: expected left-posterior-superior/LPS, "
        "right-anterior-superior/RAS, or left-anterior-superior/LAS"
    )


def nrrd_space_to_lps(
    space: str | None,
    space_directions: np.ndarray,
    space_origin: np.ndarray | None,
    source: Path | str | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Convert NRRD ``space directions``/``space origin`` into clarinet's internal LPS.

    Honors the header's ``space`` field (case-insensitive; full name or abbreviation,
    e.g. ``"right-anterior-superior"`` or ``"RAS"``). LPS passes through unchanged;
    RAS/LAS are converted by negating the affected world components of every direction
    row and of the origin (same transform for both — they express vectors/points in the
    same declared coordinate system). Slicer always writes LPS (probe P6) — this only
    affects third-party files.

    Called by :func:`nrrd_grid_from_header`, which pre-slices ``space_directions`` to
    the 3 spatial rows (a 4-D layered header's row 0 is the ``none`` list axis, not a
    spatial direction). Package-internal: not exported from ``clarinet.services.image``.

    Args:
        space: The header's ``space`` field (``None`` if the header omits it).
        space_directions: ``(3, 3)`` array; each row is one axis's world-space
            direction vector (spacing baked in), expressed in the header's ``space``.
        space_origin: ``(3,)`` world-space origin in the header's ``space``, or
            ``None`` if the header has no ``space origin``.
        source: File the header came from, named in the error.

    Returns:
        ``(space_directions, space_origin)`` re-expressed in LPS. The second element
        is ``None`` iff *space_origin* was ``None``.

    Raises:
        ImageReadError: ``space`` is missing or not one of LPS/RAS/LAS.
    """
    transform = nrrd_space_transform(space, source)
    # Each space_directions row is a per-axis world vector [x, y, z]; post-multiplying
    # by the (diagonal) transform scales those world x/y/z *columns*, i.e. negates the
    # same components in every row. space_origin is a single such vector, pre-multiplied
    # for the conventional matrix-vector form (equivalent for a diagonal transform).
    dirs_lps = space_directions @ transform
    origin_lps = None if space_origin is None else transform @ space_origin
    return dirs_lps, origin_lps


@dataclass(frozen=True, slots=True)
class NrrdGrid:
    """Grid fields a NRRD header supplies, in LPS. ``None`` = the header omitted that
    field; the caller supplies its own default."""

    spacing: tuple[float, float, float] | None
    direction: npt.NDArray[np.float64] | None
    origin: tuple[float, float, float] | None


def nrrd_grid_from_header(
    header: Mapping[str, Any],
    source: Path | str | None = None,
    *,
    spatial: slice = slice(0, 3),
) -> NrrdGrid:
    """Spacing, direction and origin in LPS from a NRRD header.

    The one place the ``space`` rule is applied, shared by
    :meth:`~clarinet.services.image.image.Image.read_nrrd` (3-D, default *spatial*) and
    :meth:`~clarinet.services.image.layered_segmentation.LayeredSegmentation.read_header`
    (4-D, ``spatial=slice(1, 4)``). Exported from ``clarinet.services.image``: code that
    parses NRRD headers with pynrrd should place geometry through this function rather
    than reading ``space directions``/``space origin`` raw and assuming LPS. Keeping both
    readers on one implementation is the point: the two used to carry separate copies
    of this logic and drifted, which is how a `spacings` header ended up with its
    origin converted and its axes left in the declared space.

    ``spacings`` are per-axis magnitudes along the array axes **of the declared
    space**, so an implicitly axis-aligned header still needs the same conversion as
    an explicit ``space directions`` one — identity in RAS is not identity in LPS.
    The single exception is a header that declares no ``space`` and has no
    ``space origin``: there is nothing to place, so its ``spacings`` are taken as-is
    and no ``space`` is required.

    Warning:
        *spatial* is not validated against the header, and the arity guard lives in the
        callers, not here: ``Image.read_nrrd`` rejects a header whose ``sizes`` is not
        length 3 before it reaches this function. A direct caller has no such guard, and
        both ways of getting *spatial* wrong fail badly. Resolving a 4-D header with the
        default ``slice(0, 3)`` reads the ``none`` list-axis row (``[nan, nan, nan]``) as
        a spatial direction: the result is a grid with a ``nan`` spacing entry and a
        ``nan`` direction column, returned silently — no exception, no warning. A slice
        yielding fewer than three axes escapes as a bare ``IndexError`` (or a numpy
        ``ValueError`` from the matmul, for a ``spacings`` header in a declared space) —
        never ``ImageReadError``. Pass the slice that matches the header's ``dimension``.

    Args:
        header: A pynrrd header mapping (``nrrd.read_header`` / ``nrrd.read``).
        source: File the header came from, named in the error.
        spatial: Which rows of ``space directions`` / entries of ``spacings`` are
            the three spatial axes. Stated by the caller, not detected from
            ``dimension``: a 4-D layered header passes ``slice(1, 4)`` because
            its row 0 is the ``none`` list axis, while a 4-D time series would
            put its extra axis last.

    Raises:
        ImageReadError: the header carries geometry to place — ``space directions``,
            or a ``space origin`` — without a supported ``space`` field.
    """
    space = header.get("space")
    space_origin = header.get("space origin")
    raw_origin = None if space_origin is None else np.asarray(space_origin[:3], dtype=float)

    def _placed(dirs: npt.NDArray[np.float64], keep_spacing: bool) -> NrrdGrid:
        arr, origin = nrrd_space_to_lps(space, dirs, raw_origin, source)
        norms = np.linalg.norm(arr, axis=1)
        return NrrdGrid(
            spacing=(float(norms[0]), float(norms[1]), float(norms[2])) if keep_spacing else None,
            direction=(arr / norms[:, np.newaxis]).T,
            origin=None
            if origin is None
            else (float(origin[0]), float(origin[1]), float(origin[2])),
        )

    space_dirs = header.get("space directions")
    if space_dirs is not None:
        return _placed(np.asarray(space_dirs[spatial], dtype=float), keep_spacing=True)

    spacings = header.get("spacings")
    raw_spacing = None if spacings is None else [float(s) for s in spacings[spatial]]
    if raw_origin is None and not (space or "").strip():
        # Nothing to place and no space declared: axis-aligned, read as-is.
        return NrrdGrid(
            spacing=None
            if raw_spacing is None
            else (raw_spacing[0], raw_spacing[1], raw_spacing[2]),
            direction=None,
            origin=None,
        )
    return _placed(np.diag(raw_spacing or [1.0, 1.0, 1.0]), keep_spacing=raw_spacing is not None)
