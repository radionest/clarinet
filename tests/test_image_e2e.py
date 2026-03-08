"""End-to-end workflow tests for clarinet.services.image.

These tests exercise multi-step pipelines simulating real clinical image
processing scenarios: format conversions, segmentation chains, COCO ingestion,
HU correction, and degraded DICOM input handling.

The image service is a pure library (no HTTP API), so "e2e" here means
multi-step workflow tests chaining several operations together.
"""

import json
from pathlib import Path

import nibabel
import nrrd
import numpy as np
import pydicom
import pydicom.uid
import pytest
from pydicom.dataset import FileDataset

from clarinet.services.image import (
    FileType,
    Image,
    Segmentation,
    coco_to_segmentation,
)

# ---------------------------------------------------------------------------
# Fixtures — self-contained synthetic data, no external files required
# ---------------------------------------------------------------------------

VOLUME_SHAPE = (32, 32, 16)
VOLUME_SPACING = (0.75, 0.75, 2.5)


@pytest.fixture()
def synthetic_volume() -> np.ndarray:
    """Deterministic 32x32x16 int16 volume."""
    rng = np.random.default_rng(seed=42)
    return rng.integers(0, 500, size=VOLUME_SHAPE, dtype=np.int16)


@pytest.fixture()
def nifti_volume_path(tmp_path: Path, synthetic_volume: np.ndarray) -> Path:
    """NIfTI file from synthetic_volume with correct affine encoding spacing."""
    affine = np.diag([*VOLUME_SPACING, 1.0])
    img = nibabel.Nifti1Image(synthetic_volume, affine, dtype=np.int16)
    path = tmp_path / "synthetic.nii.gz"
    nibabel.save(img, str(path))
    return path


@pytest.fixture()
def dicom_series_dir(tmp_path: Path, synthetic_volume: np.ndarray) -> Path:
    """16-slice DICOM directory from synthetic_volume with proper metadata."""
    dcm_dir = tmp_path / "dicom_series"
    dcm_dir.mkdir()

    for i in range(VOLUME_SHAPE[2]):
        filename = dcm_dir / f"slice_{i:04d}.dcm"
        file_meta = pydicom.Dataset()
        file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
        file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
        file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

        ds = FileDataset(str(filename), {}, file_meta=file_meta, preamble=b"\x00" * 128)
        ds.Rows = VOLUME_SHAPE[0]
        ds.Columns = VOLUME_SHAPE[1]
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1  # signed
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelSpacing = [VOLUME_SPACING[0], VOLUME_SPACING[1]]
        ds.SliceThickness = VOLUME_SPACING[2]
        ds.ImagePositionPatient = [0.0, 0.0, float(i * VOLUME_SPACING[2])]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.InstanceNumber = i + 1
        ds.PixelData = synthetic_volume[:, :, i].tobytes()
        pydicom.dcmwrite(str(filename), ds)

    return dcm_dir


# ---------------------------------------------------------------------------
# 1. Format Conversion Pipeline
# ---------------------------------------------------------------------------


