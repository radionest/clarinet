"""DICOM series reader — loads a directory of DICOM slices into a 3D numpy volume."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pydicom

from clarinet.exceptions.domain import ImageReadError
from clarinet.utils.logger import logger


def read_dicom_series(
    directory: Path,
) -> tuple[np.ndarray, tuple[float, float, float], tuple[float, float, float], np.ndarray]:
    """Read all DICOM files in a directory and stack them into a 3D volume.

    Args:
        directory: Path to a directory containing .dcm files.

    Returns:
        Tuple of (3D numpy array, spacing (row, col, slice) in mm,
        origin (x, y, z), direction 3x3 matrix).

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
    origin, direction = _extract_orientation(sorted_datasets)

    try:
        volume = np.stack([ds.pixel_array for ds in sorted_datasets], axis=-1)
    except ValueError as e:
        raise ImageReadError(f"Inconsistent slice dimensions in {directory}") from e

    logger.debug(
        f"Read {len(sorted_datasets)} DICOM slices from {directory.name}: "
        f"shape={volume.shape}, spacing={spacing}"
    )
    return volume, spacing, origin, direction


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


def _extract_orientation(
    datasets: Sequence[pydicom.Dataset],
) -> tuple[tuple[float, float, float], np.ndarray]:
    """Extract patient origin and direction cosines from DICOM datasets.

    Args:
        datasets: Sorted DICOM datasets (at least one).

    Returns:
        Tuple of (origin (x, y, z), 3x3 direction matrix with columns = axis directions).
    """
    ds0 = datasets[0]

    # Origin from first slice
    try:
        ipp = ds0.ImagePositionPatient
        origin = (float(ipp[0]), float(ipp[1]), float(ipp[2]))
    except (AttributeError, TypeError, IndexError):
        origin = (0.0, 0.0, 0.0)

    # Direction from ImageOrientationPatient
    direction = np.eye(3)
    try:
        iop = [float(v) for v in ds0.ImageOrientationPatient]
        row_dir = np.array(iop[:3])
        col_dir = np.array(iop[3:6])

        # Slice direction: prefer computed from multi-slice positions
        if len(datasets) > 1:
            try:
                pos0 = np.array([float(v) for v in datasets[0].ImagePositionPatient])
                pos1 = np.array([float(v) for v in datasets[1].ImagePositionPatient])
                slice_dir = pos1 - pos0
                norm = np.linalg.norm(slice_dir)
                slice_dir = slice_dir / norm if norm > 0 else np.cross(row_dir, col_dir)
            except (AttributeError, TypeError, IndexError):
                slice_dir = np.cross(row_dir, col_dir)
        else:
            slice_dir = np.cross(row_dir, col_dir)

        direction = np.column_stack([row_dir, col_dir, slice_dir])
    except (AttributeError, TypeError, IndexError):
        pass

    return origin, direction
