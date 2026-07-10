"""Tests for clarinet.services.image — Image, Segmentation, DICOM volume, COCO converter."""

import json
from pathlib import Path

import nibabel
import nrrd
import numpy as np
import pydicom
import pydicom.uid
import pytest
from pydicom.dataset import FileDataset

from clarinet.exceptions.domain import GeometryMismatchError, ImageError, ImageReadError
from clarinet.services.image import (
    FileType,
    Image,
    Segmentation,
    coco_to_segmentation,
    conform_seg_to_grid,
)
from clarinet.services.image.correspondence import AbsoluteOverlap, GreedyArgmax
from clarinet.services.image.dicom_volume import read_dicom_series

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def nifti_path(tmp_path: Path) -> Path:
    """Create a synthetic NIfTI file."""
    data = np.random.randint(0, 100, size=(10, 12, 8), dtype=np.int16)
    affine = np.diag([0.5, 0.6, 0.7, 1.0])
    img = nibabel.Nifti1Image(data, affine, dtype=np.int16)
    path = tmp_path / "test_volume.nii.gz"
    nibabel.save(img, str(path))
    return path


@pytest.fixture()
def nrrd_path(tmp_path: Path) -> Path:
    """Create a synthetic NRRD file."""
    data = np.random.randint(0, 100, size=(10, 12, 8), dtype=np.int16)
    header = {"spacings": [0.5, 0.6, 0.7]}
    path = tmp_path / "test_volume.nrrd"
    nrrd.write(str(path), data, header)
    return path


@pytest.fixture()
def dicom_dir(tmp_path: Path) -> Path:
    """Create a directory with synthetic DICOM files (3 slices)."""
    dcm_dir = tmp_path / "dicom_series"
    dcm_dir.mkdir()
    for i in range(3):
        filename = dcm_dir / f"slice_{i:03d}.dcm"
        file_meta = pydicom.Dataset()
        file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
        file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
        file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

        ds = FileDataset(str(filename), {}, file_meta=file_meta, preamble=b"\x00" * 128)
        ds.Rows = 4
        ds.Columns = 5
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelSpacing = [0.5, 0.6]
        ds.SliceThickness = 2.0
        ds.ImagePositionPatient = [0.0, 0.0, float(i * 2)]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.InstanceNumber = i + 1
        ds.PixelData = np.ones((4, 5), dtype=np.uint16).tobytes()
        pydicom.dcmwrite(str(filename), ds)
    return dcm_dir


@pytest.fixture()
def seg_volume() -> np.ndarray:
    """Create a simple binary volume with two separate blobs."""
    vol = np.zeros((20, 20, 10), dtype=np.uint8)
    vol[2:5, 2:5, 2:5] = 1  # Blob 1
    vol[12:16, 12:16, 5:8] = 1  # Blob 2
    return vol


# ---------------------------------------------------------------------------
# Image tests
# ---------------------------------------------------------------------------


