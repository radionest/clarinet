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

**NRRD read failures** (breaking): `read`/`read_nrrd` raise `ImageReadError` on a 4-D `.seg.nrrd` — read layered segmentations via `LayeredSegmentation` or `grid_io.read_grid` instead of building a degenerate NaN grid — and on a 3-D NRRD whose `space directions` are present without a supported `space` field. The `space` field is now honored (LPS as-is, RAS/LAS converted, anything else — including missing `space` — raises), so a third-party RAS/LAS file that was previously misread as LPS now fails loudly; this only affects non-Slicer files, since Slicer always writes LPS.

**DICOM slice sorting**: GDCM orders by `ImagePositionPatient` projected onto the slice normal (→ `InstanceNumber` fallback); the reader then canonicalizes the slice axis to a version-stable orientation (see "Slice-Axis Canonicalization" below).

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

### RAM-Lean Reads (opt-in)

All read knobs are additive — the defaults (`load_data=True`, `dtype=None`) reproduce
today's behavior exactly.

| Knob | Effect |
|---|---|
| `read(path, load_data=False)` | Populates grid metadata + `shape` from the header only; `img` is left unloaded (`has_data` stays `False`). `nibabel.load()` is already lazy, so a NIfTI header read touches no data block; NRRD uses `nrrd.read_header()`. The #452 lean path — grid checks (`same_grid`, `affine_4x4`, `shape`) work without ever materializing voxels. |
| `read(path, dtype=np.int16\|bool\|...)` | Casts once, off-disk: NIfTI loads via `np.asarray(dataobj, dtype=dtype)` (skips the `get_fdata()` float64 intermediate); NRRD casts pynrrd's native dtype via `astype(dtype, copy=False)`. `dtype=None` keeps the historical behavior — NIfTI `get_fdata()` (`float64`), NRRD native. |
| `read_slice(path, index, *, axis=2, dtype=None)` | Returns a single 2-D slice without materializing the volume. NIfTI is lazy via `dataobj` (indexes before reading — only the slice's region is decoded); `.nii.gz` still sequential-decompresses up to `index` but returns one small array. NRRD has no lazy proxy, so it's a full read then index (rare in practice — NRRD segmentations go through `LayeredSegmentation`). Also populates grid metadata/`shape`, like `load_data=False`. |
| `dataobj` | Read-only lazy array proxy (NIfTI only) for repeated windowed access after a metadata-only read (e.g. a streaming nonzero check). Raises `ImageError` for NRRD/DICOM — pynrrd has no lazy proxy, so there is nothing to expose. |
| `unload()` | Drops the resident voxel array (frees up to a full `float64` volume) but keeps grid metadata/`shape` — the image stays usable for grid checks. |
| `close()` | `unload()` plus drops the NIfTI lazy proxy (`_nifti_image`), releasing its mmap. Called by `__exit__`. |
| `with Image() as im:` | Context-manager form of `close()` — deterministic free at block exit. |

**`Segmentation` reads route at uint8.** `Segmentation.read_nifti`/`read_nrrd` force
`dtype=np.uint8` when the caller doesn't pass one, so a mask read never passes through
the `float64` `get_fdata()` path — the `img` setter already casts to `uint8`, so this is
observably identical, just without the wasted float64 intermediate.

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
| `a.difference(b, *, max_overlap=0, max_overlap_ratio=None, granularity="label", strategy=None)` | Keep ROIs from `a` with overlap below thresholds | `max_overlap`: max tolerated voxels. `max_overlap_ratio`: max fraction (now applied per component — see below). `granularity`: `"label"` (default) scores each `b` label separately; `"union"` flattens `b` to one mask (sum-over-union). `strategy`: optional `MatchingStrategy`. |
| `a.symmetric_difference(b, *, min_overlap=1, min_overlap_ratio=None, max_overlap=0, max_overlap_ratio=None, strategy=None)` | Component-level symmetric difference (unmatched A + unmatched B) | Same threshold params as `intersection`/`difference`. `strategy`: optional `MatchingStrategy`. |

**Union flattening**: the result is always binary (values 0 or 1). With `autolabel=True`, connected regions become a single label. Separate regions get different labels.

**Strict difference** (`max_overlap=0`, the default): drops any label with nonzero overlap. Use `max_overlap=N` to tolerate small overlaps.

**`difference(max_overlap_ratio=...)`**: the ratio threshold is now applied and enforced (prior versions had an inert implementation). Setting `max_overlap_ratio=0.1` drops an A component only if its overlap with the largest single B component exceeds 10% of A's size.

**`symmetric_difference` is component-level**: produces the union of unmatched A and unmatched B components. This is cleaner than the prior implementation which called `union().difference(intersection())` and could introduce re-labeling artifacts.

**Per-edge overlap threshold (behavior note)**: for `intersection` and `difference`, the default no-`strategy` path evaluates the threshold against the **largest single B-component overlap**, not the summed overlap across all B components. This matches historical behavior for default thresholds (`min_overlap=1` / `max_overlap=0`) and for single-component overlaps. Consumers relying on raised thresholds with fragmented (multi-component) other masks should be aware: for example, two B components each overlapping A by 3 voxels (sum 6) do not trigger a `min_overlap=5` threshold — only the per-component max of 3 is tested. For `difference`, passing `granularity="union"` flattens the other mask to a single label first, so each A component is scored against the combined extent — the summed behavior.

#### Optional `strategy=` parameter

The three matching-based operations (`intersection`, `difference`, `symmetric_difference`) accept an optional `strategy: MatchingStrategy` keyword argument (`union` is a plain binary OR with nothing to match). When provided, it replaces the default `ThresholdMatch`-based matching with a fully configurable correspondence engine.

```python
from clarinet.services.image.correspondence import GreedyArgmax, AbsoluteOverlap, IoU

# Resolve 1-to-N overlaps: each A picks its highest-IoU B partner
result = seg_a.difference(seg_b, strategy=GreedyArgmax(IoU(), direction="a_to_b"))
```

Available measures: `IoU`, `Dice`, `Coverage`, `OverlapCoefficient`, `AbsoluteOverlap`, `CentroidProximity`.

**Grid alignment (fail-fast)**: every set operation compares the two segmentations **by voxel index**, so both must occupy the same physical grid. By default a grid mismatch (different shape, origin, spacing, or direction — e.g. a Z-flipped projection vs. its inspector segmentation) raises `GeometryMismatchError` instead of silently producing wrong results. Pass `resample=True` to opt into automatic nearest-neighbour resampling of `other` onto the caller's grid. This mirrors ITK's "same physical space" guard plus an explicit `ResampleImageFilter`.

| Helper | Returns | Purpose |
|---|---|---|
| `a.same_grid(b, *, atol=1e-4)` | `bool` | Grids equal within tolerance (shape + affine). |
| `a.assert_same_grid(b, *, atol=1e-4)` | `None` | Raises `GeometryMismatchError` with a diagnostic if grids differ. |
| `conform_seg_to_grid(seg_path, grid_path, *, out_path=None, atol=1e-4, allow_resample=False)` | `bool` | Repair helper: classifies the pair via `grid_relation` first — `SAME` is a no-op (`False`), `REARRANGED` (mirror/transpose) repairs by an exact index rearrangement (3-D and 4-D layered `.seg.nrrd`, layer/label-preserving), `FOREIGN` raises `GeometryMismatchError` unless `allow_resample=True`. For batch-fixing historically misaligned files. See [`docs/grid-workflows.md`](/docs/grid-workflows.md) for the SAME/REARRANGED/FOREIGN decision table. |

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

## LayeredSegmentation Class

Models overlapping segments (e.g. `psoas` ⊆ `skeletal_muscle`) as a 4-D `(L, X, Y, Z)`
`uint8` NRRD over one shared 3-D grid — the layout Slicer uses for multi-layer
`.seg.nrrd` files. Lives in `layered_segmentation.py`
(`from clarinet.services.image import LayeredSegmentation`). This is **composition,
not a 4-D `Segmentation`**: each materialized layer is a normal 3-D array on the
shared grid, so `Image`/`Segmentation`'s 3-D invariants stay 3-D.

### Construction

```python
LayeredSegmentation.from_layers(
    [(name, mask3d), ...],
    spacing=(x, y, z), origin=(x, y, z), direction=direction_3x3,
)
```

One segment per layer, label `1` — the only construction path currently offered. All
masks must share the same 3-D shape; a mismatch or an empty `layers` list raises
`ImageError`. `segments` (`list[tuple[name, layer_index, label_value]]`) is populated
either way — by `from_layers` at construction, or by `read_header` from the file's
`Segment{i}_*` header blocks.

### NRRD Header Contract (Slicer format)

| Header key | Value |
|---|---|
| `dimension` | `4` |
| `sizes` | `[L, X, Y, Z]` — layer/list axis **first** |
| `kinds` | `["list", "domain", "domain", "domain"]` |
| `space directions` | row 0 is `nan` (the list axis has no direction); rows 1–3 are the 3×3 spatial directions |
| `encoding` | `raw` |
| `Segment{i}_Name` / `_LabelValue` / `_Layer` / `_ID` | per-segment metadata block, one per segment index `i` — `_Layer` maps the segment into the shared 4-D array (multiple non-overlapping segments may share a layer) |

This is Slicer's native multi-layer `.seg.nrrd` layout, so **layers are interleaved
byte-by-byte on disk** (F-order, layer axis fastest) — not layer-contiguous.

### Writing

`save(path)` pre-allocates the full 4-D `uint8` array once and fills each layer in
place, releasing (`None`-ing out) each source mask as it goes — avoids the transient
doubling `np.stack` would cause. **Single-use**: a `LayeredSegmentation` built via
`from_layers` can only be `save()`d once — a second call raises `ImageWriteError`
because the source arrays were already released on the first call.

### Reading

| Method | Returns | Voxels touched |
|---|---|---|
| `LayeredSegmentation.read_header(path)` | New instance with grid + `segments` populated | none — `nrrd.read_header()` only |
| `read_layer(path, name_or_index)` | One 3-D layer array | full 4-D read, then indexed |
| `read_layer_slice(path, name_or_index, index, *, axis=2)` | One 2-D slice of one layer | full 4-D read, then indexed |
| `iter_layers(path)` | `Iterator[(name, layer_array)]` | one full 4-D read, shared across all yields |

`name_or_index` resolves a `str` segment name through `segments` (→ its `_Layer`
index), or takes an `int` layer index directly; an unknown name raises `ImageError`.

**RAM behavior**: pynrrd has no lazy/seek proxy, so every read method above does one
full 4-D `nrrd.read()` and then numpy-indexes the requested layer — the read floor is
always one full 4-D `uint8` array, regardless of how many layers are actually needed.
This is layout-agnostic (correct regardless of on-disk byte order) but not lazy. A
seek-based per-layer reader that would avoid the full 4-D read is **out of scope
here** and deferred to the follow-up issue
[#454](https://github.com/radionest/clarinet/issues/454): because layers are
interleaved rather than layer-contiguous, such a reader would need strided reads per
layer, not one contiguous seek — so `encoding: raw` currently buys no read-side
payoff (only extra disk, ~4.2 GB/file at max size). #454 will build the seek-based
reader and re-evaluate `raw` vs `gzip` at that point.

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

### Ground-Truth Slice-Axis Correction (#453)

Before canonicalization, `ground_truth_slice_geometry` (`orientation.py`) recomputes the
slice-axis sense and origin directly from the first/last file's `ImagePositionPatient`
(read via pydicom, independent of SimpleITK). On long axial series with sub-mm spacing
wobble, `GetDirection()`'s slice-axis sign can contradict the actual GDCM file order,
producing an anatomically flipped volume; IPP is never inconsistent, so it is the
authority. For a series SimpleITK already read correctly, the override is a no-op — the
canonical grid stays byte-identical. It also returns the exact last-slice IPP, which
canonicalization uses verbatim for the flipped origin instead of extrapolating
`slice_spacing * (n - 1)` from a single nominal spacing value.

`orientation.py` also exposes `is_volume_misoriented(volume_nifti, dicom_dir)`, a
detection primitive for auditing already-converted volumes (see
`clarinet/docs/migration-orientation-0.10.17.md`); it raises `OrientationUnverifiable`
rather than guessing when ground truth can't be established (non-axial series,
unreadable DICOM tags).

### Slice-Axis Canonicalization

The reader normalizes the slice axis so it points along the **canonical sense of the IOP
normal** — the side of `cross(direction[:, 0], direction[:, 1])`, the physical normal implied
by the DICOM row/column directions (`_canonicalize_slice_axis`). Since
`det([row, col, slice]) = normal · slice_dir`, this makes the emitted determinant **positive for
every series with a non-degenerate `ImageOrientationPatient`**; it falls back to the previous
fixed positive-dominant-axis rule (plus a logged warning) only when the in-plane columns are
degenerate or the slice axis is too close to orthogonal to the normal to judge reliably. This
makes the conversion **reproducible across framework versions**: a series re-converted later
(repair, anonymization path migration, manual re-run) lands on the *identical* voxel grid.
Without it, the slice-ordering convention drifts between readers — the pre-#221 hand-written
reader sorted by ascending `ImagePositionPatient[2]`, while GDCM sorts along the IOP slice
normal, which can point either way — so a segmentation frozen on one grid and another frozen on
the other end up on physically equivalent but **index-reversed** grids (the projection/inspector-seg
Z-flip). The flip is **geometry-preserving**: the array, origin, and slice direction are reversed
*together*, so every voxel keeps its physical position (a direction-only flip would mirror the
data). No-op when the slice axis already points the canonical way.

See also: [`docs/grid-workflows.md`](/docs/grid-workflows.md) for the design rationale behind
the IOP-normal canonical sense, the live-Slicer probe evidence, and the full
`Grid`/`GridRelation`/`RelationKind` vocabulary.

### Spacing, Origin, Direction

Pulled from the resulting `sitk.Image`, then passed through slice-axis canonicalization.
The array, spacing, and direction all use SimpleITK's own `(x, y, z)` axis order — DICOM
IOP in-plane order, no in-plane row/column swap:
- `GetArrayFromImage()`'s `(z, y, x)` becomes the internal `(x, y, z)` array via `transpose(2, 1, 0)` — matches `GetSize()`'s axis order
- `GetSpacing()` returns `(x, y, z)`, used as-is
- `GetOrigin()` returns `(x, y, z)` in LPS, first overridden by the ground-truth IPP correction above — moved to the opposite slice end (the exact last-slice IPP, not an extrapolation) if the axis is flipped
- `GetDirection()` reshaped to a 3×3 matrix is used as-is — column *i* already matches array axis *i*, so no reorder is needed. Only the slice column (axis 2) is still adjusted, by the ground-truth IPP correction and `_canonicalize_slice_axis`'s sign normalization above; the in-plane columns (0, 1) pass through untouched

`RescaleSlope`/`RescaleIntercept` are applied automatically by GDCM during `Execute()`.

---

## Testing

| File | Scope | Count |
|---|---|---|
| `tests/test_image.py` | Unit tests — individual methods in isolation | 125 |
| `tests/test_image_e2e.py` | E2E workflow tests — multi-step pipelines | 17 |
| `tests/test_orientation.py` | Unit tests — `ground_truth_slice_geometry` / `is_volume_misoriented` | 13 |

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
