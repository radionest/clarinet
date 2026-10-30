## Why

The DICOM→NIfTI converter emits `volume.nii.gz` with an in-plane-swapped, **left-handed**
direction matrix (`det = −1`). Slicer canonicalizes a left-handed grid at load — negating the
slice axis and reversing the voxel array (probe-confirmed on 5.10) — so any segmentation created
fresh against that volume node exports **mirrored** relative to the on-disk volume file. The
mirror is physically faithful but index-wise consumers see a foreign grid, and the existing
in-scene guard cannot detect it: it compares two in-memory objects that were both flipped the
same way at load. The only assertion with teeth reads the written file back from disk. The
framework offers no primitive to classify grid relations, assert grid identity on disk, or
conform a segmentation to its reference grid at export — so every consuming project reimplements
this privately and inconsistently.

This revision (2026-07-22) folds in an architecture review plus live-Slicer probes: primary aim
is a clean, testable framework API; refactor volume and backward compatibility are secondary.

## What Changes

**Grid vocabulary** (image service; additive plus two safety-tightening breaks)
- From: anonymous `(shape, affine)` handling smeared over ~5 modules; boolean in-memory
  `same_grid` only; `Image.read_nrrd` silently builds a NaN grid from a 4-D `.seg.nrrd`; NRRD
  `space` field ignored (RAS files silently misread).
- To: pure bundleable core `grid.py` — `Grid` value object + `GridRelation` enum
  (`SAME | REARRANGED | FOREIGN`, signed-permutation predicate, rearrangement detail) — plus
  `grid_io.py` with `read_grid(path) -> Grid` (4-D-safe, honors NRRD `space`) and
  `assert_same_grid_on_disk` (raises `GeometryMismatchError`). `Image.read_nrrd` raises on
  non-3-D input; `Image`/`LayeredSegmentation` gain `.grid`.
- Reason: one property-testable vocabulary, shared verbatim with the Slicer runtime (which has no
  pynrrd/nibabel — probed), replacing per-project reimplementations.
- Impact: additive API; **breaking**: 4-D via `Image.read` and exotic NRRD `space` now raise
  instead of silently corrupting.

**Guarded repair** (`conform_seg_to_grid`; breaking default)
- From: unguarded resampler (silently resamples masks from a different study); broken on 4-D
  multi-layer `.seg.nrrd` (NaN grid).
- To: relation-gated — `SAME` no-op, `REARRANGED` exact index rearrangement (3-D and 4-D
  layer-preserving), `FOREIGN` raises unless `allow_resample=True`.
- Reason: safe defaults over compat (established project stance); repair must work on the
  primary artifact class (multi-layer Slicer exports).
- Impact: **breaking** — foreign-grid pairs now raise by default.

**Slicer conform-on-export** (helper; new kwarg + breaking API removal)
- From: `export_segmentation(name, out, *, reference_volume=None)` — an in-memory guard that is
  blind to the mirror and structurally fail-open (`None` conflates "don't guard" with
  "resolution failed").
- To: `export_segmentation(..., conform_to=None)` — reads the reference's **on-disk** grid via
  SimpleITK (probe-verified faithful), classifies, exactly re-grids `REARRANGED` nodes
  (layer/metadata-preserving, caller's node untouched), refuses `FOREIGN`, asserts the written
  file on disk, deletes it on assert failure. `reference_volume=` is **removed**;
  `assert_segmentation_matches_volume` becomes private (load-time best-effort only). Grid core
  rides the existing correspondence bundle (one engine, both runtimes).
- Reason: contain the mirror at the write boundary for every future segmentation, fail-closed by
  construction.
- Impact: **breaking** helper API (one-line migration: `reference_volume=node` →
  `conform_to=<volume path>`).

**Canonical DICOM→NIfTI emission** (converter; breaking root fix, own release)
- From: in-plane axis swap makes every emission left-handed; slice sense anchored to the
  +dominant axis, leaving a left-handed residual class.
- To: in-plane basis in DICOM IOP order (swap removed **end-to-end**) + canonical slice sense on
  the **IOP-normal side**, so `det = n·s > 0` for every non-degenerate series — no ITK runtime
  re-grids at load, a naive writer needs no `conform_to`. All transforms geometry-preserving
  (array + origin + direction together); direction-only flips remain forbidden (#247/#453).
  `is_volume_misoriented` derives its expected origin from the IOP normal. Degenerate IOP falls
  back to +dominant with a warning.
- Reason: removes the trigger at the source and completes #412's canonicalization (its true
  invariant is geometry-preservation + version-stability, not the +dominant anchor).
- Impact: **breaking** grid epoch — every newly-converted volume changes on-disk layout
  (in-plane transpose; slice sense where anchors disagree). Physically equivalent
  (`REARRANGED`, zero voxel drift); legacy segmentations conform once against re-converted
  volumes.

## Capabilities

### New Capabilities
- `segmentation-grid-inspection`: `Grid` value object + `GridRelation` classification
  (signed-permutation predicate) in a bundleable core; 4-D-safe `read_grid`; disk-level
  grid-identity assertion; guarded 4-D-capable repair; loud 3-D-reader failures.
- `slicer-conformed-export`: export that conforms to the reference volume's on-disk grid,
  layer-preserving, fail-closed on every unresolvable/foreign input; single-guard API.
- `canonical-volume-conversion`: DICOM→NIfTI emits a canonical right-handed
  (`det > 0`) grid for every non-degenerate series via geometry-preserving representation
  choices; detector updated to the convention.

### Modified Capabilities
- (none — no existing main spec covers these surfaces; verified against `openspec/specs/`)

## Impact

- Code: `clarinet/services/image/` (`grid.py` NEW pure core, `grid_io.py` NEW, `image.py`,
  `segmentation.py`, `dicom_volume.py`, `orientation.py`, `layered_segmentation.py`),
  `clarinet/services/slicer/` (`helper.py`, `correspondence_bundle.py`). Reuses
  `GeometryMismatchError` (`clarinet/exceptions/domain.py`).
- Breaking (Release A): `export_segmentation(reference_volume=)` removed;
  `assert_segmentation_matches_volume` privatized; `conform_seg_to_grid` raises on FOREIGN by
  default; `Image.read_nrrd` raises on non-3-D and on unsupported NRRD `space`.
- Breaking (Release B): converter grid epoch (see above).
- Docs (committed, in the feature PRs — the rationale must outlive the openspec artifacts):
  new `docs/grid-workflows.md` carrying the grid lifecycle, the dev-API decision table, the
  **design rationale and the 2026-07-22 live-Slicer probe results** (P1–P6), and the reduced
  traps catalogue; CHANGELOG *Breaking* entries per release; a conversion-orientation section in
  `clarinet/docs/migration-orientation-0.10.17.md`; `.claude/rules/slicer-helper-api.md` updated.
- Consumers: bump the pin per release; after Release B, re-convert volumes and conform legacy
  segmentations once (`conform_seg_to_grid`, idempotent, exact for REARRANGED pairs).
- Release: two tags — Release A (vocabulary + repair + export, incl. helper API break),
  Release B (converter epoch), so the repair primitives exist before the epoch that needs them.
