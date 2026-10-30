# Design

## Context

All statements below are framework-level and project-agnostic; no consuming project is named
or assumed. The bug and its remedy live entirely inside `clarinet/services/image/` and
`clarinet/services/slicer/`.

**The causal chain** (each step is a property of the framework + ITK, independent of any project):

1. `ImageOrientationPatient` (IOP) fixes only the **in-plane** row and column directions. The
   through-plane (slice) direction is *not* given by IOP — it comes from how the slices stack
   (`ImagePositionPatient` progression). SimpleITK/GDCM derives the slice-axis sign from its own
   file sort, which on some real series disagreed with the actual IPP order and returned a
   left-handed / SI-flipped direction — the "ITK + IOP" bug. clarinet #247 then #453 fixed this by
   recomputing the slice axis and origin directly from IPP ground truth
   (`ground_truth_slice_geometry`), and #412 canonicalizes that slice axis to a stable sense. So
   the slice geometry is correct and version-stable; the mirror bug below is **orthogonal to it**.
2. Independently, `read_dicom_series` maps SimpleITK's natural `(x, y, z)` axes to the internal
   `(rows=y, cols=x, slices=z)` convention: array `transpose(1,2,0)` + direction column reorder
   `d[:, [1,0,2]]`. That in-plane column swap is an **odd permutation**, so it negates the
   determinant: a physically right-handed acquisition is emitted **left-handed** (`det −1`) — a
   standard axial series lands at LPS direction `[[0,1,0],[1,0,0],[0,0,1]]` (confirmed
   empirically). The left-handedness is a representation artifact of the internal axis order,
   *not* a property of DICOM.
3. `Image._save_nifti` writes that direction faithfully, so `volume.nii.gz` is left-handed on disk.
4. Slicer's loader canonicalizes a left-handed grid at load — negates the slice axis and reverses
   the voxel array — while keeping physical placement exact. The in-scene node grid now differs
   from the on-disk file grid.
5. A segmentation created against that node inherits the in-scene (flipped) reference geometry
   *before any painting*, via `SetReferenceImageGeometryParameterFromVolumeNode`.
6. On export the `.seg.nrrd` is written **mirrored** relative to `volume.nii.gz` — physically
   coincident voxels, reversed slice index, opposite-end origin.

**Why the in-scene guard is blind.** `assert_segmentation_matches_volume` compares the
segmentation's in-memory reference geometry against the in-memory volume node. Both were
flipped identically at load, so they agree by construction. It also fails open twice: an
unresolved reference volume (`find_loaded_volume(...) → None`) skips the check, and a
segmentation loaded from disk carries no recorded reference geometry so the guard returns
early. **The only assertion that can catch the mirror re-reads the written file from disk.**

### Empirical validation (live Slicer 5.10.0, probed 2026-07-22)

Every load-bearing assumption was probed against a live Slicer web server with synthetic
volumes (in-plane-swap left-handed, pure slice-flip left-handed, right-handed control):

| # | Fact (probed, not inferred) |
|---|---|
| P1 | `slicer.util.loadVolume` canonicalizes **any** `det < 0` volume by flipping the **slice axis only** (geometry-preserving: array reversed + origin recomputed), regardless of which axes caused the left-handedness. `det > 0` volumes load byte-faithful (node IJKToRAS == file affine exactly). |
| P2 | SimpleITK inside Slicer is a **faithful on-disk grid reader**: both `sitk.ReadImage` and metadata-only `sitk.ImageFileReader.ReadImageInformation()` preserve `det −1`, origin, and direction (LPS-native). |
| P3 | Slicer 5.10's Python has **no `pynrrd` and no `nibabel`** (numpy 2.0.2, scipy, SimpleITK present). Slicer-side grid code must be numpy + sitk only. |
| P4 | sitk metadata-reads a 4-D multi-layer `.seg.nrrd` as a 3-D **vector image** (dim=3, components=n_layers), reporting the spatial grid directly — no 4-D special-casing needed Slicer-side. |
| P5 | A `det < 0` reference geometry works end-to-end for export: `addVolumeFromArray(ijkToRAS=<det<0>)` keeps the matrix verbatim; `SetReferenceImageGeometryParameterFromVolumeNode` serializes it; native `exportNode` writes the `det < 0` grid into `.seg.nrrd` **preserving overlapping layers** (2 layers stayed 2 layers, segment metadata intact). |
| P6 | Round-trip: a fresh seg on a loaded `det < 0` volume exports slice-mirrored vs the on-disk file; a fresh seg on a `det > 0` volume exports grid-identical. Slicer writes `.seg.nrrd` with `space: left-posterior-superior`. |

