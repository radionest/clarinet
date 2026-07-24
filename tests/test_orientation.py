"""Tests for clarinet.services.image.orientation — ground-truth DICOM slice geometry."""

from pathlib import Path

import nibabel
import numpy as np
import pydicom
import pydicom.uid
import pytest
from pydicom.dataset import FileDataset

from clarinet.services.image.orientation import (
    OrientationUnverifiable,
    ground_truth_slice_geometry,
    is_volume_misoriented,
)


def _write_slice(path: Path, ipp: tuple[float, float, float], iop=(1, 0, 0, 0, 1, 0), suid=None):
    """Write one minimal axial DICOM file with a given ImagePositionPatient."""
    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
    file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.SeriesInstanceUID = suid or pydicom.uid.generate_uid()
    ds.Rows, ds.Columns = 4, 5
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelSpacing = [1.0, 1.0]
    ds.SliceThickness = 3.0
    ds.ImageOrientationPatient = list(iop)
    ds.ImagePositionPatient = list(ipp)
    ds.PixelData = np.zeros((4, 5), dtype=np.uint16).tobytes()
    pydicom.dcmwrite(str(path), ds)


def _series(tmp_path: Path, name: str, z_values, iop=(1, 0, 0, 0, 1, 0)) -> list[str]:
    """Write a series (one file per Z in the given order) and return the file paths in that order."""
    d = tmp_path / name
    d.mkdir()
    suid = pydicom.uid.generate_uid()
    paths = []
    for k, z in enumerate(z_values):
        p = d / f"slice_{k:03d}.dcm"
        _write_slice(p, (0.0, 0.0, float(z)), iop=iop, suid=suid)
        paths.append(str(p))
    return paths


IDENTITY = np.eye(3)


