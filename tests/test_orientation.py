"""Tests for clarinet.services.image.orientation — ground-truth DICOM slice geometry."""

from pathlib import Path

import numpy as np
import pydicom
import pydicom.uid
from pydicom.dataset import FileDataset

from clarinet.services.image.orientation import ground_truth_slice_geometry


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
        origin, direction = ground_truth_slice_geometry(names, 5.0, (99.0, 99.0, 99.0), wrong)
        np.testing.assert_allclose(direction[:, 2], [0.0, 0.0, 1.0], atol=1e-6)
        np.testing.assert_allclose(origin, [0.0, 0.0, 0.0], atol=1e-6)

    def test_descending_ipp_direction_points_negative(self, tmp_path):
        names = _series(tmp_path, "desc", [10.0, 5.0, 0.0])
        origin, direction = ground_truth_slice_geometry(
            names, 5.0, (0.0, 0.0, 10.0), IDENTITY.copy()
        )
        np.testing.assert_allclose(direction[:, 2], [0.0, 0.0, -1.0], atol=1e-6)
        np.testing.assert_allclose(origin, [0.0, 0.0, 10.0], atol=1e-6)

    def test_in_plane_columns_preserved(self, tmp_path):
        names = _series(tmp_path, "inplane", [0.0, 3.0])
        src = np.array([[1.0, 0.0, 9.0], [0.0, -1.0, 9.0], [0.0, 0.0, 9.0]])
        _, direction = ground_truth_slice_geometry(names, 3.0, (0.0, 0.0, 0.0), src)
        np.testing.assert_allclose(direction[:, 0], src[:, 0], atol=1e-9)
        np.testing.assert_allclose(direction[:, 1], src[:, 1], atol=1e-9)

    def test_fewer_than_two_files_returns_unchanged(self, tmp_path):
        names = _series(tmp_path, "single", [0.0])
        o, d = (1.0, 2.0, 3.0), IDENTITY.copy()
        origin, direction = ground_truth_slice_geometry(names, 3.0, o, d)
        assert origin is o and direction is d

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
        origin, direction = ground_truth_slice_geometry(names, 3.0, o, dirn)
        assert origin is o and direction is dirn

    def test_degenerate_delta_returns_unchanged(self, tmp_path):
        names = _series(tmp_path, "degen", [4.0, 4.0])  # identical IPP → zero delta
        o, dirn = (4.0, 4.0, 4.0), IDENTITY.copy()
        origin, direction = ground_truth_slice_geometry(names, 3.0, o, dirn)
        assert origin is o and direction is dirn