class TestImage:
    def test_read_nifti_roundtrip(self, nifti_path: Path, tmp_path: Path) -> None:
        img = Image()
        img.read(nifti_path)

        assert img.shape == (10, 12, 8)
        assert img._filetype == FileType.NIFTI
        assert pytest.approx(img.spacing, abs=1e-4) == (0.5, 0.6, 0.7)

        out = img.save("output", tmp_path)
        assert out.exists()
        assert out.name == "output.nii.gz"

        # Read back and compare
        img2 = Image()
        img2.read(out)
        np.testing.assert_array_almost_equal(img.img, img2.img, decimal=4)

    def test_read_nrrd_roundtrip(self, nrrd_path: Path, tmp_path: Path) -> None:
        img = Image()
        img.read(nrrd_path)

        assert img.shape == (10, 12, 8)
        assert img._filetype == FileType.NRRD
        assert pytest.approx(img.spacing, abs=1e-4) == (0.5, 0.6, 0.7)

        out = img.save("output", tmp_path)
        assert out.exists()
        assert out.name == "output.nrrd"

    def test_template_creation(self, nifti_path: Path) -> None:
        img = Image()
        img.read(nifti_path)

        copy = Image(template=img, copy_data=True)
        np.testing.assert_array_equal(copy.img, img.img)

        blank = Image(template=img, copy_data=False)
        assert blank.shape == img.shape
        assert np.all(blank.img == 0)

    def test_forced_dtype(self, nifti_path: Path) -> None:
        img = Image(dtype=np.float32)
        img.read(nifti_path)
        assert img.img.dtype == np.float32

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "test.xyz"
        bad_file.write_text("not an image")
        with pytest.raises(ImageError, match="Unsupported file extension"):
            Image().read(bad_file)

    def test_corrupt_file_raises_read_error(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "corrupt.nii.gz"
        corrupt.write_bytes(b"not a nifti file at all")
        with pytest.raises(ImageReadError):
            Image().read(corrupt)

    def test_spacing_validation(self) -> None:
        img = Image()
        with pytest.raises(ValueError, match="3-tuple"):
            img.spacing = (1.0, 2.0)  # type: ignore[arg-type]

    def test_img_not_loaded_raises(self) -> None:
        img = Image()
        with pytest.raises(ImageError, match="not loaded"):
            _ = img.img

    def test_save_as(self, nifti_path: Path, tmp_path: Path) -> None:
        img = Image()
        img.read(nifti_path)

        out_nrrd = tmp_path / "converted.nrrd"
        result = img.save_as(out_nrrd, FileType.NRRD)
        assert result.exists()

    def test_origin_property(self, nifti_path: Path) -> None:
        img = Image()
        img.read(nifti_path)
        # nifti_path fixture has affine = diag([0.5, 0.6, 0.7, 1.0]) → origin = (0, 0, 0)
        assert img.origin == (0.0, 0.0, 0.0)

        img.origin = (10.0, 20.0, 30.0)
        assert img.origin == (10.0, 20.0, 30.0)

    def test_origin_validation(self) -> None:
        img = Image()
        with pytest.raises(ValueError, match="3-tuple"):
            img.origin = (1.0, 2.0)  # type: ignore[arg-type]

    def test_direction_property(self, nifti_path: Path) -> None:
        img = Image()
        img.read(nifti_path)
        # nifti_path has identity direction in RAS; internal repr is LPS
        np.testing.assert_array_almost_equal(img.direction, np.diag([-1.0, -1.0, 1.0]))

    def test_direction_validation(self) -> None:
        img = Image()
        with pytest.raises(ValueError, match="3x3"):
            img.direction = np.eye(4)

    def test_nifti_reads_direction_from_affine(self, tmp_path: Path) -> None:
        """NIfTI with non-trivial affine extracts correct direction."""
        # 30-degree rotation around Z (in RAS, as stored in NIfTI)
        angle = np.radians(30)
        rotation_ras = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0],
                [np.sin(angle), np.cos(angle), 0],
                [0, 0, 1],
            ]
        )
        spacing = (0.5, 0.6, 0.7)
        origin_ras = (10.0, 20.0, 30.0)

        affine = np.eye(4)
        affine[:3, :3] = rotation_ras * np.array(spacing)
        affine[:3, 3] = origin_ras

        data = np.zeros((4, 4, 4), dtype=np.int16)
        nib_img = nibabel.Nifti1Image(data, affine, dtype=np.int16)
        path = tmp_path / "rotated.nii.gz"
        nibabel.save(nib_img, str(path))

        img = Image()
        img.read(path)
        # Internal representation is LPS (negate X and Y)
        ras_to_lps = np.diag([-1.0, -1.0, 1.0])
        expected_direction = ras_to_lps @ rotation_ras
        expected_origin = (-10.0, -20.0, 30.0)
        assert pytest.approx(img.spacing, abs=1e-4) == spacing
        assert pytest.approx(img.origin, abs=1e-4) == expected_origin
        np.testing.assert_array_almost_equal(img.direction, expected_direction, decimal=4)

    def test_nrrd_reads_direction_from_space_directions(self, tmp_path: Path) -> None:
        """NRRD with space directions extracts correct spacing and direction."""
        data = np.zeros((4, 4, 4), dtype=np.int16)
        header = {
            "space directions": np.array([[0.5, 0.0, 0.0], [0.0, 0.6, 0.0], [0.0, 0.0, 0.7]]),
            "space origin": np.array([10.0, 20.0, 30.0]),
            "space": "left-posterior-superior",
        }
        path = tmp_path / "with_dirs.nrrd"
        nrrd.write(str(path), data, header)

        img = Image()
        img.read(path)
        assert pytest.approx(img.spacing, abs=1e-4) == (0.5, 0.6, 0.7)
        assert pytest.approx(img.origin, abs=1e-4) == (10.0, 20.0, 30.0)
        np.testing.assert_array_almost_equal(img.direction, np.eye(3), decimal=4)

    def test_dicom_reads_origin(self, dicom_dir: Path) -> None:
        """DICOM series extracts origin from ImagePositionPatient."""
        img = Image()
        img.read_dicom_series(dicom_dir)
        # First slice has ImagePositionPatient = [0.0, 0.0, 0.0]
        assert img.origin == (0.0, 0.0, 0.0)

    def test_dicom_reads_direction(self, dicom_dir: Path) -> None:
        """DICOM direction matrix maps numpy axes to patient-space (LPS)."""
        img = Image()
        img.read_dicom_series(dicom_dir)
        # Fixture IOP = [1,0,0, 0,1,0] (standard axial)
        # numpy axis 0 (row index) → col_dir [0,1,0]
        # numpy axis 1 (col index) → row_dir [1,0,0]
        # numpy axis 2 (slice)     → slice_dir [0,0,1]
        expected = np.array([[0, 1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
        np.testing.assert_array_almost_equal(img.direction, expected, decimal=4)

    def test_save_as_dicom_raises(self, nifti_path: Path, tmp_path: Path) -> None:
        img = Image()
        img.read(nifti_path)
        with pytest.raises(ImageError, match="DICOM writing"):
            img.save_as(tmp_path / "out.dcm", FileType.DICOM)

    def test_save_no_directory_no_source_raises(self) -> None:
        img = Image()
        img._img = np.zeros((2, 2, 2))
        img._filetype = FileType.NIFTI
        with pytest.raises(ImageError, match="No source path"):
            img.save("test")

    def test_save_dicom_raises(self, dicom_dir: Path) -> None:
        img = Image()
        img.read_dicom_series(dicom_dir)
        with pytest.raises(ImageError, match="DICOM writing"):
            img.save("test")

    def test_load_data_false_populates_grid_not_voxels(self, nifti_path: Path) -> None:
        img = Image()
        img.read(nifti_path, load_data=False)
        assert img.has_data is False
        assert img.shape == (10, 12, 8)
        assert pytest.approx(img.spacing, abs=1e-4) == (0.5, 0.6, 0.7)
        assert img.affine_4x4.shape == (4, 4)  # grid math works without voxels
        with pytest.raises(ImageError, match="not loaded"):
            _ = img.img

    def test_load_data_false_same_grid_against_full_read(self, nifti_path: Path) -> None:
        meta = Image()
        meta.read(nifti_path, load_data=False)
        full = Image()
        full.read(nifti_path)
        assert meta.same_grid(full)

    def test_load_data_false_nrrd(self, nrrd_path: Path) -> None:
        img = Image()
        img.read(nrrd_path, load_data=False)
        assert img.has_data is False
        assert img.shape == (10, 12, 8)
        assert pytest.approx(img.spacing, abs=1e-4) == (0.5, 0.6, 0.7)

    def test_has_data_true_after_full_read(self, nifti_path: Path) -> None:
        img = Image()
        img.read(nifti_path)
        assert img.has_data is True
        assert img.shape == (10, 12, 8)


# ---------------------------------------------------------------------------
# DICOM volume tests
# ---------------------------------------------------------------------------


class TestDicomVolume:
    def test_read_synthetic_series(self, dicom_dir: Path) -> None:
        img = Image()
        img.read_dicom_series(dicom_dir)

        assert img.shape == (4, 5, 3)
        assert img._filetype == FileType.DICOM
        assert pytest.approx(img.spacing[0], abs=1e-4) == 0.5
        assert pytest.approx(img.spacing[1], abs=1e-4) == 0.6
        assert pytest.approx(img.spacing[2], abs=1e-4) == 2.0

    def test_empty_dir_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_dicom"
        empty.mkdir()
        with pytest.raises(ImageReadError, match="No DICOM files found"):
            read_dicom_series(empty)

    def test_not_a_directory_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "not_a_dir.txt"
        f.write_text("hello")
        with pytest.raises(ImageReadError, match="Not a directory"):
            read_dicom_series(f)

    def test_multi_series_warns_and_reads_first(self, tmp_path: Path) -> None:
        """When the directory contains multiple DICOM series, only the first is read
        and a warning is emitted. Closes the silent-selection gap from PR #221 review."""
        from clarinet.utils.logger import logger as clarinet_logger

        dcm_dir = tmp_path / "multi_series"
        dcm_dir.mkdir()

        for series_idx, series_uid_suffix in enumerate(["series_a", "series_b"]):
            series_uid = pydicom.uid.generate_uid(prefix=f"1.2.3.{series_idx}.")
            for slice_idx in range(2):
                filename = dcm_dir / f"{series_uid_suffix}_{slice_idx:03d}.dcm"
                file_meta = pydicom.Dataset()
                file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
                file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
                file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

                ds = FileDataset(str(filename), {}, file_meta=file_meta, preamble=b"\x00" * 128)
                ds.Rows = 3
                ds.Columns = 3
                ds.BitsAllocated = 16
                ds.BitsStored = 16
                ds.HighBit = 15
                ds.PixelRepresentation = 0
                ds.SamplesPerPixel = 1
                ds.PhotometricInterpretation = "MONOCHROME2"
                ds.PixelSpacing = [1.0, 1.0]
                ds.SliceThickness = 1.0
                ds.SeriesInstanceUID = series_uid
                ds.ImagePositionPatient = [0.0, 0.0, float(slice_idx)]
                ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
                ds.InstanceNumber = slice_idx + 1
                ds.PixelData = np.full((3, 3), series_idx, dtype=np.uint16).tobytes()
                pydicom.dcmwrite(str(filename), ds)

        captured: list[str] = []
        sink_id = clarinet_logger.add(
            lambda message: captured.append(message.record["message"]), level="WARNING"
        )
        try:
            volume, _, _, _ = read_dicom_series(dcm_dir)
        finally:
            clarinet_logger.remove(sink_id)

        assert volume.shape == (3, 3, 2)
        assert any("Multiple DICOM series" in m for m in captured)

    @staticmethod
    def _write_axial_series(
        dcm_dir: Path,
        slice_pixels: dict[float, np.ndarray],
        iop: tuple[int, ...] = (1, 0, 0, 0, -1, 0),
    ) -> None:
        """Write an axial series mapping physical Z (mm) → (rows, cols) pixel array.

        With the default ``ImageOrientationPatient=[1,0,0,0,-1,0]`` the slice normal
        points along -Z, so SimpleITK orders slices opposite to the legacy ascending-
        ``ImagePositionPatient[2]`` convention — exercising the slice-axis flip. Pass
        ``iop=(1,0,0,0,1,0)`` (normal +Z) for an already-canonical series.
        """
        dcm_dir.mkdir()
        suid = pydicom.uid.generate_uid()
        for k, (z, pixels) in enumerate(slice_pixels.items()):
            filename = dcm_dir / f"slice_{k:03d}.dcm"
            file_meta = pydicom.Dataset()
            file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
            file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
            file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

            ds = FileDataset(str(filename), {}, file_meta=file_meta, preamble=b"\x00" * 128)
            ds.SeriesInstanceUID = suid
            ds.Rows = int(pixels.shape[0])
            ds.Columns = int(pixels.shape[1])
            ds.BitsAllocated = 16
            ds.BitsStored = 16
            ds.HighBit = 15
            ds.PixelRepresentation = 0
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.PixelSpacing = [1.0, 1.0]
            ds.SliceThickness = 3.0
            ds.ImageOrientationPatient = list(iop)
            ds.ImagePositionPatient = [0.0, 0.0, z]
            ds.InstanceNumber = k + 1
            ds.PixelData = pixels.astype(np.uint16).tobytes()
            pydicom.dcmwrite(str(filename), ds)

    def test_negative_z_axial_series_canonicalized(self, tmp_path: Path) -> None:
        """DICOM→NIfTI conversion must be slice-order canonical and version-stable.

        An axial series whose IOP slice normal points along -Z (common on MRI) is
        ordered by SimpleITK opposite to the legacy ascending-
        ``ImagePositionPatient[2]`` convention used by the pre-#221 reader. The
        reader normalises the slice axis to the positive sense of its dominant axis
        (here Z), so re-converting a series always yields the same grid —
        preventing the projection/doctor-seg index-reversed Z-flip.
        """
        dcm_dir = tmp_path / "neg_z_series"
        # value encodes |Z|: Z=0 -> 100, Z=-3 -> 130, Z=-6 -> 160
        self._write_axial_series(
            dcm_dir,
            {z: np.full((4, 5), int(100 + abs(z) * 10)) for z in (0.0, -3.0, -6.0)},
        )

        volume, _spacing, origin, direction = read_dicom_series(dcm_dir)

        # Slice axis points +Z (canonical); origin sits at the minimum physical Z.
        assert direction[2, 2] > 0
        assert pytest.approx(origin[2], abs=1e-4) == -6.0
        # In-array slices run ascending physical Z: Z=-6 (160) first, Z=0 (100) last.
        assert int(volume[0, 0, 0]) == 160
        assert int(volume[0, 0, -1]) == 100

    def test_positive_z_axial_series_unchanged(self, tmp_path: Path) -> None:
        """A series whose slice axis already points +Z is passed through untouched
        (the canonicalisation early-return), guaranteeing idempotency for the common
        case: re-converting a standard +Z axial series never moves the grid.
        """
        dcm_dir = tmp_path / "pos_z_series"
        # IOP normal = +Z; value encodes Z: Z=0 -> 100, Z=3 -> 130, Z=6 -> 160
        self._write_axial_series(
            dcm_dir,
            {z: np.full((4, 5), int(100 + z * 10)) for z in (0.0, 3.0, 6.0)},
            iop=(1, 0, 0, 0, 1, 0),
        )

        volume, _spacing, origin, direction = read_dicom_series(dcm_dir)

        # No flip: origin stays at the first (min-Z) slice, slice order unchanged.
        assert direction[2, 2] > 0
        assert pytest.approx(origin[2], abs=1e-4) == 0.0
        assert int(volume[0, 0, 0]) == 100
        assert int(volume[0, 0, -1]) == 160

    def test_canonicalization_preserves_geometry(self, tmp_path: Path) -> None:
        """Slice-axis canonicalisation flips the array, origin and direction
        together, so every voxel keeps its physical location — unlike a
        direction-only flip, which would mirror the data through the origin plane.
        A single marked voxel must map, via the voxel→LPS affine, to its true DICOM
        physical position and land on the canonical (last) slice index.
        """
        dcm_dir = tmp_path / "marked_series"
        marker = 999
        z0_pixels = np.zeros((4, 5), dtype=np.uint16)
        z0_pixels[1, 2] = marker  # row=1, col=2 in the slice at physical Z=0
        self._write_axial_series(
            dcm_dir,
            {0.0: z0_pixels, -3.0: np.zeros((4, 5)), -6.0: np.zeros((4, 5))},
        )

        img = Image()
        img.read_dicom_series(dcm_dir)

        idx = np.argwhere(img.img == marker)
        assert idx.shape[0] == 1
        r, c, k = (int(v) for v in idx[0])
        assert k == 2  # Z=0 is the maximum physical Z → last slice after canon
        phys = img.affine_4x4 @ np.array([r, c, k, 1.0])
        # IOP rowdir(col index)=[1,0,0], coldir(row index)=[0,-1,0], 1 mm spacing,
        # slice IPP z=0 → physical (col, -row, 0) = (2, -1, 0) in LPS.
        np.testing.assert_array_almost_equal(phys[:3], [2.0, -1.0, 0.0], decimal=4)

    def test_rescale_slope_intercept_applied(self, tmp_path: Path) -> None:
        """RescaleSlope/RescaleIntercept convert stored pixels to real-world values (HU)."""
        dcm_dir = tmp_path / "rescale_dicom"
        dcm_dir.mkdir()
        stored_value = 1105
        intercept = -1024.0
        slope = 1.0
        expected_hu = stored_value * slope + intercept  # 81.0

        for i in range(2):
            filename = dcm_dir / f"slice_{i:03d}.dcm"
            file_meta = pydicom.Dataset()
            file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
            file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
            file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

            ds = FileDataset(str(filename), {}, file_meta=file_meta, preamble=b"\x00" * 128)
            ds.Rows = 2
            ds.Columns = 2
            ds.BitsAllocated = 16
            ds.BitsStored = 16
            ds.HighBit = 15
            ds.PixelRepresentation = 0
            ds.SamplesPerPixel = 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.PixelSpacing = [1.0, 1.0]
            ds.ImagePositionPatient = [0.0, 0.0, float(i)]
            ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
            ds.InstanceNumber = i + 1
            ds.RescaleSlope = slope
            ds.RescaleIntercept = intercept
            ds.PixelData = np.full((2, 2), stored_value, dtype=np.uint16).tobytes()
            pydicom.dcmwrite(str(filename), ds)

        img = Image()
        img.read_dicom_series(dcm_dir)
        np.testing.assert_allclose(img.img, expected_hu)


# ---------------------------------------------------------------------------
# Segmentation tests
# ---------------------------------------------------------------------------


class TestSegmentation:
    def test_autolabel(self, seg_volume: np.ndarray) -> None:
        seg = Segmentation(autolabel=True)
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = seg_volume

        assert seg.count == 2
        assert seg.img.dtype == np.uint8

    def test_no_autolabel(self, seg_volume: np.ndarray) -> None:
        seg = Segmentation(autolabel=False)
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = seg_volume

        assert seg.count == 1  # All non-zero treated as one component when labeled

    def test_is_empty(self) -> None:
        seg = Segmentation()
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = np.zeros((5, 5, 5), dtype=np.uint8)
        assert seg.is_empty

    def test_is_not_empty(self, seg_volume: np.ndarray) -> None:
        seg = Segmentation()
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = seg_volume
        assert not seg.is_empty

    def test_separate_labels(self) -> None:
        vol = np.zeros((10, 10, 5), dtype=np.uint8)
        vol[1:3, 1:3, 1:3] = 1
        vol[6:8, 6:8, 1:3] = 1

        seg = Segmentation(autolabel=False)
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = vol
        assert len(np.unique(seg.img)) == 2  # 0 and 1

        seg.separate_labels()
        assert len(np.unique(seg.img)) == 3  # 0, 1, 2

    def test_dilate(self, seg_volume: np.ndarray) -> None:
        seg = Segmentation()
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = seg_volume
        original_nonzero = np.count_nonzero(seg.img)

        seg.dilate(radius=1)
        assert np.count_nonzero(seg.img) > original_nonzero

    def test_binary_open(self) -> None:
        vol = np.zeros((20, 20, 10), dtype=np.uint8)
        vol[5:15, 5:15, 2:8] = 1
        vol[10, 10, 5] = 0  # Tiny hole

        seg = Segmentation(autolabel=False)
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = vol
        seg.binary_open(radius=1)
        assert np.count_nonzero(seg.img) > 0

    def test_filter_by_area(self, seg_volume: np.ndarray) -> None:
        seg = Segmentation()
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = seg_volume

        # Get props sorted by area
        all_props = seg.label_props
        areas = [p.area for p in all_props]
        min_area = min(areas)

        # Filter for larger ROIs only
        filtered = seg.filtered_props("area", ge=min_area + 1)
        assert len(filtered) < len(all_props)

    def test_filter_segmentation(self, seg_volume: np.ndarray) -> None:
        seg = Segmentation()
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = seg_volume

        filtered_seg = seg.filter_segmentation("num_pixels", ge=1)
        assert isinstance(filtered_seg, Segmentation)
        assert not filtered_seg.is_empty

    def test_filter_roi(self, seg_volume: np.ndarray) -> None:
        seg = Segmentation()
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = seg_volume

        mask = seg.filter_roi("num_pixels", ge=1)
        assert mask.dtype == np.uint8
        assert np.count_nonzero(mask) > 0

    # ---------------------------------------------------------------
    # Named set operations
    # ---------------------------------------------------------------

    def test_intersection_default(self, seg_volume: np.ndarray) -> None:
        """intersection() with default min_overlap=1 keeps any-overlap labels."""
        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = seg_volume

        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = seg_volume  # Same mask → full intersection

        result = seg1.intersection(seg2)
        assert isinstance(result, Segmentation)
        assert not result.is_empty

    def test_intersection_with_min_overlap(self) -> None:
        """Labels with overlap below min_overlap are dropped."""
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:3, 1:3, 1:3] = 1  # 8 voxels
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[2:4, 2:4, 2:4] = 1  # overlaps at [2,2,2] — 1 voxel

        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1
        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        # min_overlap=1 → kept (1 >= 1)
        result = seg1.intersection(seg2, min_overlap=1)
        assert not result.is_empty

        # min_overlap=5 → dropped (1 < 5)
        result = seg1.intersection(seg2, min_overlap=5)
        assert result.is_empty

    def test_intersection_with_ratio(self) -> None:
        """min_overlap_ratio filters by overlap / label_size."""
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:3, 1:3, 1:3] = 1  # 8 voxels
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[1:3, 1:3, 1:2] = 1  # overlaps 4 voxels → ratio = 4/8 = 0.5

        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1
        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        # ratio 0.5 >= 0.3 → kept
        result = seg1.intersection(seg2, min_overlap_ratio=0.3)
        assert not result.is_empty

        # ratio 0.5 < 0.8 → dropped
        result = seg1.intersection(seg2, min_overlap_ratio=0.8)
        assert result.is_empty

    def test_union(self) -> None:
        """union() combines all nonzero voxels."""
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:3, 1:3, 1:3] = 1
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[6:8, 6:8, 1:3] = 1

        seg1 = Segmentation(autolabel=False)
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1

        seg2 = Segmentation(autolabel=False)
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        result = seg1.union(seg2)
        assert np.count_nonzero(result.img) == np.count_nonzero(vol1) + np.count_nonzero(vol2)

    def test_symmetric_difference(self, seg_volume: np.ndarray) -> None:
        """symmetric_difference() returns a valid Segmentation."""
        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = seg_volume

        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = seg_volume

        result = seg1.symmetric_difference(seg2)
        assert isinstance(result, Segmentation)

    # ---------------------------------------------------------------
    # Deprecated operators (emit DeprecationWarning)
    # ---------------------------------------------------------------

    def test_and_operation(self, seg_volume: np.ndarray) -> None:
        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = seg_volume

        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = seg_volume  # Same mask → full intersection

        with pytest.warns(DeprecationWarning, match="intersection"):
            result = seg1 & seg2
        assert isinstance(result, Segmentation)
        assert not result.is_empty

    def test_or_operation(self) -> None:
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:3, 1:3, 1:3] = 1
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[6:8, 6:8, 1:3] = 1

        seg1 = Segmentation(autolabel=False)
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1

        seg2 = Segmentation(autolabel=False)
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        with pytest.warns(DeprecationWarning, match="union"):
            result = seg1 | seg2
        assert np.count_nonzero(result.img) == np.count_nonzero(vol1) + np.count_nonzero(vol2)

    def test_sub_operation(self) -> None:
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:5, 1:5, 1:4] = 1
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[6:8, 6:8, 1:3] = 1  # Non-overlapping

        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1

        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        with pytest.warns(DeprecationWarning, match="difference"):
            result = seg1 - seg2
        assert isinstance(result, Segmentation)
        # seg1 has no overlap with seg2, so result should keep seg1's ROI
        assert not result.is_empty

    def test_sub_drops_overlapping_label(self) -> None:
        """__sub__ must drop labels with ANY overlap (no threshold tolerance)."""
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:5, 1:5, 1:4] = 1  # 48 voxels
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[4:6, 4:6, 3:4] = 1  # overlaps vol1 at [4,4,3] — 1 voxel

        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1
        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        with pytest.warns(DeprecationWarning, match="difference"):
            result = seg1 - seg2
        assert result.is_empty  # strict subtraction drops the overlapping label

    def test_difference_with_max_overlap(self) -> None:
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:5, 1:5, 1:4] = 1  # 48 voxels
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[4:6, 4:6, 3:4] = 1  # overlaps at 1 voxel

        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1
        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        # max_overlap=0 → strict (same as __sub__)
        assert seg1.difference(seg2, max_overlap=0).is_empty

        # max_overlap=5 → tolerate small overlaps
        result = seg1.difference(seg2, max_overlap=5)
        assert not result.is_empty

    def test_difference_with_ratio(self) -> None:
        # Small ROI: 8 voxels, overlap 4 → coverage(a) = 4/8 = 0.5
        # New semantics: when max_overlap_ratio is given, ratio wins (max_overlap ignored).
        # A label is REMOVED iff coverage >= max_overlap_ratio.
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:3, 1:3, 1:3] = 1  # 8 voxels
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[1:3, 1:3, 1:2] = 1  # overlaps 4 voxels

        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1
        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        # coverage 0.5 >= 0.1 → matched → removed from difference
        result = seg1.difference(seg2, max_overlap_ratio=0.1)
        assert result.is_empty

        # coverage 0.5 < 0.8 → not matched → kept in difference
        result = seg1.difference(seg2, max_overlap_ratio=0.8)
        assert not result.is_empty

    def test_add_operation(self) -> None:
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:3, 1:3, 1:3] = 1
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[6:8, 6:8, 1:3] = 1

        seg1 = Segmentation(autolabel=False)
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1

        seg2 = Segmentation(autolabel=False)
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        with pytest.warns(DeprecationWarning, match="union"):
            result = seg1 + seg2
        total = np.count_nonzero(vol1) + np.count_nonzero(vol2)
        assert np.count_nonzero(result.img) == total

    def test_xor_operation(self) -> None:
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[1:5, 1:5, 1:4] = 1
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[3:7, 3:7, 2:5] = 1  # Overlapping

        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1

        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        with pytest.warns(DeprecationWarning, match="symmetric_difference"):
            result = seg1 ^ seg2
        assert isinstance(result, Segmentation)

    def test_subtract_in_place(self) -> None:
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[2:5, 2:5, 2:5] = 1
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[3:5, 3:5, 3:5] = 1  # Subset

        seg1 = Segmentation(autolabel=False)
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = vol1

        seg2 = Segmentation(autolabel=False)
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = vol2

        original_count = np.count_nonzero(seg1.img)
        seg1.subtract(seg2)
        assert np.count_nonzero(seg1.img) < original_count

    def test_append(self) -> None:
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[2:4, 2:4, 2:4] = 1

        seg = Segmentation()
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = vol1

        label_value = seg.img[2, 2, 2]  # Get assigned label

        # Create additional voxels adjacent to the existing blob
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[3:5, 3:5, 3:5] = 1  # Overlaps with existing blob

        other = Segmentation(autolabel=False)
        other._spacing = (1.0, 1.0, 1.0)
        other.img = vol2

        seg.append(other)
        # Appended voxels should get the same label
        assert seg._img[4, 4, 4] == label_value

    def test_append_no_overlap_skips(self) -> None:
        vol1 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol1[0:2, 0:2, 0:2] = 1

        seg = Segmentation()
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = vol1
        before = seg.img.copy()

        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[8:10, 8:10, 3:5] = 1  # No overlap at all

        other = Segmentation(autolabel=False)
        other._spacing = (1.0, 1.0, 1.0)
        other.img = vol2

        seg.append(other)
        np.testing.assert_array_equal(seg.img, before)

    def test_append_multi_label_overlap_raises(self) -> None:
        seg = Segmentation(autolabel=False)
        seg._spacing = (1.0, 1.0, 1.0)
        vol = np.zeros((10, 10, 5), dtype=np.uint8)
        vol[2:5, 2:5, 2:4] = 1
        vol[4:7, 4:7, 2:4] = 2  # Adjacent label
        seg.img = vol

        # ROI that spans both labels without touching background
        vol2 = np.zeros((10, 10, 5), dtype=np.uint8)
        vol2[3:6, 3:6, 2:4] = 1  # Overlaps label 1 and label 2

        other = Segmentation(autolabel=False)
        other._spacing = (1.0, 1.0, 1.0)
        other.img = vol2

        with pytest.raises(ValueError, match="ROI overlaps multiple labels"):
            seg.append(other)

    def test_copy_from(self, seg_volume: np.ndarray) -> None:
        seg1 = Segmentation()
        seg1._spacing = (1.0, 1.0, 1.0)
        seg1.img = seg_volume

        seg2 = Segmentation()
        seg2._spacing = (1.0, 1.0, 1.0)
        seg2.img = np.zeros_like(seg_volume)
        assert seg2.is_empty

        seg2.copy_from(seg1)
        assert not seg2.is_empty

    def test_hu_correction(self) -> None:
        vol = np.zeros((10, 10, 5), dtype=np.uint8)
        vol[2:5, 2:5, 1:4] = 1

        seg = Segmentation(autolabel=True)
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = vol

        hu = Image()
        hu._spacing = (1.0, 1.0, 1.0)
        hu._img = np.full((10, 10, 5), 50.0)  # All within range

        seg.rois_hu_correction(hu, min_hu=0, max_hu=100, radius=1)
        assert not seg.is_empty

    def test_read_save_roundtrip(self, nifti_path: Path, tmp_path: Path) -> None:
        """Segmentation can read a NIfTI, save, and read back."""
        seg = Segmentation(autolabel=False)
        seg.read(nifti_path)
        out = seg.save("seg_output", tmp_path)
        assert out.exists()