class TestFormatConversionPipeline:
    """Multi-format conversion chains verifying data and spacing preservation."""

    def test_nifti_to_nrrd_to_nifti_roundtrip(
        self, nifti_volume_path: Path, tmp_path: Path
    ) -> None:
        """NIfTI → NRRD → NIfTI roundtrip preserves voxel data and spacing."""
        # Step 1: Read NIfTI
        img1 = Image(dtype=np.int16)
        img1.read(nifti_volume_path)
        original_data = img1.img.copy()
        original_spacing = img1.spacing

        # Step 2: Save as NRRD
        nrrd_path = tmp_path / "intermediate.nrrd"
        img1.save_as(nrrd_path, FileType.NRRD)
        assert nrrd_path.exists()

        # Step 3: Read NRRD — spacing preserved via canonical spatial model
        img2 = Image(dtype=np.int16)
        img2.read(nrrd_path)
        np.testing.assert_array_equal(img2.img, original_data)
        assert pytest.approx(img2.spacing, abs=1e-4) == original_spacing

        # Step 4: Save back to NIfTI
        nifti_out = tmp_path / "roundtrip.nii.gz"
        img2.save_as(nifti_out, FileType.NIFTI)
        assert nifti_out.exists()

        # Step 5: Read back — voxel data still intact through full chain
        img3 = Image(dtype=np.int16)
        img3.read(nifti_out)
        np.testing.assert_array_equal(img3.img, original_data)
        assert pytest.approx(img3.spacing, abs=1e-4) == original_spacing

    def test_nrrd_to_nifti_to_nrrd_roundtrip(self, tmp_path: Path) -> None:
        """NRRD → NIfTI → NRRD roundtrip preserves voxel data and spacing."""
        rng = np.random.default_rng(seed=99)
        data = rng.integers(0, 300, size=VOLUME_SHAPE, dtype=np.int16)
        header = {"spacings": list(VOLUME_SPACING)}
        nrrd_src = tmp_path / "source.nrrd"
        nrrd.write(str(nrrd_src), data, header)

        # Step 1: Read NRRD
        img1 = Image(dtype=np.int16)
        img1.read(nrrd_src)
        assert pytest.approx(img1.spacing, abs=1e-4) == VOLUME_SPACING

        # Step 2: Save as NIfTI
        nifti_path = tmp_path / "intermediate.nii.gz"
        img1.save_as(nifti_path, FileType.NIFTI)

        # Step 3: Read NIfTI — spacing preserved via canonical spatial model
        img2 = Image(dtype=np.int16)
        img2.read(nifti_path)
        np.testing.assert_array_equal(img2.img, data)
        assert pytest.approx(img2.spacing, abs=1e-4) == VOLUME_SPACING

        # Step 4: Save back as NRRD
        nrrd_out = tmp_path / "roundtrip.nrrd"
        img2.save_as(nrrd_out, FileType.NRRD)

        # Step 5: Read final NRRD — voxels and spacing preserved
        img3 = Image(dtype=np.int16)
        img3.read(nrrd_out)
        np.testing.assert_array_equal(img3.img, data)
        assert pytest.approx(img3.spacing, abs=1e-4) == VOLUME_SPACING

    def test_dicom_to_nifti_preserves_data(
        self, dicom_series_dir: Path, synthetic_volume: np.ndarray, tmp_path: Path
    ) -> None:
        """DICOM series → NIfTI preserves voxel data and spacing."""
        # Step 1: Read DICOM
        img = Image(dtype=np.int16)
        img.read_dicom_series(dicom_series_dir)
        assert img.shape == VOLUME_SHAPE
        assert pytest.approx(img.spacing, abs=1e-4) == VOLUME_SPACING

        # Step 2: Save as NIfTI
        nifti_path = tmp_path / "from_dicom.nii.gz"
        img.save_as(nifti_path, FileType.NIFTI)

        # Step 3: Read back
        img2 = Image(dtype=np.int16)
        img2.read(nifti_path)

        # Voxel data preserved
        np.testing.assert_array_equal(img2.img, synthetic_volume)

        # Spacing preserved via canonical spatial model
        assert pytest.approx(img2.spacing, abs=1e-4) == VOLUME_SPACING


# ---------------------------------------------------------------------------
# 2. COCO Annotation Pipeline
# ---------------------------------------------------------------------------


class TestCOCOAnnotationPipeline:
    """Full COCO ingest → process → save pipeline."""

    def test_coco_ingest_dilate_filter_save_roundtrip(
        self, nifti_volume_path: Path, tmp_path: Path
    ) -> None:
        """Read volume → create COCO → convert → dilate → filter → save → read back."""
        # Step 1: Read reference volume
        vol = Image()
        vol.read(nifti_volume_path)

        # Step 2: Build COCO JSON with 2 annotations on different slices
        #   Volume shape is (32, 32, 16). COCO images match width=32, height=32.
        slice_0 = 2
        slice_1 = 10
        coco_data = {
            "info": {
                "mode": "annotation",
                "studyInstanceUID": "1.2.3.4.5",
                "dateTime": "2025-06-01",
            },
            "categories": [{"id": 1, "name": "roi", "description": "test ROI"}],
            "images": [
                {
                    "id": 1,
                    "width": 32,
                    "height": 32,
                    "numberOfFrames": 16,
                    "seriesInstanceUID": "1.2.3.4.5.6",
                    "sopInstanceUID": "1.2.3.4.5.6.1",
                },
            ],
            "annotations": [
                {
                    "id": 1,
                    "imageId": 1,
                    "categoryId": 1,
                    "area": 100.0,
                    "bbox": [10, 10, 10, 10],
                    "frameNumber": slice_0,
                    "segmentation": [
                        [[10, 10], [10, 20], [20, 20], [20, 10]],
                    ],
                },
                {
                    "id": 2,
                    "imageId": 1,
                    "categoryId": 1,
                    "area": 64.0,
                    "bbox": [5, 5, 8, 8],
                    "frameNumber": slice_1,
                    "segmentation": [
                        [[5, 5], [5, 13], [13, 13], [13, 5]],
                    ],
                },
            ],
        }
        coco_json = tmp_path / "annotations.json"
        with open(coco_json, "w") as f:
            json.dump(coco_data, f)

        # Step 3: Convert COCO → Segmentation
        seg = coco_to_segmentation(coco_json, vol, separate_labels=True)
        assert seg.shape == vol.shape
        assert not seg.is_empty

        # Both annotated slices should have nonzero voxels
        # Note: coco_to_segmentation flips Y axis ([:, ::-1, :])
        assert np.count_nonzero(seg.img[:, :, slice_0]) > 0
        assert np.count_nonzero(seg.img[:, :, slice_1]) > 0

        nonzero_before = np.count_nonzero(seg.img)

        # Step 4: Dilate
        seg.dilate(1)
        nonzero_after_dilate = np.count_nonzero(seg.img)
        assert nonzero_after_dilate >= nonzero_before

        # Step 5: Filter segmentation — keep ROIs with enough pixels
        filtered = seg.filter_segmentation("num_pixels", ge=5)
        assert not filtered.is_empty

        # Step 6: Save and read back
        out_path = tmp_path / "coco_result.nii.gz"
        filtered.save_as(out_path, FileType.NIFTI)
        assert out_path.exists()

        readback = Segmentation(autolabel=False)
        readback.read(out_path)
        assert readback.shape == vol.shape
        assert np.count_nonzero(readback.img) > 0


