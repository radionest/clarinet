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

from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
import pydicom
import SimpleITK as sitk

from clarinet.exceptions.domain import ImageError, ImageReadError
from clarinet.utils.logger import logger

_AXIAL_DOMINANCE_THRESHOLD = 0.8
"""Minimum |normal[2]| (DICOM ground-truth IOP normal's Z-component) required
to trust the axial-dominance check.

Below this the ground-truth IOP normal is not cleanly aligned with the physical
S/I axis (oblique gantry, or a coronal/sagittal series), so the check is not
meaningful and detection refuses to judge (raises OrientationUnverifiable). The
check is on magnitude only (sign-independent — D6's raw, unforced normal has the
same |Z|-component as the old head-forced one), and deliberately against the
DICOM ground truth, not the NIfTI's own affine — see module docstring."""


def _read_ipp(path: str) -> np.ndarray:
    """ImagePositionPatient of a single DICOM file as a float array (raises if absent)."""
    ds = pydicom.dcmread(path, stop_before_pixels=True)
    return np.array([float(v) for v in ds.ImagePositionPatient])


def ground_truth_slice_geometry(
    dicom_names: list[str],
    slice_spacing: float,
    origin: tuple[float, float, float],
    direction: np.ndarray,
) -> tuple[tuple[float, float, float], np.ndarray, tuple[float, float, float] | None]:
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

    Also returns the exact ``IPP_last`` (third element) when ground truth was
    established, else ``None``. Canonicalization uses this verbatim as the
    flipped origin instead of extrapolating ``slice_spacing * (n - 1)`` from a
    single nominal spacing value — exact even when inter-slice spacing wobbles
    across the series, which the extrapolation is not.

    Never raises: fewer than 2 files, missing/unreadable IPP, or a degenerate
    (near-zero) delta → log a warning and return ``(origin, direction, None)``
    unchanged. ``slice_spacing`` sets the degeneracy floor (``0.5 * spacing``).
    """
    if len(dicom_names) < 2:
        logger.warning(
            f"Fewer than 2 DICOM files ({len(dicom_names)}); leaving reader output unchanged"
        )
        return origin, direction, None
    try:
        ipp_first = _read_ipp(dicom_names[0])
        ipp_last = _read_ipp(dicom_names[-1])
    except Exception as exc:
        logger.warning(
            f"Could not read IPP ground truth for slice-axis correction ({exc}); "
            f"leaving reader output unchanged"
        )
        return origin, direction, None

    delta = ipp_last - ipp_first
    norm = float(np.linalg.norm(delta))
    if norm < 0.5 * slice_spacing:
        logger.warning(
            f"Degenerate slice-axis delta ({norm:.4f} mm across the series); "
            f"leaving reader output unchanged"
        )
        return origin, direction, None

    new_direction = np.ascontiguousarray(direction.copy())
    new_direction[:, 2] = delta / norm
    true_origin = (float(ipp_first[0]), float(ipp_first[1]), float(ipp_first[2]))
    exact_last_ipp = (float(ipp_last[0]), float(ipp_last[1]), float(ipp_last[2]))
    return true_origin, new_direction, exact_last_ipp


class OrientationUnverifiable(ImageError):
    """Ground-truth DICOM geometry could not be established for a series, so its
    volume's orientation can be neither confirmed nor refuted. Callers must treat
    this as "unknown" — never as "correct" — so a genuinely-flipped series is
    surfaced for review instead of silently passing detection."""


def _series_file_names(directory: Path) -> list[str]:
    """GDCM's file order for the first series in ``directory`` (raises if none)."""
    directory = Path(directory)
    reader = sitk.ImageSeriesReader()
    series_ids = reader.GetGDCMSeriesIDs(str(directory))
    if not series_ids:
        raise ImageReadError(f"No DICOM series found in {directory}")
    names = reader.GetGDCMSeriesFileNames(str(directory), series_ids[0])
    if not names:
        raise ImageReadError(f"No DICOM files in series {series_ids[0]} of {directory}")
    return list(names)