# ---------------------------------------------------------------------------
# COCO converter tests
# ---------------------------------------------------------------------------


class TestCOCO:
    def test_coco_parse_and_convert(self, nifti_path: Path, tmp_path: Path) -> None:
        """Full roundtrip: create COCO JSON, convert to segmentation."""
        # Create a simple COCO JSON
        coco_data = {
            "info": {
                "mode": "annotation",
                "studyInstanceUID": "1.2.3",
                "dateTime": "2025-01-01",
            },
            "categories": [{"id": 1, "name": "lesion", "description": "test category"}],
            "images": [
                {
                    "id": 1,
                    "width": 12,
                    "height": 10,
                    "numberOfFrames": 8,
                    "seriesInstanceUID": "1.2.3.4",
                    "sopInstanceUID": "1.2.3.4.1",
                }
            ],
            "annotations": [
                {
                    "id": 1,
                    "imageId": 1,
                    "categoryId": 1,
                    "area": 10.0,
                    "bbox": [2, 2, 4, 4],
                    "frameNumber": 3,
                    "segmentation": [
                        [[2, 2], [2, 6], [6, 6], [6, 2]],
                    ],
                }
            ],
        }

        coco_json = tmp_path / "test_coco.json"
        with open(coco_json, "w") as f:
            json.dump(coco_data, f)

        # Read reference volume
        vol = Image()
        vol.read(nifti_path)

        # Convert
        seg = coco_to_segmentation(coco_json, vol, separate_labels=True)

        assert isinstance(seg, Segmentation)
        assert seg.shape == vol.shape

    def test_invalid_json_raises(self, nifti_path: Path, tmp_path: Path) -> None:
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("not valid json")

        vol = Image()
        vol.read(nifti_path)

        with pytest.raises(ImageReadError, match="Failed to read COCO JSON"):
            coco_to_segmentation(bad_json, vol)


