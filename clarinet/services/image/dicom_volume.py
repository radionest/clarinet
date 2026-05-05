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
    # SimpleITK GDCM can return a left-handed direction matrix on some series
    # (observed on Philips iDose CT) — slice direction has the wrong sign so
    # TransformIndexToPhysicalPoint disagrees with the actual array ordering,
    # producing a SI flip in saved NIfTI. Force right-handed by recomputing
    # the slice axis from the cross product of the row/column axes; this is
    # a no-op for series that already have a consistent direction matrix.
    # Assumes slice axis is approximately ±(row x col); for gantry-tilted CT
    # this would drop the tilt component (rare on modern scanners).
    det = np.linalg.det(d)
    if det < 0:
        slice_from_cross = np.cross(d[:, 0], d[:, 1])
        if np.dot(slice_from_cross, d[:, 2]) < 0:
            logger.debug(
                f"Left-handed DICOM direction matrix in {directory.name} "
                f"(det={det:.3f}); recomputing slice axis from row x col"
            )
            d[:, 2] = slice_from_cross
        else:
            logger.warning(
                f"Left-handed direction matrix in {directory.name} "
                f"(det={det:.3f}) with consistent slice axis — leaving as-is"
            )
    direction = np.ascontiguousarray(d[:, [1, 0, 2]])

    logger.debug(
        f"Read {len(dicom_names)} DICOM slices from {directory.name}: "
        f"shape={volume.shape}, spacing={spacing}"
    )
    return volume, spacing, origin, direction