class TestGroundTruthSliceGeometry:
    def test_ascending_ipp_overrides_slice_column_with_wrong_direction(self, tmp_path):
        names = _series(tmp_path, "asc", [0.0, 5.0, 10.0])
        wrong = IDENTITY.copy()
        wrong[:, 2] = [0.0, 0.0, -1.0]  # SimpleITK reported the wrong sign
        origin, direction, exact_last_ipp = ground_truth_slice_geometry(
            names, 5.0, (99.0, 99.0, 99.0), wrong
        )
        np.testing.assert_allclose(direction[:, 2], [0.0, 0.0, 1.0], atol=1e-6)
        np.testing.assert_allclose(origin, [0.0, 0.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(exact_last_ipp, [0.0, 0.0, 10.0], atol=1e-6)

    def test_descending_ipp_direction_points_negative(self, tmp_path):
        names = _series(tmp_path, "desc", [10.0, 5.0, 0.0])
        origin, direction, exact_last_ipp = ground_truth_slice_geometry(
            names, 5.0, (0.0, 0.0, 10.0), IDENTITY.copy()
        )
        np.testing.assert_allclose(direction[:, 2], [0.0, 0.0, -1.0], atol=1e-6)
        np.testing.assert_allclose(origin, [0.0, 0.0, 10.0], atol=1e-6)
        np.testing.assert_allclose(exact_last_ipp, [0.0, 0.0, 0.0], atol=1e-6)

    def test_in_plane_columns_preserved(self, tmp_path):
        names = _series(tmp_path, "inplane", [0.0, 3.0])
        src = np.array([[1.0, 0.0, 9.0], [0.0, -1.0, 9.0], [0.0, 0.0, 9.0]])
        _, direction, _ = ground_truth_slice_geometry(names, 3.0, (0.0, 0.0, 0.0), src)
        np.testing.assert_allclose(direction[:, 0], src[:, 0], atol=1e-9)
        np.testing.assert_allclose(direction[:, 1], src[:, 1], atol=1e-9)

    def test_fewer_than_two_files_returns_unchanged(self, tmp_path):
        names = _series(tmp_path, "single", [0.0])
        o, d = (1.0, 2.0, 3.0), IDENTITY.copy()
        origin, direction, exact_last_ipp = ground_truth_slice_geometry(names, 3.0, o, d)
        assert origin is o and direction is d
        assert exact_last_ipp is None

    def test_missing_ipp_returns_unchanged(self, tmp_path):
        d = tmp_path / "noipp"
        d.mkdir()
        for k in range(2):
            p = d / f"s{k}.dcm"
            _write_slice(p, (0.0, 0.0, float(k)))
            ds = pydicom.dcmread(str(p))
            del ds.ImagePositionPatient
            ds.save_as(str(p))
        names = [str(d / "s0.dcm"), str(d / "s1.dcm")]
        o, dirn = (7.0, 8.0, 9.0), IDENTITY.copy()
        origin, direction, exact_last_ipp = ground_truth_slice_geometry(names, 3.0, o, dirn)
        assert origin is o and direction is dirn
        assert exact_last_ipp is None

    def test_degenerate_delta_returns_unchanged(self, tmp_path):
        names = _series(tmp_path, "degen", [4.0, 4.0])  # identical IPP → zero delta
        o, dirn = (4.0, 4.0, 4.0), IDENTITY.copy()
        origin, direction, exact_last_ipp = ground_truth_slice_geometry(names, 3.0, o, dirn)
        assert origin is o and direction is dirn
        assert exact_last_ipp is None


def _nifti_with_lps_origin(path: Path, lps_origin, spacing=(1.0, 1.0, 3.0)):
    """Write a NIfTI whose reconstructed LPS origin equals lps_origin and whose
    slice column is +Z (dominant axis 2). affine[:3,3] (RAS) = LPS->RAS of origin."""
    sx, sy, sz = spacing
    affine = np.array(
        [
            [-sx, 0.0, 0.0, -lps_origin[0]],
            [0.0, -sy, 0.0, -lps_origin[1]],
            [0.0, 0.0, sz, lps_origin[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    data = np.zeros((4, 5, 3), dtype=np.int16)
    nibabel.save(nibabel.Nifti1Image(data, affine, dtype=np.int16), str(path))


def _nifti_coronal_with_lps_origin(path: Path, lps_origin, spacing=(1.0, 1.0, 3.0)):
    """Write a NIfTI whose reconstructed LPS origin equals lps_origin and whose
    slice column is +Y (dominant axis 1) — a genuinely coronal-shaped volume,
    unlike ``_nifti_with_lps_origin`` which is always axis-2 (axial)."""
    sx, sy, sz = spacing
    affine = np.array(
        [
            [-sx, 0.0, 0.0, -lps_origin[0]],
            [0.0, 0.0, -sz, -lps_origin[1]],
            [0.0, -sy, 0.0, lps_origin[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    data = np.zeros((4, 3, 5), dtype=np.int16)
    nibabel.save(nibabel.Nifti1Image(data, affine, dtype=np.int16), str(path))


class TestIsVolumeMisoriented:
    def test_correct_volume_not_misoriented(self, tmp_path):
        # Axial +Z series at Z = 0, 3, 6; feet end (min Z) = 0.
        _series(tmp_path, "dcm", [0.0, 3.0, 6.0])
        nii = tmp_path / "correct.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 0.0))  # origin at feet
        assert is_volume_misoriented(nii, tmp_path / "dcm") is False

    def test_flipped_volume_misoriented(self, tmp_path):
        _series(tmp_path, "dcm", [0.0, 3.0, 6.0])
        nii = tmp_path / "flipped.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 6.0))  # origin at head end → wrong
        assert is_volume_misoriented(nii, tmp_path / "dcm") is True

    def test_idempotent_after_correction(self, tmp_path):
        _series(tmp_path, "dcm", [0.0, 3.0, 6.0])
        nii = tmp_path / "remediated.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 0.0))  # a corrected volume
        assert is_volume_misoriented(nii, tmp_path / "dcm") is False

    def test_one_slice_off_is_detected(self, tmp_path):
        # Origin off by exactly one slice spacing (3 mm) — must exceed the 0.5*spacing tol.
        _series(tmp_path, "dcm", [0.0, 3.0, 6.0])
        nii = tmp_path / "off.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 3.0))
        assert is_volume_misoriented(nii, tmp_path / "dcm") is True

    def test_non_axial_series_raises(self, tmp_path):
        # Coronal series: IOP normal along +Y, so head_dir[2] ≈ 0 < threshold.
        # The NIfTI is genuinely coronal too (slice column +Y, dominant axis 1) —
        # a self-consistent conversion, not an axial-shaped fixture that would
        # mask a guard bug keying off the NIfTI's own axis instead of the DICOM
        # ground truth.
        _series(tmp_path, "cor", [0.0, 3.0, 6.0], iop=(1, 0, 0, 0, 0, -1))
        nii = tmp_path / "any.nii.gz"
        _nifti_coronal_with_lps_origin(nii, (0.0, 0.0, 0.0))
        with pytest.raises(OrientationUnverifiable):
            is_volume_misoriented(nii, tmp_path / "cor")

    def test_unreadable_series_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        nii = tmp_path / "any.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 0.0))
        with pytest.raises(OrientationUnverifiable):
            is_volume_misoriented(nii, empty)

    def test_degenerate_iop_raises(self, tmp_path):
        # Row == column direction cosines: cross product is zero, so the slice
        # normal cannot be computed at all (degenerate/malformed IOP).
        _series(tmp_path, "degeniop", [0.0, 3.0, 6.0], iop=(1, 0, 0, 1, 0, 0))
        nii = tmp_path / "any.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 0.0))
        with pytest.raises(OrientationUnverifiable):
            is_volume_misoriented(nii, tmp_path / "degeniop")

    def test_negative_dominant_normal_canonical_not_misoriented(self, tmp_path):
        # row=(1,0,0), col=(0,-1,0) -> n=(0,0,-1) (negative-dominant normal).
        # Under D6 the expected origin is the IPP endpoint with the SMALLER
        # projection onto the raw n — since n points -Z, that is the endpoint
        # with the LARGER Z (opposite of the head-forced pre-D6 selection).
        _series(tmp_path, "dcm", [0.0, 3.0, 6.0], iop=(1, 0, 0, 0, -1, 0))
        nii = tmp_path / "canonical.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 6.0))
        assert is_volume_misoriented(nii, tmp_path / "dcm") is False

    def test_negative_dominant_normal_pre_epoch_misoriented(self, tmp_path):
        # Same series; origin at the OLD (+dominant-axis / head-forced) feet-end
        # selection (Z=0) is now the WRONG endpoint under D6 -> misoriented.
        _series(tmp_path, "dcm", [0.0, 3.0, 6.0], iop=(1, 0, 0, 0, -1, 0))
        nii = tmp_path / "pre_epoch.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 0.0))
        assert is_volume_misoriented(nii, tmp_path / "dcm") is True

    def test_negative_dominant_normal_idempotent(self, tmp_path):
        _series(tmp_path, "dcm", [0.0, 3.0, 6.0], iop=(1, 0, 0, 0, -1, 0))
        nii = tmp_path / "remediated.nii.gz"
        _nifti_with_lps_origin(nii, (0.0, 0.0, 6.0))
        assert is_volume_misoriented(nii, tmp_path / "dcm") is False
        assert is_volume_misoriented(nii, tmp_path / "dcm") is False