# ---------------------------------------------------------------------------
# Spatial alignment tests
# ---------------------------------------------------------------------------


def _make_seg(
    shape: tuple[int, ...] = (10, 10, 10),
    spacing: tuple[float, float, float] = (1.0, 1.0, 1.0),
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    direction: np.ndarray | None = None,
    data: np.ndarray | None = None,
) -> Segmentation:
    """Helper to build a Segmentation with explicit spatial metadata."""
    seg = Segmentation(autolabel=False)
    if direction is None:
        direction = np.eye(3)
    seg._direction = direction.copy()
    seg._origin = origin
    seg._spacing = spacing
    if data is not None:
        seg.img = data
    else:
        seg.img = np.zeros(shape, dtype=np.uint8)
    return seg


class TestSpatialAlignment:
    """Tests for affine_4x4, _same_grid, reindex_to, and aligned set operations."""

    # -- affine_4x4 --

    def test_affine_4x4_identity(self) -> None:
        seg = _make_seg()
        expected = np.eye(4)
        np.testing.assert_array_almost_equal(seg.affine_4x4, expected)

    def test_affine_4x4_custom(self) -> None:
        direction = np.array([[0, 1, 0], [1, 0, 0], [0, 0, -1]], dtype=float)
        seg = _make_seg(spacing=(0.5, 0.6, 0.7), origin=(10.0, 20.0, 30.0), direction=direction)
        A = seg.affine_4x4
        # direction * spacing → columns of A[:3,:3]
        expected_rot = direction * np.array([0.5, 0.6, 0.7])
        np.testing.assert_array_almost_equal(A[:3, :3], expected_rot)
        np.testing.assert_array_almost_equal(A[:3, 3], [10.0, 20.0, 30.0])

    # -- _same_grid --

    def test_same_grid_true(self) -> None:
        a = _make_seg(origin=(1.0, 2.0, 3.0))
        b = _make_seg(origin=(1.0, 2.0, 3.0))
        assert a.same_grid(b)

    def test_same_grid_false_origin(self) -> None:
        a = _make_seg(origin=(0.0, 0.0, 0.0))
        b = _make_seg(origin=(0.0, 0.0, 1.0))
        assert not a.same_grid(b)

    def test_same_grid_false_shape(self) -> None:
        a = _make_seg(shape=(10, 10, 10))
        b = _make_seg(shape=(10, 10, 12))
        assert not a.same_grid(b)

    def test_same_grid_false_spacing(self) -> None:
        a = _make_seg(spacing=(1.0, 1.0, 1.0))
        b = _make_seg(spacing=(1.0, 1.0, 2.0))
        assert not a.same_grid(b)

    def test_same_grid_false_direction(self) -> None:
        a = _make_seg()
        flipped = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        b = _make_seg(direction=flipped)
        assert not a.same_grid(b)

    def test_same_grid_within_tolerance(self) -> None:
        a = _make_seg(origin=(0.0, 0.0, 0.0))
        b = _make_seg(origin=(0.0, 0.0, 1e-6))
        assert a.same_grid(b)

    # -- reindex_to --

    def test_reindex_identity_noop(self) -> None:
        data = np.zeros((10, 10, 10), dtype=np.uint8)
        data[3:6, 3:6, 3:6] = 5
        a = _make_seg(data=data)
        target = _make_seg()  # same grid
        result = a.reindex_to(target)
        np.testing.assert_array_equal(result.img, data)

    def test_reindex_axis_flip(self) -> None:
        """Flip axis 2: direction [0,0,-1], origin shifted so physical coords match."""
        shape = (10, 10, 10)
        data = np.zeros(shape, dtype=np.uint8)
        data[2:5, 2:5, 7:10] = 3  # blob at high k-indices

        # Source: flipped z-axis, origin at physical end
        flipped_dir = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        src = _make_seg(
            shape=shape,
            origin=(0.0, 0.0, 9.0),  # physical z starts at 9, goes to 0
            direction=flipped_dir,
            data=data,
        )

        # Target: standard orientation
        target = _make_seg(shape=shape, origin=(0.0, 0.0, 0.0))

        result = src.reindex_to(target)
        # Blob at k=7..9 in flipped space → k=0..2 in standard space
        assert np.sum(result.img[2:5, 2:5, 0:3]) > 0
        assert np.sum(result.img[2:5, 2:5, 7:10]) == 0
        # Labels preserved
        assert set(np.unique(result.img)) == {0, 3}

    def test_reindex_into_flipped_target(self) -> None:
        """Source is identity, target has flipped z + shifted origin."""
        shape = (10, 10, 10)
        data = np.zeros(shape, dtype=np.uint8)
        data[2:5, 2:5, 2:5] = 3

        src = _make_seg(shape=shape, data=data)

        flipped_dir = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        target = _make_seg(shape=shape, direction=flipped_dir, origin=(0.0, 0.0, 9.0))

        result = src.reindex_to(target)
        # k=2..4 in identity space → k=5..7 in flipped target
        assert np.sum(result.img[2:5, 2:5, 5:8]) > 0
        assert np.sum(result.img[2:5, 2:5, 2:5]) == 0
        assert set(np.unique(result.img)) == {0, 3}

    def test_reindex_preserves_labels(self) -> None:
        """order=0 must never create new label values."""
        data = np.zeros((10, 10, 10), dtype=np.uint8)
        data[1:4, 1:4, 1:4] = 5
        data[6:9, 6:9, 6:9] = 8
        src = _make_seg(data=data, spacing=(1.0, 1.0, 1.0))
        target = _make_seg(spacing=(1.2, 1.2, 1.2))  # different spacing
        result = src.reindex_to(target)
        assert set(np.unique(result.img)).issubset({0, 5, 8})

    # -- set operations with misaligned grids --

    def test_difference_with_flipped_axis(self) -> None:
        """Reproduces the production bug: same physical blob, flipped grid → 0 FP."""
        shape = (10, 10, 20)
        blob = np.zeros(shape, dtype=np.uint8)
        blob[3:7, 3:7, 12:18] = 1  # blob at high k

        # seg: standard orientation
        seg = _make_seg(shape=shape, data=blob.copy())

        # proj: z-axis flipped, origin adjusted
        flipped_dir = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        proj_data = np.zeros(shape, dtype=np.uint8)
        proj_data[3:7, 3:7, 2:8] = 1  # same physical location, mirrored indices
        proj = _make_seg(
            shape=shape,
            origin=(0.0, 0.0, 19.0),
            direction=flipped_dir,
            data=proj_data,
        )

        fp = seg.difference(proj, max_overlap_ratio=0.05, resample=True)
        assert fp.count == 0  # no false positives

    def test_union_with_different_origin(self) -> None:
        shape = (10, 10, 10)
        data_a = np.zeros(shape, dtype=np.uint8)
        data_a[2:5, 2:5, 2:5] = 1
        a = _make_seg(shape=shape, origin=(0.0, 0.0, 0.0), data=data_a)

        # b is shifted 2 voxels along z
        data_b = np.zeros(shape, dtype=np.uint8)
        data_b[2:5, 2:5, 0:3] = 1  # same physical location as a's blob
        b = _make_seg(shape=shape, origin=(0.0, 0.0, 2.0), data=data_b)

        result = a.union(b, resample=True)
        # The blob from b should land at k=2..4 in a's space
        assert np.sum(result.img[2:5, 2:5, 2:5]) > 0

    def test_subtract_with_flip(self) -> None:
        shape = (10, 10, 10)
        data_self = np.zeros(shape, dtype=np.uint8)
        data_self[2:5, 2:5, 7:10] = 1

        seg = _make_seg(shape=shape, data=data_self)

        # other: same physical blob, flipped z
        flipped_dir = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        data_other = np.zeros(shape, dtype=np.uint8)
        data_other[2:5, 2:5, 0:3] = 1
        other = _make_seg(
            shape=shape,
            origin=(0.0, 0.0, 9.0),
            direction=flipped_dir,
            data=data_other,
        )

        seg.subtract(other, resample=True)
        # After subtract, blob at k=7..9 should be zeroed
        assert np.sum(seg.img[2:5, 2:5, 7:10]) == 0

    def test_fast_path_returns_same_object(self) -> None:
        a = _make_seg()
        b = _make_seg()
        aligned = a._align_other(b)
        assert aligned is b  # no copy, same object

    # -- assert_same_grid + fail-fast set operations --

    def test_assert_same_grid_passes(self) -> None:
        a = _make_seg(origin=(1.0, 2.0, 3.0))
        b = _make_seg(origin=(1.0, 2.0, 3.0))
        a.assert_same_grid(b)  # no raise

    def test_assert_same_grid_raises_on_flip(self) -> None:
        a = _make_seg()
        flipped = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        b = _make_seg(direction=flipped)
        with pytest.raises(GeometryMismatchError, match="same physical grid"):
            a.assert_same_grid(b)

    def test_setop_raises_on_grid_mismatch_by_default(self) -> None:
        """Set operations fail-fast on misaligned grids unless resample=True."""
        a = _make_seg(origin=(0.0, 0.0, 0.0))
        b = _make_seg(origin=(0.0, 0.0, 5.0))
        with pytest.raises(GeometryMismatchError, match="same physical grid") as exc:
            a.difference(b)
        # diagnostic names both grids (self/other summaries with origin)
        msg = str(exc.value)
        assert "self" in msg and "other" in msg and "origin" in msg
        with pytest.raises(GeometryMismatchError):
            a.union(b)
        with pytest.raises(GeometryMismatchError):
            a.intersection(b)
        with pytest.raises(GeometryMismatchError):
            a.subtract(b)

    def test_setops_size1_other_short_circuits_grid_guard(self) -> None:
        """A size-1 (empty marker) other short-circuits before the grid guard."""
        a = _make_seg(data=np.ones((10, 10, 10), dtype=np.uint8))
        empty = Segmentation(autolabel=False)
        empty.img = np.zeros((1, 1, 1), dtype=np.uint8)
        # none of these raise GeometryMismatchError on the size-1 path
        assert a.union(empty).img.shape == (10, 10, 10)
        assert a.difference(empty).img.shape == (10, 10, 10)
        assert a.intersection(empty).is_empty

    def test_setop_same_grid_needs_no_resample(self) -> None:
        """Matching grids take the fast path without resample."""
        data = np.zeros((10, 10, 10), dtype=np.uint8)
        data[2:5, 2:5, 2:5] = 1
        a = _make_seg(data=data)
        b = _make_seg(data=data.copy())
        result = a.union(b)  # no resample arg
        assert np.sum(result.img) > 0