P1/P6 confirm the causal chain and the payoff of a right-handed emission; P2–P5 fix the
Slicer-side implementation strategy (D3, D4). Probe details live in the committed
`docs/grid-workflows.md` (Task 7) so future devs inherit the evidence, not just the conclusions.

## Goals / Non-Goals

**Goals:**
- A single, testable grid vocabulary — a `Grid` value object and a `GridRelation` taxonomy —
  shared by **both runtimes** (clarinet process and in-Slicer helper) from one source module.
- A Slicer export path that conforms a segmentation to its reference volume's **on-disk** grid,
  preserves multi-layer structure, and fails closed on every unresolvable/foreign input.
- A converter that emits a canonical **right-handed** grid for every non-degenerate series, so no
  ITK runtime mirrors a fresh segmentation at load.
- Repair primitives that are 4-D-safe and refuse to silently resample foreign grids.
- Committed documentation carrying the design rationale and the probe evidence for future devs.

**Non-Goals:**
- Any project-specific backfill, audit, or migration script. The framework ships the primitives;
  a consuming project owns the walk over its own storage layout and file kinds.
- Re-tuning any overlap/comparison thresholds — separate concern downstream of a clean grid.
- Renaming the three capability directories (naming nit noted in review; not worth churn).
- Backward compatibility as a constraint: this change deliberately removes API that cannot be made
  safe (`reference_volume=`) and tightens defaults (`conform_seg_to_grid`, `read_nrrd`). Sanctioned.

## Decisions

### D1: `Grid` value object + `GridRelation` enum in a pure, bundleable core

- **Choice:** new `clarinet/services/image/grid.py` — **numpy + stdlib only, zero clarinet
  imports** (same constraint as `correspondence/*`) — holding:
  - `Grid`: frozen dataclass `(shape: tuple[int,int,int], affine: np.ndarray)`, LPS by
    construction; derived `spacing`/`direction`/`origin` properties; `summary()` (absorbs
    `Image._grid_summary` formatting).
  - `GridRelation`: `SAME | REARRANGED | FOREIGN` plus, on `REARRANGED`, the detail
    `(perm, flips)` from which "mirrored" / "in-plane transpose" are derivable for messages.
  - `grid_relation(a: Grid, b: Grid, *, atol) -> GridRelation` (D2 predicate).
  File-level IO lives in `clarinet/services/image/grid_io.py` (clarinet-side only):
  `read_grid(path) -> Grid` dispatching NIfTI/3-D NRRD via `Image` metadata reads and 4-D NRRD via
  `LayeredSegmentation`, and `assert_same_grid_on_disk(path_a, path_b, *, atol=1e-4)` raising
  `GeometryMismatchError` with both summaries. `Image.grid` / `LayeredSegmentation.grid`
  properties expose the VO; `Image.same_grid`/`assert_same_grid` delegate to it (signatures kept).
- **Rationale:** the affine-composition formula currently exists in ~5 places (`affine_4x4`,
  `_save_nifti`, `_save_nrrd`, `_build_header`, and the planned 4-D branch); `_grid_summary` is
  trapped as a private `Image` method. A pure VO makes every primitive property-testable without
  I/O and is the only shape that can be **bundled into Slicer** (P3: no pynrrd/nibabel there).
  The core deliberately contains **no raise-policy**: asserts live per-runtime
  (`GeometryMismatchError` clarinet-side, `SlicerHelperError` Slicer-side), so the pure module
  never needs clarinet exceptions.
- **Alternatives considered:** free functions over `(shape, affine)` tuples (original design) —
  rejected: anonymous domain concept, formula duplication, not bundleable as a unit, no home for
  summaries/tolerances. Extending `Image.same_grid` to a tri-state — rejected: in-memory only.

### D2: Signed-permutation predicate defines the relation taxonomy

- **Choice:** with `M = affine_a⁻¹ @ affine_b`: **REARRANGED** iff `M[:3,:3]` is a signed
  permutation matrix (entries ∈ {−1, 0, 1} within `atol`, exactly one nonzero per row and column)
  and for every axis mapping `j → i` with sign `s`: `shape_b[j] == shape_a[i]` and the offset
  `t[i]` equals `0` (s=+1) or `shape_a[i] − 1` (s=−1) within 0.5 voxel. **SAME** iff the identity
  case. Everything else is **FOREIGN**.
