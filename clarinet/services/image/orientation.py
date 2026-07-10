"""Ground-truth DICOM slice-axis geometry from raw tags.

SimpleITK/GDCM's ``ImageSeriesReader`` can return an internally-inconsistent
result on long axial series with sub-mm spacing wobble: ``GetOrigin()`` matches
the first file's physical position, but ``GetDirection()``'s slice-axis sign can
contradict the actual GDCM file order (clarinet #453), producing an anatomically
flipped volume. This module recomputes the correct slice-axis sense and origin
directly from ``ImagePositionPatient`` (read via pydicom, independent of
SimpleITK) so ``dicom_volume.read_dicom_series`` can trust physical geometry, not
SimpleITK's direction sign. It never imports ``dicom_volume`` (no import cycle).
"""

from __future__ import annotations

import numpy as np
import pydicom

from clarinet.utils.logger import logger


def _read_ipp(path: str) -> np.ndarray:
    """ImagePositionPatient of a single DICOM file as a float array (raises if absent)."""
    ds = pydicom.dcmread(path, stop_before_pixels=True)
    return np.array([float(v) for v in ds.ImagePositionPatient])


def ground_truth_slice_geometry(
    dicom_names: list[str],
    slice_spacing: float,
    origin: tuple[float, float, float],
    direction: np.ndarray,
) -> tuple[tuple[float, float, float], np.ndarray]:
    """Override the slice-axis column and origin from IPP ground truth.

    ``dicom_names`` is the exact file order the reader passed to
    ``SetFileNames`` (array slice ``k`` ⇔ ``dicom_names[k]``), so
    ``normalize(IPP_last - IPP_first)`` is the true physical direction of
    increasing slice index and ``IPP_first`` is the true origin. Replaces
    ``direction`` column 2 with that unit vector and ``origin`` with
    ``IPP_first``; in-plane columns (from IOP) are left untouched.

    For a series SimpleITK read correctly these equal the values it already
    reported, so the canonical grid is byte-identical. Only a series whose
    reported direction sign contradicts its own IPP progression (the #453 bug)
    is changed.

    Never raises: fewer than 2 files, missing/unreadable IPP, or a degenerate
    (near-zero) delta → log a warning and return ``(origin, direction)``
    unchanged. ``slice_spacing`` sets the degeneracy floor (``0.5 * spacing``).
    """
    if len(dicom_names) < 2:
        return origin, direction
    try:
        ipp_first = _read_ipp(dicom_names[0])
        ipp_last = _read_ipp(dicom_names[-1])
    except Exception as exc:
        logger.warning(
            f"Could not read IPP ground truth for slice-axis correction ({exc}); "
            f"leaving reader output unchanged"
        )
        return origin, direction

    delta = ipp_last - ipp_first
    norm = float(np.linalg.norm(delta))
    if norm < 0.5 * slice_spacing:
        logger.warning(
            f"Degenerate slice-axis delta ({norm:.4f} mm across the series); "
            f"leaving reader output unchanged"
        )
        return origin, direction

    new_direction = np.ascontiguousarray(direction.copy())
    new_direction[:, 2] = delta / norm
    true_origin = (float(ipp_first[0]), float(ipp_first[1]), float(ipp_first[2]))
    return true_origin, new_direction