class TestConformSegToGrid:
    """Tests for the conform_seg_to_grid repair helper."""

    @staticmethod
    def _write_volume(path: Path, shape: tuple[int, ...]) -> Image:
        vol = Image()
        vol._direction = np.eye(3)
        vol._origin = (0.0, 0.0, 0.0)
        vol._spacing = (1.0, 1.0, 1.0)
        vol.img = np.zeros(shape, dtype=np.uint8)
        vol.save_as(path, FileType.NIFTI)
        return vol

    def test_conform_resamples_flipped_seg(self, tmp_path: Path) -> None:
        shape = (10, 10, 10)
        vol_path = tmp_path / "volume.nii.gz"
        vol = self._write_volume(vol_path, shape)

        # Segmentation: Z-flipped, blob at high k (mirrors the production bug)
        seg_data = np.zeros(shape, dtype=np.uint8)
        seg_data[3:6, 3:6, 7:10] = 4
        flipped = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        seg_path = tmp_path / "seg.seg.nrrd"
        _make_seg(origin=(0.0, 0.0, 9.0), direction=flipped, data=seg_data).save_as(
            seg_path, FileType.NRRD
        )

        changed = conform_seg_to_grid(seg_path, vol_path)
        assert changed is True

        fixed = Segmentation(autolabel=False)
        fixed.read(seg_path)
        assert fixed.same_grid(vol)
        # blob at k=7..9 in flipped space → k=0..2 in the canonical grid
        assert np.sum(fixed.img[3:6, 3:6, 0:3]) > 0
        assert np.sum(fixed.img[3:6, 3:6, 7:10]) == 0
        assert set(np.unique(fixed.img)) == {0, 4}  # labels preserved (NN)

    def test_conform_noop_when_same_grid(self, tmp_path: Path) -> None:
        shape = (8, 8, 8)
        vol_path = tmp_path / "volume.nii.gz"
        self._write_volume(vol_path, shape)

        seg_data = np.zeros(shape, dtype=np.uint8)
        seg_data[1:4, 1:4, 1:4] = 1
        seg_path = tmp_path / "seg.seg.nrrd"
        _make_seg(origin=(0.0, 0.0, 0.0), data=seg_data).save_as(seg_path, FileType.NRRD)

        assert conform_seg_to_grid(seg_path, vol_path) is False

    def test_conform_to_out_path_leaves_source(self, tmp_path: Path) -> None:
        shape = (8, 8, 8)
        vol_path = tmp_path / "volume.nii.gz"
        self._write_volume(vol_path, shape)
        flipped = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        seg_data = np.zeros(shape, dtype=np.uint8)
        seg_data[2:5, 2:5, 5:8] = 2
        seg_path = tmp_path / "seg.seg.nrrd"
        _make_seg(origin=(0.0, 0.0, 7.0), direction=flipped, data=seg_data).save_as(
            seg_path, FileType.NRRD
        )
        out_path = tmp_path / "fixed.seg.nrrd"

        assert conform_seg_to_grid(seg_path, vol_path, out_path=out_path) is True
        assert out_path.is_file()
        # source still on the flipped grid (not overwritten)
        src = Segmentation(autolabel=False)
        src.read(seg_path)
        assert float(src._direction[2, 2]) == -1.0


