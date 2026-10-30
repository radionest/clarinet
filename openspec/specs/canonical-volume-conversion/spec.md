# canonical-volume-conversion Specification

## Purpose
TBD - created by archiving change canonicalize-segmentation-grids. Update Purpose after archive.
## Requirements
### Requirement: Canonical right-handed emission for every non-degenerate series

The DICOM→NIfTI converter SHALL emit a volume that is physically faithful to the DICOM geometry —
every voxel keeps its `ImagePositionPatient`/`ImageOrientationPatient` world position; the emitted
volume is never a mirror of the source — and, for every series whose IOP yields a non-degenerate
normal, the emitted direction matrix SHALL have a **positive determinant**, so no ITK runtime
canonicalizes (and thus re-grids) it at load. This is achieved by two geometry-preserving
representation choices:

1. **In-plane basis in DICOM IOP order**: the internal in-plane axis swap is removed
   **end-to-end** — array layout and direction matrix move together, and `read_dicom_series` and
   a re-read of the emitted file report the same grid. The emitted file SHALL NOT be re-oriented
   at write time while the in-memory volume keeps a different order.
2. **Slice sense on the IOP-normal side**: the slice-axis canonicalization flip
   (geometry-preserving: array + origin + direction together) SHALL trigger when the IPP-derived
   slice direction opposes the IOP normal `n = row × col`, so `det = n·s > 0` always. The
   IPP-derived slice **line** and origin ground truth (#247/#453) remain authoritative; only the
   canonical sense changes anchor (was: +dominant axis).

A direction-only flip of any axis SHALL never occur (that repositions voxels — the #247/#453
mirror class). When the IOP normal is degenerate or unavailable, the converter SHALL fall back to
the +dominant-axis sense deterministically and log a warning; such volumes remain covered by
conform-on-export.

#### Scenario: standard axial series is right-handed and grid-faithful
- **WHEN** a series with IOP row=(1,0,0), col=(0,1,0) stacking along the +normal is converted
- **THEN** the emitted determinant SHALL be positive
- **AND** `grid_relation(emitted_volume, dicom_ground_truth_grid)` SHALL be `SAME`

#### Scenario: stacking opposing the normal still emits right-handed, unmirrored
- **WHEN** a series whose IPP progression opposes its IOP normal is converted
- **THEN** the emitted determinant SHALL be positive (geometry-preserving sense flip)
- **AND** every voxel SHALL keep its physical position (no anatomical mirror)

#### Scenario: negative-dominant IOP normal emits right-handed
- **WHEN** a series whose IOP normal's dominant component is negative is converted
- **THEN** the emitted determinant SHALL be positive
- **AND** every voxel SHALL keep its physical position

#### Scenario: emitted file round-trips to the grid it was read as
- **WHEN** `read_dicom_series` reads a series and the emitted `volume.nii.gz` is read back
- **THEN** both SHALL report the same grid (shape and affine within tolerance)

#### Scenario: physical placement preserved across the epoch
- **WHEN** the same series is converted by the previous (in-plane-swapped) and the new path
- **THEN** the two emitted volumes SHALL be physically equivalent — identical physical bounding
  box, each reproducing the other under exact index rearrangement (zero voxel drift)
- **AND** the two grids SHALL relate as `REARRANGED` (in-plane permutation, plus a slice flip
  where the sense anchor changed), never `FOREIGN`

---

### Requirement: No mirror introduced at ITK load

A segmentation created fresh against an emitted right-handed volume in an ITK-canonicalizing
runtime SHALL export on a grid `SAME` as the emitted volume file — the load step introduces no
re-grid (probe-verified: `det > 0` volumes load byte-faithful in Slicer 5.10).

#### Scenario: fresh segmentation exports faithful without conform
- **WHEN** a converter-emitted volume is loaded into Slicer, a segmentation is created against it
  and exported **without** `conform_to`
- **THEN** `grid_relation(exported_segmentation, emitted_volume)` SHALL be `SAME`

---

### Requirement: Deterministic, idempotent conversion

Re-converting the same series SHALL produce an identical grid, so a later re-conversion (repair,
path migration, manual re-run) SHALL NOT drift the grid. The canonical sense SHALL depend only on
per-series constants (IOP, IPP endpoints), never on reader iteration order or framework version
beyond this epoch.

#### Scenario: re-conversion is stable
- **WHEN** a series is converted twice
- **THEN** the two emitted grids SHALL be identical (same shape and affine within tolerance)

---

### Requirement: Misorientation detector matches the canonical convention

`is_volume_misoriented` SHALL derive its expected origin from the IOP normal `n`: the expected
origin is the IPP endpoint with the smaller projection onto `n` (equal to the feet-end when `n`
points head-ward; the opposite end otherwise). Detection SHALL stay idempotent (a
canonically-converted volume is never flagged) and SHALL keep raising `OrientationUnverifiable`
when ground truth cannot be established.

#### Scenario: canonical volume passes detection
- **WHEN** `is_volume_misoriented` runs on a volume emitted by the canonical converter
- **THEN** it SHALL return False

#### Scenario: negative-dominant normal handled
- **WHEN** the series' IOP normal has a negative dominant component
- **THEN** the expected origin SHALL be the minimal-projection-onto-`n` IPP endpoint, and a
  canonical volume SHALL still pass