- **Rationale:** REARRANGED ⇔ every voxel center of one grid lands exactly on a voxel center of
  the other ⇔ losslessly repairable by pure index rearrangement (`reindex_to(order=0)` is exact).
  That is the boundary consumers actually need: conform-vs-refuse. The original three-string
  vocabulary (`"same"/"mirrored"/"other"`) could not express the in-plane transpose that **this
  change itself mass-produces** (post-epoch, every legacy seg vs its re-converted volume), and its
  rationale ("other must fail") contradicted its own migration plan ("conform legacy pairs"). The
  bespoke mirror predicate (`z₀+(n−1)·spacing` along one axis) was axis-aligned world-coordinate
  thinking; the signed-permutation form handles oblique grids automatically and gives the
  0.5-voxel tolerance a principled meaning (nearer the correct center than any neighbour). The
  probe classifier implemented this predicate and correctly labelled every pair
  (mirror → `REARRANGED(perm=id, flips=[axis2:-])`, conformed → `SAME`).
- **Alternatives considered:** keep `"mirrored"` as a special case — rejected: it is derivable
  detail of REARRANGED, not a distinct category; special-casing it re-opens the vocabulary gap.

### D3: One engine, both runtimes — grid core rides the correspondence bundle

- **Choice:** append `grid` to `_MODULES` in `correspondence_bundle.py` (`service.py:229` injects
  the bundle between helper and runner). Helper-side symbols resolve via `globals()` with the same
  session-sticky guard pattern as `subtract_segmentations`. The Slicer-side IO adapter is
  `_read_grid_on_disk(path)` in `helper.py`: `sitk.ImageFileReader.ReadImageInformation()` →
  `Grid` (P2 faithful; P4 gives the spatial grid of 4-D `.seg.nrrd` directly).
- **Rationale:** helper.py cannot import clarinet (it ships as text); duplicating the relation
  predicate in two runtimes is how verdicts drift — the exact failure class PR #494 eliminated
  for set-ops. P2/P3/P4 make sitk the only viable and a fully sufficient adapter; no header
  parsing needed.
- **Alternatives considered:** hand-rolled minimal check inside helper.py — rejected: second
  implementation of subtle math. A separate second bundle — rejected: one injection point exists
  and the paths that export (record open/validate/submit-validator) already always include it.

### D4: Conform-on-export — classify, exact re-grid on a temp node, native export, disk assert

- **Choice:** `export_segmentation(name, output_path, *, conform_to: str | None = None)`.
  When `conform_to` is set (probe-validated mechanics, P2/P5):
  1. Read the reference's **on-disk** grid via `_read_grid_on_disk` (never via `loadVolume` —
     P1 shows the loaded node carries the canonicalized grid, which is the bug itself).
  2. Classify the node's current binary-labelmap grid (converted to LPS) against it:
     `SAME` → export directly; `REARRANGED` → build a hidden reference node
     (`addVolumeFromArray(ijkToRAS=...)`, det<0 kept verbatim per P5), re-grid **exactly**
     (index rearrangement, no interpolation) onto a temporary segmentation node preserving
     segment names/colors/label values and layer structure, native `exportNode`; `FOREIGN` →
     raise `SlicerHelperError`, write nothing.
  3. Post-write: re-read `output_path` from disk, assert `SAME` vs the reference grid; on
     mismatch delete the written file and raise (fail-closed leaves no bad artifact).
  4. Temp nodes are removed in `finally`; the caller's segmentation node is **never mutated**.
  Without the bundle, `conform_to` raises `SlicerHelperError` (same opt-in contract as
  `detect_overlaps`/`subtract_segmentations`).
- **Rationale:** this contains the mirror for every future segmentation whatever the writer,
  preserves multi-layer semantics (P5 — the earlier "labelmap re-export" sketch would have
  silently flattened overlapping segments), and the classification step is what makes
  "cannot be conformed" principled: a partially-overlapping foreign grid would otherwise re-grid
  to a non-empty labelmap and be silently resampled.

### D5: Single-guard export API — remove `reference_volume=`

