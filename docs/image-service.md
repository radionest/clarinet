# Image Processing Service — Behavioral Reference

Detailed documentation for `clarinet/services/image/`.

## Image Class

### Construction

```python
Image(template=None, copy_data=False, dtype=None)
```

- `template`: copies `_source_path`, `spacing`, `_nifti_image`, `_nrrd_header`, `_filetype` from another Image
- `copy_data=True`: deep-copies voxel array; `False`: creates zero-filled array of same shape
- `dtype`: forces all voxel data to this numpy dtype on every `img` assignment

### Reading

| Method | Dispatched by | Sets `_filetype` | Sets `_nifti_image` | Sets `_nrrd_header` |
|---|---|---|---|---|
| `read(path)` | File extension (`.nii` → NIfTI, `.nrrd` → NRRD) | yes | if NIfTI | if NRRD |
| `read_nifti(path)` | Direct call | `NIFTI` | yes | no |
| `read_nrrd(path)` | Direct call | `NRRD` | no | yes |
| `read_dicom_series(dir)` | Direct call | `DICOM` | no | no |

**NIfTI reading**: `get_fdata()` returns `float64` by default. Use `dtype=np.int16` to force integer dtype for cross-format comparisons.

**NRRD spacing resolution**: tries `spacings` key first, then `space directions` diagonal. Falls back to default `(1.0, 1.0, 1.0)` if neither is present.

**DICOM slice sorting**: `ImagePositionPatient[2]` (Z-coordinate) → `InstanceNumber` → file order.

**DICOM error tolerance**: non-DICOM files and DICOM files without `pixel_array` are silently skipped. Only raises `ImageReadError` if zero valid slices remain.

### Writing

| Method | Behavior |
|---|---|
| `save(filename, directory)` | Saves in the original format; appends correct extension. Raises `ImageError` for DICOM. |
| `save_as(path, filetype)` | Saves at exact path in specified format. Raises `ImageError` for DICOM. |

**NIfTI write (`_save_nifti`)**: uses `_nifti_image.affine` if available, otherwise `np.eye(4)`. This means spacing is only preserved in the NIfTI affine when the source was also NIfTI.

**NRRD write (`_save_nrrd`)**: passes `_nrrd_header` (minus `Segment*` keys) if available, otherwise empty `{}`. Spacing is only embedded in NRRD when the source was also NRRD.

### Cross-Format Spacing Preservation

| Source → Target | Spacing preserved? | Why |
|---|---|---|
| NIfTI → NIfTI | yes | Affine copied from `_nifti_image` |
| NRRD → NRRD | yes | Header copied from `_nrrd_header` |
| NIfTI → NRRD | **no** | `_nrrd_header` is None → empty header → no spacing |
| NRRD → NIfTI | **no** | `_nifti_image` is None → `np.eye(4)` → spacing `(1,1,1)` |
| DICOM → NIfTI | **no** | `_nifti_image` is None → `np.eye(4)` → spacing `(1,1,1)` |
| DICOM → NRRD | **no** | `_nrrd_header` is None → empty header → no spacing |

Voxel data is always preserved regardless of format conversion. Spacing loss is a metadata-only issue.

---

## Segmentation Class

### Construction

```python
Segmentation(autolabel=True, template=None, copy_data=False)
```

Inherits from `Image` with `dtype=np.uint8` forced. The `autolabel` flag controls whether connected-component labeling runs on every `img` assignment.

### img Setter (Autolabel)

When `autolabel=True` (default), every assignment to `seg.img = array` runs `skimage.measure.label()` and casts to `uint8`. This means:
- Input label values are **not preserved** — connected components get sequential labels starting at 1
- Tests should check voxel occupancy (nonzero positions), not specific label values
- Two adjacent blobs in the input may merge into one label if they touch

When `autolabel=False`, the array is only cast to `uint8` without relabeling. Use this when label values must be preserved (e.g., reading back saved segmentations, multi-class labels from HU correction).

### Region Properties

`label_props` is lazily cached and invalidated on every `img` assignment. Uses `regionprops(self.img, spacing=self.spacing)`.

Available filter properties (`PropName`): `"axis_major_length"`, `"num_pixels"`, `"area"`.

### Morphological Operations

| Method | Behavior | Note |
|---|---|---|
| `dilate(radius)` | Expands binary mask | Uses isotropic dilation (spacing-aware) when Z < 200; ball footprint otherwise |
| `binary_open(radius)` | Erosion + dilation | Same isotropic/ball switching. Removes small protrusions, fills small holes |

Both operations convert to binary (`img > 0`) before applying, so label information is lost.

### HU Correction (`rois_hu_correction`)

Pipeline:
1. **Dilate** the labeled mask by `radius` using `ball(radius)`
2. **Constrain** to `white_mask` if provided (zero out where mask is 0, preserve original labels)
3. **Filter** by HU range: zero out voxels where `hu_image < min_hu` or `> max_hu`
4. **Opening** with `ball(2)` — removes regions smaller than ~4x4x4 voxels
5. **Keep largest component** per label via `regionprops` sorted by area

