"""Tests for clarinet.services.image — Image, Segmentation, DICOM volume, COCO converter."""

import json
from pathlib import Path

import nibabel
import nrrd
import numpy as np
import pydicom
import pydicom.uid
import pytest
from pydicom.dataset import Dataset, FileDataset

from clarinet.exceptions.domain import ImageError, ImageReadError
from clarinet.services.image import (
    FileType,
    Image,
    Segmentation,
    coco_to_segmentation,
)
from clarinet.services.image.dicom_volume import (
    _extract_spacing,
    _sort_slices,
    read_dicom_series,
)

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

    def test_sort_by_instance_number(self) -> None:
        datasets = []
        for i in [3, 1, 2]:
            ds = Dataset()
            ds.InstanceNumber = i
            datasets.append(ds)

        sorted_ds = _sort_slices(datasets)
        assert [int(ds.InstanceNumber) for ds in sorted_ds] == [1, 2, 3]

    def test_extract_spacing_defaults(self) -> None:
        ds = Dataset()
        spacing = _extract_spacing([ds])
        assert spacing == (1.0, 1.0, 1.0)


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
        # Small ROI: 8 voxels, overlap 4 → ratio = 0.5
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

        # max_overlap=10 but ratio 0.5 > 0.1 → dropped
        result = seg1.difference(seg2, max_overlap=10, max_overlap_ratio=0.1)
        assert result.is_empty

        # ratio threshold 0.8 → 0.5 < 0.8, so kept
        result = seg1.difference(seg2, max_overlap=10, max_overlap_ratio=0.8)
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