def _label_to_name(header: dict) -> dict[int, str]:
    """Map LabelValue -> Name from contiguous Segment{i}_* header blocks."""
    out: dict[int, str] = {}
    i = 0
    while f"Segment{i}_LabelValue" in header:
        out[int(header[f"Segment{i}_LabelValue"])] = header[f"Segment{i}_Name"]
        i += 1
    return out


class TestSegmentMetadataRoundtrip:
    """Named-segment metadata must survive writes, resampling, and set operations.

    Regression for the conform_seg_to_grid bug where Segment{i}_Name/LabelValue
    were dropped on resample (PR #393 follow-up).
    """

    def test_save_roundtrips_segment_names(self, tmp_path: Path) -> None:
        """A plain read -> save preserves semantic keys and drops grid-bound extents."""
        seg_path = tmp_path / "named.seg.nrrd"
        data = np.zeros((6, 6, 6), dtype=np.uint8)
        data[1:4, 1:4, 1:4] = 1
        data[1:4, 1:4, 4:6] = 2
        nrrd.write(
            str(seg_path),
            data,
            {
                "space": "left-posterior-superior",
                "space directions": np.eye(3),
                "space origin": np.zeros(3),
                "Segment0_Name": "liver",
                "Segment0_LabelValue": "1",
                "Segment0_Color": "1 0 0",
                "Segment0_Extent": "1 3 1 3 1 3",
                "Segment1_Name": "tumor",
                "Segment1_LabelValue": "2",
            },
        )
        seg = Segmentation(autolabel=False)
        seg.read(seg_path)
        out_path = tmp_path / "out.seg.nrrd"
        seg.save_as(out_path, FileType.NRRD)

        _, header = nrrd.read(str(out_path))
        assert _label_to_name(header) == {1: "liver", 2: "tumor"}
        assert header.get("Segment0_Color") == "1 0 0"
        assert not any("Extent" in k for k in header)  # grid-dependent dropped on write

    def test_conform_preserves_segment_names(self, tmp_path: Path) -> None:
        """conform_seg_to_grid round-trips Name/LabelValue across a Z-flip resample."""
        seg_path = tmp_path / "doctor.seg.nrrd"
        ref_path = tmp_path / "volume.nrrd"
        data = np.zeros((8, 8, 8), dtype=np.uint8)
        data[2:5, 2:5, 2:5] = 1  # mts
        data[2:5, 2:5, 5:7] = 3  # benign
        nrrd.write(
            str(seg_path),
            data,
            {
                "space": "left-posterior-superior",
                "space directions": np.eye(3),
                "space origin": np.zeros(3),
                "Segment0_Name": "mts",
                "Segment0_LabelValue": "1",
                "Segment0_Layer": "0",
                "Segment0_Extent": "2 4 2 4 2 4",
                "Segment1_Name": "benign",
                "Segment1_LabelValue": "3",
                "Segment1_Layer": "0",
            },
        )
        # Reference on a flipped grid (same physical volume) forces a real resample.
        nrrd.write(
            str(ref_path),
            np.zeros((8, 8, 8), dtype=np.uint8),
            {
                "space": "left-posterior-superior",
                "space directions": np.diag([1.0, 1.0, -1.0]),
                "space origin": np.array([0.0, 0.0, 7.0]),
            },
        )

        assert conform_seg_to_grid(seg_path, ref_path) is True
        out_data, header = nrrd.read(str(seg_path))
        assert sorted(int(x) for x in np.unique(out_data) if x) == [1, 3]  # labels survive
        assert _label_to_name(header) == {1: "mts", 3: "benign"}  # names survive
        assert not any("Extent" in k for k in header)  # stale grid extent dropped

    def test_reindex_carries_source_metadata(self, tmp_path: Path) -> None:
        """reindex_to carries the source's names onto the target grid (seen on save)."""
        shape = (10, 10, 10)
        data = np.zeros(shape, dtype=np.uint8)
        data[2:5, 2:5, 2:5] = 1
        data[2:5, 2:5, 6:9] = 2
        src = _make_seg(shape=shape, data=data)
        src._nrrd_header = {
            "Segment0_Name": "mts",
            "Segment0_LabelValue": "1",
            "Segment0_Color": "1 0 0",
            "Segment0_Extent": "2 4 2 4 2 4",
            "Segment1_Name": "benign",
            "Segment1_LabelValue": "2",
            "Segment1_Extent": "2 4 2 4 6 8",
        }
        flipped = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float)
        target = _make_seg(shape=shape, direction=flipped, origin=(0.0, 0.0, 9.0))

        out_path = tmp_path / "reindexed.seg.nrrd"
        src.reindex_to(target).save_as(out_path, FileType.NRRD)
        out_data, header = nrrd.read(str(out_path))
        assert {int(v) for v in np.unique(out_data) if v} == {1, 2}  # both labels resampled
        assert _label_to_name(header) == {1: "mts", 2: "benign"}
        assert header.get("Segment0_Color") == "1 0 0"
        assert not any("Extent" in k for k in header)  # grid-dependent dropped

    def test_intersection_prunes_dropped_segment(self, tmp_path: Path) -> None:
        """A label dropped by intersection loses its metadata on save; survivors renumber."""
        shape = (10, 10, 10)
        data = np.zeros(shape, dtype=np.uint8)
        data[1:4, 1:4, 1:4] = 1  # mts
        data[6:9, 6:9, 6:9] = 2  # benign
        a = _make_seg(shape=shape, data=data)
        a._nrrd_header = {
            "Segment0_Name": "mts",
            "Segment0_LabelValue": "1",
            "Segment1_Name": "benign",
            "Segment1_LabelValue": "2",
        }
        other_data = np.zeros(shape, dtype=np.uint8)
        other_data[1:4, 1:4, 1:4] = 1  # overlaps only the mts blob
        other = _make_seg(shape=shape, data=other_data)

        out_path = tmp_path / "inter.seg.nrrd"
        a.intersection(other, min_overlap=1).save_as(out_path, FileType.NRRD)
        out_data, header = nrrd.read(str(out_path))
        assert {int(v) for v in np.unique(out_data) if v} == {1}  # benign dropped
        assert _label_to_name(header) == {1: "mts"}  # renumbered, benign pruned

    def test_union_drops_segment_names(self, tmp_path: Path) -> None:
        """union relabels into components — original per-label names no longer apply."""
        shape = (10, 10, 10)
        data = np.zeros(shape, dtype=np.uint8)
        data[1:4, 1:4, 1:4] = 1  # mts
        data[6:9, 6:9, 6:9] = 3  # benign
        a = _make_seg(shape=shape, data=data)
        a._nrrd_header = {
            "Segment0_Name": "mts",
            "Segment0_LabelValue": "1",
            "Segment1_Name": "benign",
            "Segment1_LabelValue": "3",
        }
        other = _make_seg(shape=shape, data=np.zeros(shape, dtype=np.uint8))

        out_path = tmp_path / "uni.seg.nrrd"
        a.union(other).save_as(out_path, FileType.NRRD)
        _, header = nrrd.read(str(out_path))
        assert not any(k.startswith("Segment") for k in header)  # no named segments survive

    def test_symmetric_difference_drops_segment_names(self, tmp_path: Path) -> None:
        """symmetric_difference relabels into fresh components — the source's
        per-label names no longer map, so they must not ride along on save
        (else a survivor inherits an unrelated name, cf. #397)."""
        shape = (10, 10, 10)
        data = np.zeros(shape, dtype=np.uint8)
        data[1:4, 1:4, 1:4] = 1  # mts: overlaps other -> matched -> dropped
        data[6:9, 6:9, 6:9] = 2  # benign: no overlap -> kept, relabeled to 1
        a = _make_seg(shape=shape, data=data)
        a._nrrd_header = {
            "Segment0_Name": "mts",
            "Segment0_LabelValue": "1",
            "Segment1_Name": "benign",
            "Segment1_LabelValue": "2",
        }
        other_data = np.zeros(shape, dtype=np.uint8)
        other_data[1:4, 1:4, 1:4] = 1  # overlaps only the mts blob
        other = _make_seg(shape=shape, data=other_data)

        out_path = tmp_path / "symdiff.seg.nrrd"
        a.symmetric_difference(other).save_as(out_path, FileType.NRRD)
        out_data, header = nrrd.read(str(out_path))
        assert {int(v) for v in np.unique(out_data) if v} == {1}  # only benign survives
        assert not any(k.startswith("Segment") for k in header)  # stale names dropped


