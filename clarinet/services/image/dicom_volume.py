"""DICOM series reader — loads a directory of DICOM slices into a 3D numpy volume."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pydicom

from clarinet.exceptions.domain import ImageReadError
from clarinet.utils.logger import logger


def read_dicom_series(directory: Path) -> tuple[np.ndarray, tuple[float, float, float]]:
    """Read all DICOM files in a directory and stack them into a 3D volume.

    Args:
        directory: Path to a directory containing .dcm files.

    Returns:
        Tuple of (3D numpy array, spacing as (row, col, slice) in mm).

    Raises:
        ImageReadError: If the directory is empty, contains no valid DICOM files,
            or slices have inconsistent dimensions.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ImageReadError(f"Not a directory: {directory}")

    dcm_files = sorted(directory.glob("*.dcm"))
    if not dcm_files:
        # Also try files without .dcm extension (common in PACS exports)
        dcm_files = [
            f for f in sorted(directory.iterdir()) if f.is_file() and not f.name.startswith(".")
        ]

    if not dcm_files:
        raise ImageReadError(f"No DICOM files found in {directory}")

    datasets = []
    for f in dcm_files:
        try:
            ds = pydicom.dcmread(str(f))
            if hasattr(ds, "pixel_array"):
                datasets.append(ds)
        except Exception:
            logger.debug(f"Skipping non-DICOM file: {f.name}")

    if not datasets:
        raise ImageReadError(f"No valid DICOM files with pixel data in {directory}")

    sorted_datasets: Sequence[pydicom.Dataset] = _sort_slices(datasets)
    spacing = _extract_spacing(sorted_datasets)

    try:
        volume = np.stack([ds.pixel_array for ds in sorted_datasets], axis=-1)
    except ValueError as e:
        raise ImageReadError(f"Inconsistent slice dimensions in {directory}") from e

    logger.debug(
        f"Read {len(sorted_datasets)} DICOM slices from {directory.name}: "
        f"shape={volume.shape}, spacing={spacing}"
    )
    return volume, spacing


def _sort_slices(datasets: Sequence[pydicom.Dataset]) -> list[pydicom.Dataset]:
    """Sort DICOM datasets by slice position.

    Primary sort: ImagePositionPatient Z-coordinate.
    Fallback: InstanceNumber.
    """
    try:
        return sorted(datasets, key=lambda ds: float(ds.ImagePositionPatient[2]))
    except (AttributeError, TypeError, IndexError):
        logger.debug("ImagePositionPatient unavailable, falling back to InstanceNumber")
        try:
            return sorted(datasets, key=lambda ds: int(ds.InstanceNumber))
        except (AttributeError, TypeError):
            logger.warning("Cannot determine slice order — using file order")
            return list(datasets)


def _extract_spacing(datasets: Sequence[pydicom.Dataset]) -> tuple[float, float, float]:
    """Extract voxel spacing from DICOM datasets.

    Row and column spacing come from PixelSpacing.
    Slice spacing is computed from ImagePositionPatient if available,
    otherwise from SliceThickness.

    Returns:
        (row_spacing, col_spacing, slice_spacing) in mm.
    """
    ds0 = datasets[0]

    # Row/col spacing
    try:
        pixel_spacing = ds0.PixelSpacing
        row_sp, col_sp = float(pixel_spacing[0]), float(pixel_spacing[1])
    except (AttributeError, IndexError):
        logger.warning("PixelSpacing not found, defaulting to 1.0")
        row_sp, col_sp = 1.0, 1.0

    # Slice spacing: prefer computed from positions
    slice_sp = 1.0
    if len(datasets) > 1:
        try:
            z0 = float(datasets[0].ImagePositionPatient[2])
            z1 = float(datasets[1].ImagePositionPatient[2])
            slice_sp = abs(z1 - z0)
        except (AttributeError, TypeError, IndexError):
            pass

    if slice_sp == 0.0 or (slice_sp == 1.0 and len(datasets) > 1):
        try:
            slice_sp = float(ds0.SliceThickness)
        except (AttributeError, TypeError):
            logger.warning("SliceThickness not found, defaulting to 1.0")
            slice_sp = 1.0

    return (row_sp, col_sp, slice_sp)