def _iop_normal_from_series(
    directory: Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (raw unit IOP normal in LPS, IPP_first, IPP_last) for a series.

    The normal is ``cross(row_cosines, col_cosines)`` from the first file's
    ``ImageOrientationPatient`` — *not* forced toward either side. Under D6 the
    converter's canonical slice sense already is whichever side this raw normal
    points to, so the expected origin (see ``is_volume_misoriented``) must be
    derived from it unforced. First/last are per GDCM's own sort order, read
    directly via pydicom.
    """
    dicom_names = _series_file_names(directory)
    first = pydicom.dcmread(dicom_names[0], stop_before_pixels=True)
    last = pydicom.dcmread(dicom_names[-1], stop_before_pixels=True)
    iop = [float(v) for v in first.ImageOrientationPatient]
    normal = np.cross(np.array(iop[0:3]), np.array(iop[3:6]))
    norm = float(np.linalg.norm(normal))
    if norm == 0.0:
        raise ValueError(f"degenerate ImageOrientationPatient {iop} (zero cross product)")
    unit_normal = normal / norm
    ipp_first = np.array([float(v) for v in first.ImagePositionPatient])
    ipp_last = np.array([float(v) for v in last.ImagePositionPatient])
    return unit_normal, ipp_first, ipp_last


def is_volume_misoriented(volume_nifti: Path, dicom_dir: Path) -> bool:
    """True iff the on-disk NIfTI's slice origin disagrees with the expected
    IOP-normal-side origin (i.e. it was produced by a pre-#453 or pre-D6 reader).

    Idempotent: a corrected/remediated volume returns False, so re-running a
    remediation script is safe. Reconstructs the origin from the NIfTI affine
    (RAS→LPS, mirroring ``_LPS_TO_RAS`` in ``image.py``) and compares it to the
    expected origin within ``0.5 * slice_spacing``. Under D6 the canonical slice
    sense is the side of the raw (unforced) IOP normal ``n``, so the expected
    origin is the IPP endpoint with the **smaller** projection onto ``n`` — the
    feet-end IPP when ``n`` points head-ward (the common case, unchanged from
    the pre-D6 detector), the opposite end for a negative-dominant-normal
    series. The "is this axial?" guard is checked against the DICOM ground
    truth (``normal``), never the NIfTI's own affine — see module docstring.

    Raises ``OrientationUnverifiable`` when ground truth cannot be established
    (unreadable/absent series, or a non-dominantly-axial series the sign check
    cannot judge) — never silently reports "not misoriented" in those cases.
    """
    nii: Any = nib.load(str(volume_nifti))
    affine = nii.affine
    spacing = tuple(float(z) for z in nii.header.get_zooms()[:3])
    lps = np.diag([-1.0, -1.0, 1.0])
    origin = np.asarray(lps @ affine[:3, 3], dtype=float)

    try:
        normal, ipp_first, ipp_last = _iop_normal_from_series(Path(dicom_dir))
    except Exception as exc:
        raise OrientationUnverifiable(
            f"cannot read ground-truth geometry for {dicom_dir}: {exc}"
        ) from exc

    if abs(normal[2]) < _AXIAL_DOMINANCE_THRESHOLD:
        raise OrientationUnverifiable(
            f"series {dicom_dir} is not dominantly axial (normal={np.round(normal, 3).tolist()})"
        )

    proj_first = float(np.dot(ipp_first, normal))
    proj_last = float(np.dot(ipp_last, normal))
    expected_origin = np.asarray(ipp_first if proj_first < proj_last else ipp_last, dtype=float)
    # 0.5 * spacing, not 1.0 * spacing: a full-slice-span tolerance sits exactly on the
    # boundary for an "origin one slice-span off" misorientation (np.allclose treats
    # |delta| == atol as equal), which would let that exact case slip past detection.
    return not np.allclose(origin, expected_origin, atol=0.5 * spacing[2])
