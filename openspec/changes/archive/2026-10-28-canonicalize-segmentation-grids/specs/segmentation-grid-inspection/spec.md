## ADDED Requirements

### Requirement: Grid value object in a pure, bundleable core

The framework SHALL provide a `Grid` value object — spatial shape plus voxel-to-world affine,
always expressed in LPS — in a module that imports only numpy and the standard library (no
clarinet-internal imports, no file-format libraries), so the identical source can execute inside
3D Slicer via the script bundle. `Image` and `LayeredSegmentation` SHALL expose their grid as a
`Grid` (`.grid` property), and the existing in-memory `same_grid`/`assert_same_grid` SHALL
delegate to it without signature changes. Grid summaries used in mismatch diagnostics SHALL be
produced by the value object (one formatting authority).

#### Scenario: pure core is bundleable
- **WHEN** the grid core module's source is flattened into the Slicer script bundle and executed
  in a namespace without clarinet, nibabel, or pynrrd
- **THEN** `Grid` construction and relation classification SHALL work unchanged

#### Scenario: one summary authority
- **WHEN** a grid mismatch error is raised by any framework guard
- **THEN** both grid descriptions in the message SHALL come from `Grid.summary()`

---

### Requirement: Format-agnostic, 4-D-safe grid reading

The framework SHALL provide `read_grid(path) -> Grid` for any supported on-disk image. It SHALL
return the **spatial** grid: a 4-D multi-layer segmentation's layer (list) axis SHALL NOT be
treated as spatial, and a 4-D NIfTI SHALL report its spatial 3-axis shape. The NRRD `space` field
SHALL be honored: LPS read as-is, RAS/LAS converted into LPS, any other value SHALL raise
`ImageReadError` (never a silent wrong-coordinate read).

#### Scenario: 3-D NIfTI and NRRD grid read
- **WHEN** `read_grid` is called on a 3-D `.nii.gz` or `.nrrd`
- **THEN** it SHALL return the volume's spatial shape and its LPS voxel-to-world affine

#### Scenario: 4-D multi-layer segmentation grid read
- **WHEN** `read_grid` is called on a 4-D `(L, X, Y, Z)` Slicer `.seg.nrrd`
- **THEN** it SHALL return the spatial `(X, Y, Z)` grid equal to the layered reader's grid

#### Scenario: RAS-native NRRD is converted, not misread
- **WHEN** `read_grid` reads an NRRD declaring `space: right-anterior-superior`
- **THEN** the returned affine SHALL express the same physical grid in LPS

#### Scenario: coordinate system is always LPS
- **WHEN** `read_grid` reads a RAS-native NIfTI and an LPS-native NRRD of the same physical volume
- **THEN** both returned affines SHALL express that same physical grid in LPS

---

### Requirement: Grid relation classification by the signed-permutation predicate

The framework SHALL provide `grid_relation(a, b)` over two `Grid`s returning a `GridRelation` of
exactly one of `SAME`, `REARRANGED`, or `FOREIGN`. With `M = affine_a⁻¹ @ affine_b`:
`REARRANGED` SHALL hold iff `M`'s linear part is a signed permutation matrix and the offset maps
voxel centers exactly onto voxel centers (per negated axis: offset `n−1`; per positive axis: `0`;
within half a voxel; shapes permuted accordingly) — i.e. the grids describe physically-coincident,
index-rearranged voxels. `SAME` is the identity case; everything else is `FOREIGN`. A `REARRANGED`
result SHALL carry the permutation and per-axis flips so callers and error messages can name the
specific relation (slice mirror, in-plane transpose, ...).

#### Scenario: identical grids
- **WHEN** `grid_relation` is given two images on the same grid
- **THEN** it SHALL return `SAME`

#### Scenario: slice-axis mirror
- **WHEN** given two grids of equal shape whose slice axis is negated with the compensating origin
- **THEN** it SHALL return `REARRANGED` with detail identity-permutation + slice-axis flip