# ---------------------------------------------------------------------------
# 3. Segmentation Processing Chain
# ---------------------------------------------------------------------------


class TestSegmentationProcessingChain:
    """Set operations chain with persistence."""

    def test_set_operations_chain_and_persistence(self, tmp_path: Path) -> None:
        """Create 2 overlapping segmentations → named ops → chain → filter → save → read back."""
        shape = (30, 30, 16)

        # Create two segmentations with overlapping 7x7x7 cubes (3x3x5 overlap)
        vol_a = np.zeros(shape, dtype=np.uint8)
        vol_a[5:12, 5:12, 3:10] = 1  # 7x7x7 cube

        vol_b = np.zeros(shape, dtype=np.uint8)
        vol_b[9:16, 9:16, 5:12] = 1  # 7x7x7 cube, overlaps with a at [9:12, 9:12, 5:10]

        seg_a = Segmentation(autolabel=True)
        seg_a._spacing = (1.0, 1.0, 1.0)
        seg_a.img = vol_a

        seg_b = Segmentation(autolabel=True)
        seg_b._spacing = (1.0, 1.0, 1.0)
        seg_b.img = vol_b

        # Basic set operations (named methods)
        intersection = seg_a.intersection(seg_b)
        union = seg_a.union(seg_b)

        assert np.count_nonzero(intersection.img) > 0, "Overlapping cubes must intersect"
        assert np.count_nonzero(union.img) > 0

        # Union should have more nonzero than either individual
        assert np.count_nonzero(union.img) >= np.count_nonzero(seg_a.img)
        assert np.count_nonzero(union.img) >= np.count_nonzero(seg_b.img)

        # symmetric_difference() = union - intersection. With default
        # max_overlap=0 the difference is strict: drops any label overlapping
        # the intersection → may produce empty result for connected components.
        sym_diff = seg_a.symmetric_difference(seg_b)
        assert isinstance(sym_diff, Segmentation)

        # Chain: union(a, b).intersection(a) — keep a's voxels that overlap with union
        chain_step = seg_a.union(seg_b).intersection(seg_a)
        assert np.count_nonzero(chain_step.img) > 0, "a overlaps with union(a, b)"

        # Filter: keep only ROIs with enough pixels
        filtered = chain_step.filter_segmentation("num_pixels", ge=3)
        assert not filtered.is_empty

        # Save and read back
        out = tmp_path / "chain_result.nii.gz"
        filtered.save_as(out, FileType.NIFTI)
        assert out.exists()

        readback = Segmentation(autolabel=False)
        readback.read(out)
        np.testing.assert_array_equal(readback.img, filtered.img)


# ---------------------------------------------------------------------------
# 4. HU Correction Workflow
# ---------------------------------------------------------------------------


