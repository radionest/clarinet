# slicer-conformed-export Specification

## Purpose
TBD - created by archiving change canonicalize-segmentation-grids. Update Purpose after archive.
## Requirements
### Requirement: Conform-on-export to the reference's on-disk grid

`export_segmentation(name, output_path, *, conform_to=None)` SHALL, when `conform_to` is given,
guarantee the written file shares the reference volume's **on-disk** grid:

1. The reference grid SHALL be read from the file itself (SimpleITK metadata read — probe-verified
   faithful), never from a scene node loaded by Slicer's canonicalizing loader.
2. The node's grid SHALL be classified against it: `SAME` → export directly; `REARRANGED` →
   re-grid by **exact index rearrangement** (no interpolation) and export; `FOREIGN` → raise,
   write nothing.
3. After writing, the output SHALL be re-read from disk and asserted `SAME` against the reference
   grid; on mismatch the written file SHALL be deleted and the export SHALL raise.

The conform path SHALL preserve multi-layer structure and per-segment metadata (names, colors,
label values; overlapping segments stay on distinct layers), and SHALL NOT mutate the caller's
segmentation node (temporary nodes are removed even on failure).

#### Scenario: mirror repaired at export
- **WHEN** a segmentation authored against an ITK-canonicalized volume node is exported with
  `conform_to` set to its source volume file
- **THEN** the written file SHALL be grid-identical to the on-disk volume (including a
  negative-determinant grid, written verbatim)
- **AND** every voxel SHALL keep its physical position relative to the unconformed export

#### Scenario: overlapping layers preserved through conform
- **WHEN** a two-layer (overlapping-segment) segmentation is exported with `conform_to`
- **THEN** the written `.seg.nrrd` SHALL keep two layers and both segments' metadata

#### Scenario: foreign grid raises instead of silently resampling
- **WHEN** the node's voxels sit on a grid FOREIGN to the reference (e.g. sub-voxel shift or
  different study), even if a re-grid would produce a non-empty labelmap
- **THEN** the export SHALL raise and SHALL NOT write a file

#### Scenario: caller's node untouched
- **WHEN** any conform-on-export completes or fails
- **THEN** the caller's segmentation node's grid, segments, and representations SHALL be unchanged
- **AND** no temporary node SHALL remain in the scene

#### Scenario: unguarded export unchanged
- **WHEN** `export_segmentation` is called without `conform_to`
- **THEN** it SHALL export the node as-is (today's behavior)

---

### Requirement: Guarded export fails closed

Every failure to establish the guard SHALL raise before or instead of leaving an unguarded file:
`conform_to` names a missing or unreadable file → raise; the grid bundle is absent from the Slicer
session → raise (same opt-in contract as the other engine-backed helpers); the post-write assert
fails → delete the file and raise. There SHALL be no code path where a requested guard silently
degrades to no guard.

#### Scenario: missing reference file
- **WHEN** `conform_to` points at a nonexistent or unreadable path
- **THEN** the export SHALL raise an error identifying the path and SHALL NOT write a file

#### Scenario: bundle absent
- **WHEN** `conform_to` is used in a Slicer session where the grid/correspondence bundle was
  never injected
- **THEN** the export SHALL raise (never silently export unguarded)

#### Scenario: post-write assert failure leaves no artifact
- **WHEN** the written file's grid does not match the reference on re-read
- **THEN** the file SHALL be deleted and the export SHALL raise

---

### Requirement: Single-guard export API with a deprecated legacy channel

`conform_to` SHALL be the guard parameter of record for `export_segmentation`: the only channel
that re-grids a REARRANGED node (exact index rearrangement before write) and the only one with the
fail-closed post-write guard. The former `reference_volume=` parameter SHALL remain as a
deprecated compatibility shim for downstream validators (removal tracked for the next release):
any use — `None` included — SHALL emit a deprecation warning that reaches the Slicer console and,
when `conform_to` is not set, SHALL run the in-scene `assert_segmentation_matches_volume` check
before anything is written. When both are passed, `conform_to` SHALL supersede the in-scene check
(it can repair a REARRANGED node the in-scene check would reject). `assert_segmentation_matches_volume`
SHALL remain on the public helper surface so downstream validator scripts can assert grids directly.

#### Scenario: deprecated parameter warns and still guards
- **WHEN** a script calls `export_segmentation(..., reference_volume=node)` without `conform_to`
- **THEN** a deprecation warning SHALL be emitted AND the in-scene grid assertion SHALL run before
  the export (mismatch raises before anything is written)

#### Scenario: conform_to supersedes the deprecated check
- **WHEN** `conform_to` and `reference_volume` are both passed
- **THEN** the on-disk guard SHALL be enforced and the in-scene check SHALL NOT run (it could
  reject a REARRANGED node that conform_to re-grids)

#### Scenario: load-time check remains best-effort
- **WHEN** `load_segmentation` loads a file while a source volume is set
- **THEN** the geometry pre-check SHALL still reject a grossly foreign segmentation
- **AND** the write-side guard (`conform_to`) SHALL remain the enforcement point of record