- **Choice:** delete the `reference_volume=` parameter from `export_segmentation` and demote
  `assert_segmentation_matches_volume` to a private helper (`_assert_segmentation_matches_volume`)
  used only by `load_segmentation`'s best-effort load-time check. `conform_to` is the only export
  guard. Breaking; sanctioned.
- **Rationale:** the fail-open is **structural** to the parameter shape: callers pass
  `reference_volume=find_loaded_volume(path)`, and `None` means both "don't guard" and "my
  resolution failed" — no implementation can fail closed on that channel without breaking
  `find_loaded_volume`'s query contract. `conform_to` heals it by moving resolution inside the
  enforcement point. Keeping the blind parameter alongside its replacement would ship permanent
  API debt plus documented interaction rules ("supersedes in practice"). Enforcement lives at the
  **write** boundary; the load-time check stays best-effort by design.

### D6: Canonical slice sense = the IOP-normal side (universal `det > 0`)

- **Choice:** two coordinated emission changes in `dicom_volume.py`:
  1. **In-plane (unchanged from prior design):** drop the `d[:, [1,0,2]]` swap **end-to-end** —
     array layout and direction move together; `read_dicom_series` and a re-read of the emitted
     file agree. In-plane basis = DICOM IOP order.
  2. **Slice sense (new):** `_canonicalize_slice_axis` flips (geometry-preserving, as today) when
     `dot(slice_dir, cross(d[:,0], d[:,1])) < 0` — i.e. the canonical sense is the **side of the
     IOP normal**, not the +dominant axis. Since `det([r, c, s]) = (r×c)·s = n·s`, the emitted
     determinant is **positive for every series with a non-degenerate IOP**. Degenerate case
     (`|n·s| ≈ 0` or unavailable in-plane columns): fall back to the current +dominant rule and
     log a warning.
  The hard invariant, restated correctly: **every emitted-grid transform must be a
  geometry-preserving index rearrangement** (array + origin + direction together; positions
  invariant). Forcing `det > 0` via a *direction-only* flip remains forbidden — that is the
  #247/#453 mirror. Forcing it via a geometry-preserving flip is not a mirror; it is a grid
  representation choice, and #412 already established that index order need not preserve
  acquisition order.
- **Rationale:** the prior design believed a class of "genuinely-left-handed acquisitions" must
  stay left-handed and be caught by D4-conform. That model was wrong twice: (a) the existing
  canonicalization already flips stack-opposing-normal series (so the old Requirement 2 scenario
  could never pass), and (b) the class that *would* remain left-handed under the old rule
  (negative-dominant IOP normal) is left-handed only by an arbitrary anchor choice. Re-anchoring
  to the IOP-normal side — inside an epoch that is already breaking for every series — eliminates
  the left-handed population entirely, so ITK never canonicalizes at load (P1) and a fresh
  segmentation is faithful with **no** conform step (P6). D4-conform demotes to
  defense-in-depth for legacy/foreign files.
- **Consequence — detector update:** `is_volume_misoriented` currently expects origin = feet-end
  IPP; under D6 the expected origin is the IPP endpoint with the **smaller projection onto the
  IOP normal** `n` (equal to feet-end when `n` points head-ward, opposite otherwise). Derive the
  expectation from `n`; keep idempotence.
- **Alternatives considered:** keep +dominant and contain the residual class via conform
  (prior design) — rejected: leaves a trap-documented left-handed population and a converter whose
  "canonical" name promises more than it does. Emit `det > 0` by in-plane mirroring — rejected:
  in-plane basis must stay IOP-faithful.

### D7: Guarded repair — relation gate + 4-D support in `conform_seg_to_grid`

- **Choice:** `conform_seg_to_grid(seg_path, grid_path, *, out_path=None, atol=1e-4,
  allow_resample=False)`:
  - `SAME` → no-op (returns False); `REARRANGED` → exact per-index repair (3-D: `reindex_to(order=0)`;
    4-D: per-layer reindex via `LayeredSegmentation`, reassembled with segment names/labels/layers
    preserved); `FOREIGN` → raise `GeometryMismatchError` unless `allow_resample=True` explicitly
    opts into true nearest-neighbour resampling.
  - `Image.read_nrrd` raises `ImageReadError` on `ndim != 3` (today it silently builds a NaN grid
    from the list-axis row), pointing to `LayeredSegmentation`/`read_grid`.
  - NRRD `space` field is honored on read: LPS as-is, RAS/LAS flipped into LPS, anything else
    raises `ImageReadError` (today the field is ignored — a RAS-native NRRD is silently misread;
    P6 confirms Slicer itself writes LPS, so this only affects third-party files).