class TestHUCorrectionWorkflow:
    """HU-based ROI filtering with known values."""

    def test_hu_range_filters_rois(self, tmp_path: Path) -> None:
        """Create CT with 3 HU regions → 3-label seg → HU correction → verify filtering."""
        shape = (40, 40, 20)

        # Create CT volume with 3 distinct HU regions
        ct_data = np.zeros(shape, dtype=np.float64)
        ct_data[5:12, 5:12, 5:12] = 50.0  # Region 1: HU=50 (within range)
        ct_data[15:22, 15:22, 5:12] = 200.0  # Region 2: HU=200 (above range)
        ct_data[28:35, 28:35, 5:12] = 75.0  # Region 3: HU=75 (within range)

        hu_image = Image()
        hu_image._img = ct_data
        hu_image._spacing = (1.0, 1.0, 1.0)

        # Create matching 3-label segmentation (blobs must be >=4x4x4 to survive
        # opening(ball(2)) in rois_hu_correction)
        seg_data = np.zeros(shape, dtype=np.uint8)
        seg_data[5:12, 5:12, 5:12] = 1  # Label 1 at HU=50
        seg_data[15:22, 15:22, 5:12] = 2  # Label 2 at HU=200
        seg_data[28:35, 28:35, 5:12] = 3  # Label 3 at HU=75

        seg = Segmentation(autolabel=False)
        seg._spacing = (1.0, 1.0, 1.0)
        seg.img = seg_data
        assert np.count_nonzero(seg.img == 2) > 0, "Label 2 should exist before correction"

        # Apply HU correction: keep only HU in [0, 100]
        seg.rois_hu_correction(hu_image, min_hu=0, max_hu=100, radius=2)

        # Label 2 (HU=200) should be eliminated
        assert np.count_nonzero(seg.img == 2) == 0, (
            "HU=200 region should be eliminated by max_hu=100 filter"
        )

        # Labels 1 and 3 (HU=50, HU=75) should survive
        assert np.count_nonzero(seg.img == 1) > 0, "HU=50 region should survive"
        assert np.count_nonzero(seg.img == 3) > 0, "HU=75 region should survive"

        # Save and read back to verify persistence
        out = tmp_path / "hu_corrected.nii.gz"
        seg.save_as(out, FileType.NIFTI)

        readback = Segmentation(autolabel=False)
        readback.read(out)
        assert np.count_nonzero(readback.img == 2) == 0
        assert np.count_nonzero(readback.img == 1) > 0
        assert np.count_nonzero(readback.img == 3) > 0


# ---------------------------------------------------------------------------
# 5. Template Propagation
# ---------------------------------------------------------------------------


class TestTemplatePropagation:
    """Template metadata alignment and copy consistency."""

    def test_template_chain_preserves_metadata(self, dicom_series_dir: Path) -> None:
        """DICOM → blank Image template → Seg template → verify metadata → copy_from."""
        # Step 1: Read DICOM source
        source = Image()
        source.read_dicom_series(dicom_series_dir)

        # Step 2: Create blank Image template
        blank = Image(template=source, copy_data=False)
        assert blank.shape == source.shape
        assert blank.spacing == source.spacing
        assert np.all(blank.img == 0)

        # Step 3: Create Segmentation template from source
        seg1 = Segmentation(autolabel=True, template=source)
        assert seg1.shape == source.shape
        assert seg1.spacing == source.spacing

        # Step 4: Populate seg1 with blobs
        blob_data = np.zeros(source.shape, dtype=np.uint8)
        blob_data[5:10, 5:10, 2:6] = 1
        blob_data[20:26, 20:26, 8:14] = 1
        seg1.img = blob_data
        assert not seg1.is_empty

        occupancy1 = np.count_nonzero(seg1.img)

        # Step 5: Copy to a second segmentation via copy_from
        seg2 = Segmentation(autolabel=False, template=source)
        assert seg2.is_empty

        seg2.copy_from(seg1)
        assert not seg2.is_empty
        assert np.count_nonzero(seg2.img) == occupancy1


# ---------------------------------------------------------------------------
# 6. Multi-Format Roundtrip
# ---------------------------------------------------------------------------