# ---------------------------------------------------------------------------
# Correspondence-engine adapter tests (Tasks 6)
# ---------------------------------------------------------------------------


def _seg(arr: np.ndarray) -> Segmentation:
    """Build a Segmentation from a numpy array, auto-labeling components."""
    s = Segmentation(autolabel=True)
    s.img = arr.astype(np.uint8)
    return s


def test_intersection_backward_compatible() -> None:
    """intersection(min_overlap=N) keeps A labels with >= N voxel overlap."""
    a = np.zeros((8, 8, 1), dtype=np.uint8)
    b = np.zeros((8, 8, 1), dtype=np.uint8)
    a[1:4, 1:4, 0] = 1
    b[2:3, 2:3, 0] = 1  # 1-voxel overlap
    seg_a, seg_b = _seg(a), _seg(b)
    assert not seg_a.intersection(seg_b, min_overlap=1).is_empty
    assert seg_a.intersection(seg_b, min_overlap=5).is_empty


def test_strategy_overrides_resolve_1_to_n() -> None:
    """strategy= override resolves 1-to-N matches; difference keeps unmatched A only."""
    a = np.zeros((4, 10, 1), dtype=np.uint8)
    b = np.zeros((4, 10, 1), dtype=np.uint8)
    a[1:3, 1:9, 0] = 1  # one wide A component
    b[1:3, 1:5, 0] = 1  # B1 — larger overlap
    b[1:3, 7:8, 0] = 1  # B2 — smaller overlap (separate component)
    seg_a, seg_b = _seg(a), _seg(b)
    out = seg_a.difference(seg_b, strategy=GreedyArgmax(AbsoluteOverlap(), direction="a_to_b"))
    # A matched its larger-overlap partner and is dropped; the result is empty of A
    assert out.is_empty