Blobs must be at least ~4x4x4 voxels to survive the `opening(ball(2))` step.

### ROI Filtering

| Method | Returns | Mutates self? |
|---|---|---|
| `filtered_props(prop, ge, le)` | `list[_RegionProperties]` | no |
| `filter_roi(prop, ge, le)` | Binary `np.ndarray` (uint8) | no |
| `filter_segmentation(prop, ge, le)` | New `Segmentation` | no |

### Set Operations

All set operations are **label-based** (operate on connected-component labels), not purely voxel-based. This has non-obvious consequences:

| Operator | Semantics | Returns |
|---|---|---|
| `a & b` | Keep ROIs from `a` where overlap with `b` exceeds 2 voxels | New `Segmentation` |
| `a \| b` | Binary union — all nonzero from both, result is single-valued | New `Segmentation` |
| `a - b` | Keep ROIs from `a` with no overlap, or overlap < 10 voxels | New `Segmentation` |
| `a + b` | Binary union (same as `\|` but via direct assignment) | New `Segmentation` |
| `a ^ b` | `(a \| b) - (a & b)` | New `Segmentation` |

**Subtraction edge case (`__sub__`)**: keeps a label if overlap is 0, or if overlap < 10 voxels AND the label exists in self. When overlapping cubes form a single connected component in the union, XOR may return an empty result because the subtraction drops the entire unified label.

**Union flattening (`__or__`)**: the result is always binary (values 0 or 1). With `autolabel=True`, connected regions become a single label. Separate regions get different labels.

### In-Place Operations

| Method | Behavior |
|---|---|
| `subtract(other)` | Zeros out voxels where `other` is nonzero (voxel-level, not label-level) |
| `append(other)` | Merges ROIs from `other` into matching labels. Raises `ValueError` if an ROI overlaps multiple labels. |
| `copy_from(other)` | Replaces voxel data entirely |
| `separate_labels()` | Re-runs connected-component labeling |

---

## COCO Converter

### Data Model

```
COCODataset
├── info: COCOInfo (mode, studyInstanceUID, dateTime)
├── categories: list[COCOCategory] (id, name, description)
├── images: list[COCOImage] (id, width, height, numberOfFrames, seriesInstanceUID, sopInstanceUID)
└── annotations: list[COCOAnnotation] (id, imageId, categoryId, area, bbox, frameNumber, segmentation)
```

### `coco_to_segmentation(coco_json_path, volume, separate_labels=True)`

1. Parses COCO JSON into `COCODataset` (Pydantic validation)
2. For each annotation: rasterizes polygon via `skimage.draw.polygon2mask` into a 2D mask
3. Places mask at `output.img[mask > 0, frameNumber] = 1`
4. **Flips Y axis** (`output.img[:, ::-1, :]`) to match NIfTI orientation convention
5. If `separate_labels=True`, autolabel relabels connected components

Annotations referencing unknown `imageId` are logged as warnings and skipped.

---

## DICOM Volume Reader

### File Discovery

1. `sorted(directory.glob("*.dcm"))` — looks for `.dcm` files first
2. If none found, falls back to all non-hidden files in the directory (common in PACS exports)
3. Raises `ImageReadError` if no files found

### Error Tolerance

- Files that fail `pydicom.dcmread()` are silently skipped (logged at DEBUG)
- DICOM files without `pixel_array` attribute are silently skipped
- Only raises `ImageReadError` when zero valid datasets remain
- `ValueError` from `np.stack` (inconsistent dimensions) is wrapped as `ImageReadError`

### Slice Sorting

Priority order:
1. `ImagePositionPatient[2]` (Z-coordinate) — most reliable
2. `InstanceNumber` — fallback when position unavailable
3. File order — last resort (with WARNING log)

### Spacing Extraction

| Component | Primary source | Fallback |
|---|---|---|
| Row/col spacing | `PixelSpacing[0], [1]` | `1.0` (with WARNING) |
| Slice spacing | `abs(ImagePositionPatient[2] difference)` between first two slices | `SliceThickness` → `1.0` |

---

## Testing

| File | Scope | Count |
|---|---|---|
| `tests/test_image.py` | Unit tests — individual methods in isolation | 39 |
| `tests/test_image_e2e.py` | E2E workflow tests — multi-step pipelines | 9 |

### E2E Test Scenarios

| Class | What it tests |
|---|---|
| `TestFormatConversionPipeline` | NIfTI↔NRRD↔NIfTI roundtrips, DICOM→NIfTI conversion |
| `TestCOCOAnnotationPipeline` | COCO ingest → dilate → filter → save → readback |
| `TestSegmentationProcessingChain` | Set operations chain (`&`, `\|`, `^`) with persistence |
| `TestHUCorrectionWorkflow` | HU range filtering with 3 distinct regions |
| `TestTemplatePropagation` | DICOM → Image template → Segmentation template → copy_from |
| `TestMultiFormatRoundtrip` | NIfTI vs NRRD cross-format voxel equality |
| `TestDegradedDICOMInput` | Mixed valid/invalid/no-pixel DICOM tolerance |