class TestMultiFormatRoundtrip:
    """Cross-format voxel equivalence."""

    def test_nifti_and_nrrd_identical_voxels(self, nifti_volume_path: Path, tmp_path: Path) -> None:
        """Read NIfTI → save as NIfTI + NRRD → read both → assert voxel equality."""
        # Read original
        original = Image(dtype=np.int16)
        original.read(nifti_volume_path)

        # Save as NIfTI (new path) and NRRD
        nifti_copy = tmp_path / "copy.nii.gz"
        nrrd_copy = tmp_path / "copy.nrrd"

        original.save_as(nifti_copy, FileType.NIFTI)
        original.save_as(nrrd_copy, FileType.NRRD)

        # Read both back with forced dtype
        from_nifti = Image(dtype=np.int16)
        from_nifti.read(nifti_copy)

        from_nrrd = Image(dtype=np.int16)
        from_nrrd.read(nrrd_copy)

        # Voxels must be identical across formats
        np.testing.assert_array_equal(
            from_nifti.img,
            from_nrrd.img,
            err_msg="NIfTI and NRRD should produce identical voxel data",
        )


# ---------------------------------------------------------------------------
# 7. Degraded DICOM Input
# ---------------------------------------------------------------------------


class TestDegradedDICOMInput:
    """Error-tolerant DICOM reading with mixed valid and invalid files."""

    def test_mixed_valid_and_invalid_dicom_files(self, dicom_series_dir: Path) -> None:
        """Dir with valid DICOM + garbage .dcm + no-pixel DICOM → only valid slices loaded."""
        # The fixture already has 16 valid slices. Add garbage files.

        # Garbage file: not a DICOM at all
        garbage = dicom_series_dir / "garbage_999.dcm"
        garbage.write_bytes(b"THIS IS NOT A DICOM FILE AT ALL")

        # No-pixel DICOM: valid DICOM structure but no PixelData
        no_pixel = dicom_series_dir / "no_pixel_998.dcm"
        file_meta = pydicom.Dataset()
        file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
        file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
        file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
        ds = FileDataset(str(no_pixel), {}, file_meta=file_meta, preamble=b"\x00" * 128)
        ds.Rows = VOLUME_SHAPE[0]
        ds.Columns = VOLUME_SHAPE[1]
        ds.InstanceNumber = 999
        # Deliberately no PixelData
        pydicom.dcmwrite(str(no_pixel), ds)

        # Read — should skip garbage and no-pixel files
        img = Image(dtype=np.int16)
        img.read_dicom_series(dicom_series_dir)

        # Only the 16 original valid slices should be loaded
        assert img.shape == VOLUME_SHAPE, (
            f"Expected shape {VOLUME_SHAPE} (16 valid slices), got {img.shape}"
        )
        assert pytest.approx(img.spacing, abs=1e-4) == VOLUME_SPACING


# ---------------------------------------------------------------------------
# 8. Spatial Preservation Across Formats
# ---------------------------------------------------------------------------


