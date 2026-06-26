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

**NRRD write (`_save_nrrd`)**: passes `_nrrd_header` if available, otherwise empty `{}`. Segment metadata (`Segment{i}_Name`/`_LabelValue`/`_Color`/...) is preserved but **reconciled to the labels actually present** in the voxel data — blocks for absent label values are dropped and survivors renumbered contiguously, and grid-dependent keys (`*_Extent`, `Segmentation_ReferenceImageExtentOffset`) are dropped (readers recompute the effective extent on load). This guarantees a written segmentation never names a label value absent from its data. Spacing is only embedded in NRRD when the source was also NRRD.

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

### Named Set Operations

All set operations are **label-based** (operate on connected-component labels), not purely voxel-based. Use the named methods below for configurable thresholds.

| Method | Semantics | Key Parameters |
|---|---|---|
| `a.intersection(b, *, min_overlap=1, min_overlap_ratio=None, strategy=None)` | Keep ROIs from `a` with sufficient overlap with `b` | `min_overlap`: min voxels (default 1). `min_overlap_ratio`: min fraction of label size. `strategy`: optional `MatchingStrategy` — see below. |
| `a.union(b)` | Binary union — all nonzero from both, result is single-valued | — |
| `a.difference(b, *, max_overlap=0, max_overlap_ratio=None, strategy=None)` | Keep ROIs from `a` with overlap below thresholds | `max_overlap`: max tolerated voxels. `max_overlap_ratio`: max fraction (now applied per component — see below). `strategy`: optional `MatchingStrategy`. |
| `a.symmetric_difference(b, *, min_overlap=1, min_overlap_ratio=None, max_overlap=0, max_overlap_ratio=None, strategy=None)` | Component-level symmetric difference (unmatched A + unmatched B) | Same threshold params as `intersection`/`difference`. `strategy`: optional `MatchingStrategy`. |

**Union flattening**: the result is always binary (values 0 or 1). With `autolabel=True`, connected regions become a single label. Separate regions get different labels.

**Strict difference** (`max_overlap=0`, the default): drops any label with nonzero overlap. Use `max_overlap=N` to tolerate small overlaps.

**`difference(max_overlap_ratio=...)`**: the ratio threshold is now applied and enforced (prior versions had an inert implementation). Setting `max_overlap_ratio=0.1` drops an A component only if its overlap with the largest single B component exceeds 10% of A's size.

**`symmetric_difference` is component-level**: produces the union of unmatched A and unmatched B components. This is cleaner than the prior implementation which called `union().difference(intersection())` and could introduce re-labeling artifacts.

**Per-edge overlap threshold (behavior note)**: for `intersection` and `difference`, the default no-`strategy` path evaluates the threshold against the **largest single B-component overlap**, not the summed overlap across all B components. This matches historical behavior for default thresholds (`min_overlap=1` / `max_overlap=0`) and for single-component overlaps. Consumers relying on raised thresholds with fragmented (multi-component) other masks should be aware: for example, two B components each overlapping A by 3 voxels (sum 6) do not trigger a `min_overlap=5` threshold — only the per-component max of 3 is tested.

#### Optional `strategy=` parameter

All four named operations accept an optional `strategy: MatchingStrategy` keyword argument. When provided, it replaces the default `ThresholdMatch`-based matching with a fully configurable correspondence engine.

```python
from clarinet.services.image.correspondence import GreedyArgmax, AbsoluteOverlap, IoU

# Resolve 1-to-N overlaps: each A picks its highest-IoU B partner
result = seg_a.difference(seg_b, strategy=GreedyArgmax(IoU(), direction="a_to_b"))
```

Available measures: `IoU`, `Dice`, `Coverage`, `OverlapCoefficient`, `AbsoluteOverlap`, `CentroidProximity`.

**Grid alignment (fail-fast)**: every set operation compares the two segmentations **by voxel index**, so both must occupy the same physical grid. By default a grid mismatch (different shape, origin, spacing, or direction — e.g. a Z-flipped projection vs. its doctor segmentation) raises `GeometryMismatchError` instead of silently producing wrong results. Pass `resample=True` to opt into automatic nearest-neighbour resampling of `other` onto the caller's grid. This mirrors ITK's "same physical space" guard plus an explicit `ResampleImageFilter`.

