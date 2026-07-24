"""DICOM series reader — loads a directory of DICOM slices into a 3D numpy volume."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import SimpleITK as sitk

from clarinet.exceptions.domain import ImageReadError
from clarinet.services.image.orientation import ground_truth_slice_geometry
from clarinet.utils.logger import logger

_DEGENERATE_NORMAL_EPS = 1e-6
"""Threshold for the IOP-normal degeneracy checks in ``_canonicalize_slice_axis``:
below this either the in-plane columns don't yield a meaningful cross product
(``|n|``) or the slice axis is too close to orthogonal to ``n`` to trust
``dot(slice_dir, n)``'s sign. Both fall back to the +dominant-axis rule."""


def read_dicom_series(
    directory: Path,
) -> tuple[np.ndarray, tuple[float, float, float], tuple[float, float, float], np.ndarray]:
    """Read all DICOM files in a directory and stack them into a 3D volume.

    Uses SimpleITK/GDCM for robust handling of compressed, enhanced,
    and vendor-specific DICOM formats.

    Args:
        directory: Path to a directory containing DICOM files.

    Returns:
        Tuple of (3D numpy array in DICOM IOP in-plane order, shaped like
        SimpleITK's own ``GetSize()`` (x, y, z),
        spacing (x, y, z) in mm,
        origin (x, y, z) in LPS,
        direction 3x3 matrix — columns correspond to numpy axes).

    Raises:
        ImageReadError: If the directory is empty or contains no valid DICOM files.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ImageReadError(f"Not a directory: {directory}")

    reader = sitk.ImageSeriesReader()
    series_ids = reader.GetGDCMSeriesIDs(str(directory))
    if not series_ids:
        raise ImageReadError(f"No DICOM files found in {directory}")

    selected_series = series_ids[0]
    if len(series_ids) > 1:
        logger.warning(
            f"Multiple DICOM series in {directory} ({len(series_ids)} found), "
            f"reading only the first: {selected_series}"
        )

    dicom_names = reader.GetGDCMSeriesFileNames(str(directory), selected_series)
    if not dicom_names:
        raise ImageReadError(f"No DICOM files in series {selected_series} of {directory}")

    reader.SetFileNames(dicom_names)
    try:
        image = reader.Execute()
    except RuntimeError as e:
        raise ImageReadError(f"Failed to read DICOM series from {directory}: {e}") from e

    # SimpleITK → internal convention mapping (DICOM IOP in-plane order — no
    # in-plane row/column swap; only the slice axis, column 2, is adjusted below).
    # Array: SimpleITK (z, y, x) → internal (x, y, z) — matches GetSize() order.
    volume = np.transpose(sitk.GetArrayFromImage(image), (2, 1, 0))

    # Spacing: SimpleITK (x, y, z) — used as-is, matches the array above.
    sx, sy, sz = image.GetSpacing()
    spacing = (sx, sy, sz)

    # Origin: (x, y, z) in LPS — matches internal convention
    ox, oy, oz = image.GetOrigin()
    origin = (float(ox), float(oy), float(oz))

    # Direction: SimpleITK columns (x, y, z) — used as-is; column i already
    # matches array axis i, so no reorder is needed. reshape() already returns a
    # fresh contiguous array; ascontiguousarray is kept defensively so downstream
    # consumers can still rely on .tobytes() / C-extension passing.
    d = np.array(image.GetDirection()).reshape(3, 3)
    direction = np.ascontiguousarray(d)

    origin, direction, exact_last_ipp = ground_truth_slice_geometry(
        dicom_names, spacing[2], origin, direction
    )
    volume, origin, direction = _canonicalize_slice_axis(
        volume, spacing[2], origin, direction, directory.name, exact_last_ipp
    )

    logger.debug(
        f"Read {len(dicom_names)} DICOM slices from {directory.name}: "
        f"shape={volume.shape}, spacing={spacing}"
    )
    return volume, spacing, origin, direction


def _canonicalize_slice_axis(
    volume: np.ndarray,
    slice_spacing: float,
    origin: tuple[float, float, float],
    direction: np.ndarray,
    name: str,
    exact_last_position: tuple[float, float, float] | None = None,
) -> tuple[np.ndarray, tuple[float, float, float], np.ndarray]:
    """Normalise the slice axis to the canonical IOP-normal side.

    DICOM→NIfTI conversion must be reproducible across framework versions: a series
    re-converted later (repair, anonymization path migration, manual re-run) must
    land on the *identical* voxel grid. Otherwise a segmentation frozen on the old
    grid and one frozen on the new grid sit on physically equivalent but
    index-reversed grids, and any index-wise overlay of the two reads as zero
    overlap (the projection/doctor-seg Z-flip).

    The slice-ordering convention drifted when the reader switched from a
    hand-written pydicom reader (sorted by ascending ``ImagePositionPatient[2]``)
    to SimpleITK (sorted along the IOP slice normal, which may point either way).
    Flipping the slice axis to a single canonical sense restores a stable
    convention for every reader/version.

    The flip is **geometry-preserving**: the array, ``origin`` and the slice
    direction are reversed *together*, so every voxel keeps its physical position
    (unlike a direction-only flip, which mirrors the data through the origin
    plane). No-op when the slice axis already points the canonical way.

    Canonical sense (D6): the slice axis is flipped, when needed, to align with
    the IOP normal ``n = cross(direction[:, 0], direction[:, 1])`` — the physical
    normal implied by the in-plane row/column directions — rather than with a
    fixed +dominant-axis convention. Since ``det([r, c, s]) = n·s``, this makes
    the emitted determinant **positive for every series with a non-degenerate
    IOP**, including ones whose IOP normal itself has a negative-dominant
    component (where the old +dominant rule would have landed on ``det < 0``).
    The flip stays the geometry-preserving index rearrangement described above —
    never a direction-only mirror, which remains forbidden (the #247/#453 bug
    class). #412's real invariant was always version-stability plus
    geometry-preservation of the slice axis, not the +dominant-axis anchor
    itself; re-anchoring to the IOP-normal side is compatible with it and
    removes the residual left-handed population the old anchor left behind.
    When the in-plane columns are degenerate/unavailable (``|n| ≈ 0``) or the
    slice axis is nearly orthogonal to ``n`` (``|dot(slice_dir, n)| ≈ 0``, so its
    side cannot be judged reliably), falls back to the +dominant-axis rule and
    logs a warning — conversion never fails on this.

    Scope — this stabilizes *future* conversions. Segmentations that were frozen
    on a divergent earlier-epoch grid stay physically correct but still need
    ``clarinet.services.image.conform_seg_to_grid`` (PR #393) to re-align by index
    against a re-converted volume.

    ``exact_last_position``, when given (DICOM IPP ground truth was established
    by ``ground_truth_slice_geometry``), is used verbatim as the flipped origin
    instead of extrapolating ``slice_spacing * (n_slices - 1)`` from a single
    nominal spacing value — exact even when inter-slice spacing wobbles across
    the series, which the extrapolation is not. Falls back to the extrapolation
    when ``None`` (ground truth unavailable).
    """
    slice_dir = direction[:, 2]
    # n = the physical IOP normal implied by the in-plane row/column directions
    # (post-Task-7, direction[:, 0:2] are IOP-ordered, so this is exactly the
    # DICOM normal). Canonical sense (D6) = the side of n, not a fixed axis.
    n = np.cross(direction[:, 0], direction[:, 1])
    n_norm = float(np.linalg.norm(n))
    dot_n = float(np.dot(slice_dir, n))
    # argmax resolves a perfectly diagonal (equal-magnitude) slice normal to the
    # first axis deterministically, so the fallback choice stays reproducible.
    dominant = int(np.argmax(np.abs(slice_dir)))
    if n_norm < _DEGENERATE_NORMAL_EPS or abs(dot_n) < _DEGENERATE_NORMAL_EPS * n_norm:
        logger.warning(
            f"Degenerate/near-in-plane IOP normal in {name} "
            f"(n={np.round(n, 3).tolist()}, |n|={n_norm:.3g}, dot={dot_n:.3g}); "
            f"falling back to the +dominant-axis rule for slice-sense canonicalization"
        )
        flip = slice_dir[dominant] < 0
    else:
        flip = dot_n < 0

    if not flip:
        return volume, origin, direction

    logger.info(
        f"Canonicalizing DICOM slice axis in {name} "
        f"(flipping slice direction {np.round(slice_dir, 3).tolist()})"
    )
    if exact_last_position is not None:
        new_origin = np.asarray(exact_last_position, dtype=float)
    else:
        n_slices = volume.shape[2]
        new_origin = np.asarray(origin, dtype=float) + slice_dir * slice_spacing * (n_slices - 1)
    new_volume = np.ascontiguousarray(volume[:, :, ::-1])
    new_direction = direction.copy()
    new_direction[:, 2] = -new_direction[:, 2]
    return (
        new_volume,
        (float(new_origin[0]), float(new_origin[1]), float(new_origin[2])),
        new_direction,
    )