#### Scenario: in-plane transpose (the conversion-epoch relation)
- **WHEN** given a grid and its in-plane-transposed counterpart (as produced by re-converting a
  legacy volume under the canonical converter)
- **THEN** it SHALL return `REARRANGED` with detail axis-permutation `0↔1`, no flips

#### Scenario: oblique mirror
- **WHEN** given an oblique (non-axis-aligned direction) grid and its slice-mirrored counterpart
- **THEN** it SHALL return `REARRANGED` (the predicate operates in index space, not world axes)

#### Scenario: foreign grids
- **WHEN** given two grids that are shifted by a non-integral offset, rotated, or differ in shape
  beyond a permutation
- **THEN** it SHALL return `FOREIGN`

---

### Requirement: Disk-level grid-identity assertion

The framework SHALL provide `assert_same_grid_on_disk(path_a, path_b, *, atol=1e-4)` that reads
both grids from disk via `read_grid` and raises `GeometryMismatchError` — carrying both
`Grid.summary()` strings — when the relation is not `SAME`. Because it re-reads written files, it
SHALL detect a mirror that an in-memory comparison of two consistently-flipped objects cannot.

#### Scenario: matching on-disk grids pass
- **WHEN** called on two files that share a grid
- **THEN** it SHALL return without raising

#### Scenario: mirrored on-disk grids raise
- **WHEN** called on a volume and a segmentation written mirrored relative to it
- **THEN** it SHALL raise `GeometryMismatchError` naming both grids

#### Scenario: catches what the in-memory guard misses
- **WHEN** a segmentation exported from an ITK-canonicalized volume node passes the in-memory
  reference-geometry comparison yet is written mirrored on disk
- **THEN** `assert_same_grid_on_disk` against the on-disk volume SHALL raise

---

### Requirement: Guarded, 4-D-safe repair

`conform_seg_to_grid` SHALL classify the segmentation-vs-reference relation before acting:
`SAME` → no-op; `REARRANGED` → exact index rearrangement (lossless), supporting both 3-D files and
4-D multi-layer `.seg.nrrd` (per-layer rearrangement; segment names, label values, colors, and
layer structure preserved); `FOREIGN` → raise `GeometryMismatchError` unless the caller passes
`allow_resample=True` to explicitly opt into true nearest-neighbour resampling. The safe behavior
is the default.

#### Scenario: mirrored segmentation repaired exactly
- **WHEN** `conform_seg_to_grid` runs on a slice-mirrored segmentation against its volume
- **THEN** the written file SHALL share the volume's grid and every voxel SHALL keep its physical
  position (pure index flip, no interpolation)

#### Scenario: 4-D multi-layer repair
- **WHEN** `conform_seg_to_grid` runs on a mirrored 4-D `.seg.nrrd` with overlapping segments
- **THEN** the repaired file SHALL keep its layer count and per-segment metadata
- **AND** each layer SHALL be exactly rearranged onto the reference grid

#### Scenario: foreign grid refused by default
- **WHEN** the segmentation's grid is FOREIGN to the reference
- **THEN** `conform_seg_to_grid` SHALL raise `GeometryMismatchError` and write nothing

#### Scenario: explicit opt-in to resample
- **WHEN** the caller passes `allow_resample=True` for a FOREIGN pair
- **THEN** the segmentation SHALL be nearest-neighbour resampled onto the reference grid

---

### Requirement: 3-D readers fail loudly on 4-D input

`Image.read_nrrd` SHALL raise `ImageReadError` when the file is not 3-D, directing the caller to
`LayeredSegmentation`/`read_grid`, instead of silently constructing a grid from the list-axis row.

#### Scenario: 4-D NRRD through the 3-D reader
- **WHEN** `Image.read` is called on a 4-D multi-layer `.seg.nrrd`
- **THEN** it SHALL raise `ImageReadError` naming the 4-D-capable alternatives
- **AND** it SHALL NOT return an image with NaN spacing or direction