| Helper | Returns | Purpose |
|---|---|---|
| `a.same_grid(b, *, atol=1e-4)` | `bool` | Grids equal within tolerance (shape + affine). |
| `a.assert_same_grid(b, *, atol=1e-4)` | `None` | Raises `GeometryMismatchError` with a diagnostic if grids differ. |
| `conform_seg_to_grid(seg_path, grid_path, *, out_path=None)` | `bool` | Repair helper: resample a `.seg.nrrd` onto a reference volume's grid (in place or to `out_path`); returns whether a resample was needed. For batch-fixing historically misaligned files. |

### Deprecated Operators

The dunder operators delegate to the named methods above and emit `DeprecationWarning`. They preserve the old hardcoded thresholds for backward compatibility.

| Operator | Delegates to | Notes |
|---|---|---|
| `a & b` | `a.intersection(b, min_overlap=3)` | Old threshold was `> 2` |
| `a \| b` | `a.union(b)` | — |
| `a - b` | `a.difference(b)` | Default `max_overlap=0` (strict) |
| `a + b` | `a.union(b)` | — |
| `a ^ b` | `a.symmetric_difference(b, min_overlap=3)` | Inherits old `&` threshold |

Because they delegate to the named methods, the operators also **raise `GeometryMismatchError` on grid mismatch** — there is no `resample` opt-in on the operator form, so migrate to the named method (`a.union(b, resample=True)`) if you need resampling.

### In-Place Operations

| Method | Behavior |
|---|---|
| `subtract(other, *, resample=False)` | Zeros out voxels where `other` is nonzero (voxel-level, not label-level). Grid mismatch → `GeometryMismatchError` unless `resample=True`. |
| `append(other, *, strategy=None, resample=False)` | Merges ROIs from `other` into matching labels. Default (no `strategy`): raises `ValueError` if an ROI overlaps multiple labels. With `strategy`: resolves overlaps via the correspondence engine — each B component is merged into its best matching A label. Grid mismatch → `GeometryMismatchError` unless `resample=True`. |
| `copy_from(other)` | Replaces voxel data entirely |
| `separate_labels()` | Re-runs connected-component labeling |

**`append` with `strategy=`**: pass a `MatchingStrategy` to resolve multi-label overlaps instead of raising. The B component's voxels are repainted with the matched A label value.

```python
from clarinet.services.image.correspondence import GreedyArgmax, AbsoluteOverlap

# merge bridging ROIs into their best-matching existing label
seg.append(other, strategy=GreedyArgmax(AbsoluteOverlap(), direction="b_to_a"))
```

Unmatched B components (no A partner found by the strategy) are silently dropped.

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

Implemented in `dicom_volume.py` as a thin wrapper around `SimpleITK.ImageSeriesReader` + GDCM.
Supports compressed (JPEG/JPEG2000/JPEG-LS), Enhanced multi-frame, and vendor-specific DICOMs out of the box.

### File Discovery

1. `sitk.ImageSeriesReader.GetGDCMSeriesIDs(directory)` — GDCM scans by file content (DICM magic bytes), not extension
2. Raises `ImageReadError` if no DICOM series detected
3. If multiple series are present, the **first** is selected and a WARNING is logged; the rest are ignored

### Error Tolerance

- Files GDCM does not recognize as DICOM are silently ignored
- Raises `ImageReadError` when no series is detected, when the chosen series has no files, or when `Execute()` fails (`RuntimeError` → `ImageReadError`)

### Slice Sorting

GDCM sorts slices by projection of `ImagePositionPatient` onto the slice direction (handles oblique acquisitions).
Falls back to `InstanceNumber` when position metadata is unavailable.

### Spacing, Origin, Direction

Pulled directly from the resulting `sitk.Image`:
- `GetSpacing()` returns `(x, y, z)`, mapped to internal `(row=y, col=x, slice=z)`
- `GetOrigin()` returns `(x, y, z)` in LPS, used as-is
- `GetDirection()` reshaped to a 3×3 matrix; columns are reordered from `(x, y, z)` to `(y, x, z)` to match the numpy axis convention

`RescaleSlope`/`RescaleIntercept` are applied automatically by GDCM during `Execute()`.

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