def test_difference_intersection_multi_component_per_edge_threshold() -> None:
    """Per-edge threshold: one wide A overlaps two B-components (3 and 4 voxels, sum 7).

    The default no-strategy path uses ThresholdMatch per edge (largest single-component
    overlap = 4), not the summed overlap (7). This locks in the accepted behavior.
    """
    # One wide self-component A overlaps TWO separate other-components,
    # with per-component overlaps 3 and 4 (sum 7). The default no-strategy
    # path thresholds on the largest single overlap (4), not the sum (7).
    a = np.zeros((1, 12, 1), dtype=np.uint8)
    a[0, 0:9, 0] = 1  # A: one component, 9 voxels
    b = np.zeros((1, 12, 1), dtype=np.uint8)
    b[0, 0:3, 0] = 1  # B1 overlaps A by 3 voxels
    b[0, 5:9, 0] = 1  # B2 overlaps A by 4 voxels (gap at cols 3,4 keeps B1/B2 separate)
    seg_a, seg_b = _seg(a), _seg(b)
    # max single-component overlap is 4 < max_overlap+1 = 6  ->  A is NOT matched
    diff = seg_a.difference(seg_b, max_overlap=5)
    assert not diff.is_empty  # A survives (old summed-overlap path would have removed it)
    assert int(np.count_nonzero(diff.img)) == 9
    # same fixture, intersection: max single overlap 4 < min_overlap 5  ->  no match -> empty
    inter = seg_a.intersection(seg_b, min_overlap=5)
    assert inter.is_empty


# ---------------------------------------------------------------------------
# symmetric_difference component-level behavior (Fix 1 — reviewer)
# ---------------------------------------------------------------------------


def test_symmetric_difference_keeps_component_adjacent_to_match() -> None:
    """Component-level symdiff keeps A2 even when it is physically adjacent to B.

    The old union()-relabel approach merged A2 into the overlapping blob and then
    dropped it; the new correspondence-engine approach keeps A2 because it has no
    direct voxel overlap with B.

    Layout (cols in a 1x8x1 array):
      A1 = cols 1-2  (overlaps B at col 2)
      A2 = cols 4-5  (no overlap with B; gap at col 3 keeps components separate)
      B  = cols 2-3  (overlaps A1 at col 2; adjacent to A2 at col 3|4)
    """
    a = np.zeros((1, 8, 1), dtype=np.uint8)
    a[0, 1:3, 0] = 1  # A1: cols 1-2 (overlaps B at col 2)
    a[0, 4:6, 0] = 1  # A2: cols 4-5 (separate component; gap at col 3)
    b = np.zeros((1, 8, 1), dtype=np.uint8)
    b[0, 2:4, 0] = 1  # B: cols 2-3 (overlaps A1 at col 2; adjacent to A2 at col 3|4)
    out = _seg(a).symmetric_difference(_seg(b))
    assert not out.is_empty
    # A2 (the unmatched, disagreeing component) must survive
    assert int(out.img[0, 4, 0]) != 0 and int(out.img[0, 5, 0]) != 0
    # col 2 was the A1<->B overlap — it is removed in matched pairs
    assert int(out.img[0, 2, 0]) == 0


# ---------------------------------------------------------------------------
# append opt-in strategy (Task 7)
# ---------------------------------------------------------------------------


def _two_label_self_and_bridging_other() -> tuple[Segmentation, Segmentation]:
    base = np.zeros((4, 10, 1), dtype=np.uint8)
    base[1:3, 1:3, 0] = 1  # self label A
    base[1:3, 7:9, 0] = 1  # self label B (separate)
    other = np.zeros((4, 10, 1), dtype=np.uint8)
    other[1:3, 2:8, 0] = 1  # one ROI bridging both; equal overlap on each side (2 voxels each),
    # winner is base label A (lowest a-label) — GreedyArgmax deterministic tie-break
    return _seg(base), _seg(other)


def test_append_multi_label_raises_by_default() -> None:
    seg, other = _two_label_self_and_bridging_other()
    with pytest.raises(ValueError, match="multiple labels"):
        seg.append(other)


def test_append_strategy_resolves_to_winner() -> None:
    seg, other = _two_label_self_and_bridging_other()
    seg.append(other, strategy=GreedyArgmax(AbsoluteOverlap(), direction="b_to_a"))
    # bridging ROI merged into exactly one existing label, none added as new
    assert {int(v) for v in np.unique(seg.img)} <= {0, 1, 2}
    # col 4, row 1 is inside the bridge ROI but not inside either base label — it must now
    # carry one of the existing label values (1 or 2), proving the ROI was genuinely merged
    assert int(seg.img[1, 4, 0]) in (1, 2)