- **Rationale:** the previous design claimed `conform_seg_to_grid` was "already sufficient" —
  false for the primary artifact class (multi-layer `.seg.nrrd`), and it silently resamples
  foreign masks, contradicting the change's own philosophy. Safe defaults win over compat
  (established project stance). With these fixes, traps T3/T4/T5/T6/T7/T8 of the original plan
  stop being documentation and become impossible states.

## Risks / Trade-offs

- [Grid epoch break] Release B changes every newly-converted volume's grid (in-plane transpose
  always; slice sense where the old and new anchors disagree) → **Mitigation:** Release A ships
  first with detection (`grid_relation`) + repair (`conform_seg_to_grid`, idempotent, exact for
  REARRANGED) + conform-on-export; CHANGELOG *Breaking* entry + migration-doc section; re-convert
  then conform-once remediation.
- [Helper API removal] Scripts/validators calling `export_segmentation(reference_volume=)` break
  loudly (TypeError) at Release A → **Mitigation:** CHANGELOG *Breaking* entry names the
  one-line migration (`conform_to=<volume path>`); framework-internal callers updated in-change.
- [Degenerate IOP] Series with unavailable/degenerate in-plane columns can't derive `n` →
  **Mitigation:** deterministic fallback to the +dominant rule + warning; conversion never fails
  on this, and such volumes remain covered by conform-on-export.
- [Bundle-required conform] `conform_to` on a script sent without the bundle raises →
  **Mitigation:** same session-sticky contract as the set-ops; record open/validate/submit paths
  always bundle; ad-hoc `/exec` opts in via `include_correspondence`.
- [sitk fidelity assumption] D3/D4 rest on P2/P4 → **Mitigation:** probed on 5.10.0; the live
  integration test re-asserts it every run (a canonicalizing sitk would fail the `SAME` assert).
- [Raw-index/spacing consumers] Dropping the in-plane swap changes DICOM-sourced array order
  (`(y,x,z)` → `(x,y,z)`) **and** spacing order (`(sy,sx,sz)` → `(sx,sy,sz)`); slice axis stays
  axis 2 → **Mitigation:** apply-phase audit of raw-index *and* spacing-order consumers;
  geometry-preservation test pins physical bbox delta 0.00 mm and `before.reindex_to(after)`
  data equality.
- [Temp-node hygiene] Conform-on-export creates hidden nodes → **Mitigation:** removal in
  `finally`; the caller's node is never mutated (spec scenario).

## Migration Plan

Two releases, dependency-ordered (repair must exist before the epoch that requires it):

1. **Release A:** D1/D2/D3 (grid core + IO + bundle), D4 (conform-on-export), D5 (single-guard
   API, breaking), D7 (guarded repair, breaking defaults). Consumers bump the pin, gain
   detection/repair and a contained Slicer export; call sites of `reference_volume=` migrate to
   `conform_to=`. No data changes required.
2. **Release B:** D6 (canonical converter epoch, breaking). CHANGELOG *Breaking* + migration-doc
   section (detect via `grid_relation`/`is_volume_misoriented`, repair via idempotent
   `conform_seg_to_grid`). After a consumer re-converts, legacy segmentations are conformed once.

Rollback: Release A — revert the pin (removed API returns). Release B — pin back to Release A;
re-converted volumes stay valid (any REARRANGED-related grid is physically equivalent; conformed
segs remain correct).

## Resolved Questions

- **Q1 (was: is right-handed emission the intended reconciliation with #412?):** Resolved as
  **yes, via D6**. #412's actual invariant is *version-stability + geometry-preservation of the
  slice axis*, not the +dominant anchor itself; re-anchoring to the IOP-normal side inside an
  already-breaking epoch completes the canonicalization (`det > 0` universal) without mirroring
  anything. Confirmed empirically: right-handed volumes round-trip Slicer with zero grid drift (P6).
- **Q2 (was: which raw-index consumers?):** Narrowed to an executable audit (Task 6): raw-index
  consumers of DICOM-sourced arrays **and** spacing-order consumers (`(sy,sx)` → `(sx,sy)`), with
  the geometry-preservation + round-trip tests as the invariants.