class TestSpatialPreservation:
    """Verify spacing, origin, and direction survive cross-format conversions."""

    def test_nifti_to_nrrd_spacing(self, nifti_volume_path: Path, tmp_path: Path) -> None:
        """NIfTI → NRRD preserves spacing."""
        img = Image(dtype=np.int16)
        img.read(nifti_volume_path)

        nrrd_path = tmp_path / "out.nrrd"
        img.save_as(nrrd_path, FileType.NRRD)

        img2 = Image(dtype=np.int16)
        img2.read(nrrd_path)
        assert pytest.approx(img2.spacing, abs=1e-4) == VOLUME_SPACING

    def test_nrrd_to_nifti_spacing(self, tmp_path: Path) -> None:
        """NRRD → NIfTI preserves spacing."""
        data = np.zeros(VOLUME_SHAPE, dtype=np.int16)
        header = {"spacings": list(VOLUME_SPACING)}
        src = tmp_path / "source.nrrd"
        nrrd.write(str(src), data, header)

        img = Image(dtype=np.int16)
        img.read(src)

        nifti_path = tmp_path / "out.nii.gz"
        img.save_as(nifti_path, FileType.NIFTI)

        img2 = Image(dtype=np.int16)
        img2.read(nifti_path)
        assert pytest.approx(img2.spacing, abs=1e-4) == VOLUME_SPACING

    def test_dicom_to_nifti_spacing(self, dicom_series_dir: Path, tmp_path: Path) -> None:
        """DICOM → NIfTI preserves spacing and origin."""
        img = Image(dtype=np.int16)
        img.read_dicom_series(dicom_series_dir)

        nifti_path = tmp_path / "out.nii.gz"
        img.save_as(nifti_path, FileType.NIFTI)

        img2 = Image(dtype=np.int16)
        img2.read(nifti_path)
        assert pytest.approx(img2.spacing, abs=1e-4) == VOLUME_SPACING
        assert pytest.approx(img2.origin, abs=1e-4) == (0.0, 0.0, 0.0)

    def test_dicom_to_nrrd_spacing(self, dicom_series_dir: Path, tmp_path: Path) -> None:
        """DICOM → NRRD preserves spacing."""
        img = Image(dtype=np.int16)
        img.read_dicom_series(dicom_series_dir)

        nrrd_path = tmp_path / "out.nrrd"
        img.save_as(nrrd_path, FileType.NRRD)

        img2 = Image(dtype=np.int16)
        img2.read(nrrd_path)
        assert pytest.approx(img2.spacing, abs=1e-4) == VOLUME_SPACING

    def test_oblique_nifti_roundtrip(self, tmp_path: Path) -> None:
        """Rotated affine survives NIfTI → save → read roundtrip."""
        angle = np.radians(15)
        rotation = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0],
                [np.sin(angle), np.cos(angle), 0],
                [0, 0, 1],
            ]
        )
        spacing = VOLUME_SPACING
        origin = (5.0, -10.0, 25.0)

        affine = np.eye(4)
        affine[:3, :3] = rotation * np.array(spacing)
        affine[:3, 3] = origin

        data = np.zeros(VOLUME_SHAPE, dtype=np.int16)
        nib_img = nibabel.Nifti1Image(data, affine, dtype=np.int16)
        src = tmp_path / "oblique.nii.gz"
        nibabel.save(nib_img, str(src))

        img = Image(dtype=np.int16)
        img.read(src)
        assert pytest.approx(img.spacing, abs=1e-4) == spacing
        assert pytest.approx(img.origin, abs=1e-4) == origin
        np.testing.assert_array_almost_equal(img.direction, rotation, decimal=4)

        # Save and read back
        out = tmp_path / "oblique_out.nii.gz"
        img.save_as(out, FileType.NIFTI)

        img2 = Image(dtype=np.int16)
        img2.read(out)
        assert pytest.approx(img2.spacing, abs=1e-4) == spacing
        assert pytest.approx(img2.origin, abs=1e-4) == origin
        np.testing.assert_array_almost_equal(img2.direction, rotation, decimal=4)

    def test_oblique_nifti_to_nrrd_roundtrip(self, tmp_path: Path) -> None:
        """Rotated affine survives NIfTI → NRRD → NIfTI."""
        angle = np.radians(20)
        rotation = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0],
                [np.sin(angle), np.cos(angle), 0],
                [0, 0, 1],
            ]
        )
        spacing = VOLUME_SPACING
        origin = (1.0, 2.0, 3.0)

        affine = np.eye(4)
        affine[:3, :3] = rotation * np.array(spacing)
        affine[:3, 3] = origin

        data = np.zeros(VOLUME_SHAPE, dtype=np.int16)
        nib_img = nibabel.Nifti1Image(data, affine, dtype=np.int16)
        src = tmp_path / "oblique_src.nii.gz"
        nibabel.save(nib_img, str(src))

        # NIfTI → NRRD
        img = Image(dtype=np.int16)
        img.read(src)
        nrrd_path = tmp_path / "oblique.nrrd"
        img.save_as(nrrd_path, FileType.NRRD)

        # NRRD → NIfTI
        img2 = Image(dtype=np.int16)
        img2.read(nrrd_path)
        nifti_out = tmp_path / "oblique_roundtrip.nii.gz"
        img2.save_as(nifti_out, FileType.NIFTI)

        img3 = Image(dtype=np.int16)
        img3.read(nifti_out)
        assert pytest.approx(img3.spacing, abs=1e-4) == spacing
        assert pytest.approx(img3.origin, abs=1e-4) == origin
        np.testing.assert_array_almost_equal(img3.direction, rotation, decimal=4)

    def test_template_preserves_spatial(self, dicom_series_dir: Path, tmp_path: Path) -> None:
        """Origin and direction propagate through template creation."""
        source = Image()
        source.read_dicom_series(dicom_series_dir)

        copy = Image(template=source, copy_data=True)
        assert copy.origin == source.origin
        np.testing.assert_array_equal(copy.direction, source.direction)
        assert copy.spacing == source.spacing

        # Save template copy as NIfTI and verify spatial metadata
        out = tmp_path / "template_copy.nii.gz"
        copy.save_as(out, FileType.NIFTI)

        readback = Image()
        readback.read(out)
        assert pytest.approx(readback.spacing, abs=1e-4) == source.spacing
        assert pytest.approx(readback.origin, abs=1e-4) == source.origin
