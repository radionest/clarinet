"""DICOM series reader — loads a directory of DICOM slices into a 3D numpy volume."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import SimpleITK as sitk

from clarinet.exceptions.domain import ImageReadError
from clarinet.utils.logger import logger


def read_dicom_series(
    directory: Path,
) -> tuple[np.ndarray, tuple[float, float, float], tuple[float, float, float], np.ndarray]:
    """Read all DICOM files in a directory and stack them into a 3D volume.

    Uses SimpleITK/GDCM for robust handling of compressed, enhanced,
    and vendor-specific DICOM formats.

    Args:
        directory: Path to a directory containing DICOM files.

    Returns:
        Tuple of (3D numpy array shaped (rows, cols, slices),
        spacing (row, col, slice) in mm,
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

    # SimpleITK → internal convention mapping
    # Array: SimpleITK (z, y, x) → internal (rows=y, cols=x, slices=z)
    volume = np.transpose(sitk.GetArrayFromImage(image), (1, 2, 0))

    # Spacing: SimpleITK (x, y, z) → internal (row=y, col=x, slice=z)
    sx, sy, sz = image.GetSpacing()
    spacing = (sy, sx, sz)

    # Origin: (x, y, z) in LPS — matches internal convention
    ox, oy, oz = image.GetOrigin()
    origin = (float(ox), float(oy), float(oz))

    # Direction: SimpleITK columns (x, y, z) → internal columns (y, x, z).
    # Fancy indexing returns a non-contiguous view; force C-contiguous so
    # downstream consumers can rely on .tobytes() / C-extension passing.
    d = np.array(image.GetDirection()).reshape(3, 3)
    direction = np.ascontiguousarray(d[:, [1, 0, 2]])

    volume, origin, direction = _canonicalize_slice_axis(
        volume, spacing[2], origin, direction, directory.name
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
) -> tuple[np.ndarray, tuple[float, float, float], np.ndarray]:
    """Normalise the slice axis to point along the +sense of its dominant axis.

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

    Scope — this stabilizes *future* conversions. Segmentations that were frozen
    on a divergent earlier-epoch grid stay physically correct but still need
    ``clarinet.services.image.conform_seg_to_grid`` (PR #393) to re-align by index
    against a re-converted volume. The canonical frame may be left-handed (det < 0) for
    series whose in-plane axes oppose the slice normal — this is valid (NIfTI /
    NRRD / ITK all support a negative-determinant direction), matches the
    pre-#221 reader, and no framework consumer depends on a positive determinant.
    """
    slice_dir = direction[:, 2]
    # argmax resolves a perfectly diagonal (equal-magnitude) slice normal to the
    # first axis deterministically, so the canonical choice stays reproducible.
    dominant = int(np.argmax(np.abs(slice_dir)))
    if slice_dir[dominant] >= 0:
        return volume, origin, direction

    logger.info(
        f"Canonicalizing DICOM slice axis in {name} "
        f"(flipping slice direction {np.round(slice_dir, 3).tolist()} to +{dominant}-axis)"
    )
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
