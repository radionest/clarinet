# Canonicalize Segmentation Grids — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan
> task-by-task in a worktree. Each task is TDD (write the failing test first). Commit after each
> task. `openspec/` content never enters a PR; the committed docs of Task 10 do.

**Goal:** One grid vocabulary (`Grid` + `GridRelation`, signed-permutation predicate) shared by
both runtimes; fail-closed conform-on-export in Slicer; guarded 4-D-safe repair; then a canonical
right-handed converter (`det > 0` universal) so no ITK runtime re-grids a fresh segmentation.

**Architecture (design.md D1–D7):** pure bundleable `clarinet/services/image/grid.py` (numpy +
stdlib ONLY) + IO in `grid_io.py`; grid module rides the correspondence bundle
(`correspondence_bundle.py:14` `_MODULES`, injected at `service.py:229`); helper reads on-disk
grids via SimpleITK metadata (probe P2/P4); converter epoch = in-plane IOP order + slice sense on
the IOP-normal side. Probe evidence P1–P6: design.md "Empirical validation".

**Tech stack:** Python 3.12, numpy, nibabel, pynrrd; `uv run pytest`; live 3D Slicer for
`-m slicer`. **Selector baselines (validated 2026-07-22):**
`tests/test_image.py tests/test_orientation.py tests/test_slicer_helper_unit.py --collect-only -q`
→ **149**; `tests/test_image.py -k "grid_relation or read_grid or GridRelation"` → **0** (TDD
red); `tests/test_image.py -k conform` → **4** (existing `conform_seg_to_grid` tests);
`tests/test_grid.py` does not exist yet.

**Releases:** Release A after Task 6, Release B after Task 9 (`.claude/rules/release.md` flow).

---

## Task 1: `grid.py` — pure core (`Grid`, `GridRelation`, signed-permutation predicate)

- [ ] **Step 1 (red):** NEW `tests/test_grid.py`:
      - `TestGrid`: `from_components(shape, spacing, origin, direction)` ⇄ `affine` round-trip;
        `affine` equals `Image.affine_4x4` (`image.py:255`) for the same components; `summary()`
        prints shape/origin/spacing/full direction (port the `_grid_summary` format,
        `image.py:273`).
      - `TestGridRelation` (seeded `np.random.default_rng`, parametrized — no hypothesis dep):
        identity → SAME; each of a set of random signed permutations (incl. slice mirror,
        in-plane transpose, combined, on axis-aligned AND oblique direction matrices) applied to
        a random grid → REARRANGED with exact `perm`/`flips` detail; non-integral offset /
        small rotation / shape change → FOREIGN; 0.5-voxel offset tolerance boundary.
      Run `uv run pytest tests/test_grid.py -q` → fails (module missing).
- [ ] **Step 2 (green):** create `clarinet/services/image/grid.py` — **imports: numpy, stdlib
      only; zero `from clarinet ...`** (bundle constraint, design D1):
      ```python
      class RelationKind(enum.Enum): SAME | REARRANGED | FOREIGN
      @dataclass(frozen=True)
      class GridRelation:
          kind: RelationKind
          perm: tuple[int, int, int] | None = None   # REARRANGED only: source axis per output axis
          flips: tuple[bool, bool, bool] | None = None
      @dataclass(frozen=True)
      class Grid:
          shape: tuple[int, int, int]
          affine: np.ndarray                          # 4x4, LPS
          # from_components(...) classmethod; spacing/direction/origin properties; summary()
      def grid_relation(a: Grid, b: Grid, *, atol: float = 1e-4) -> GridRelation
      ```
      Predicate (design D2): `M = inv(a.affine) @ b.affine`; linear part signed-permutation
      within `atol`; per axis `j→i` sign `s`: `b.shape[j] == a.shape[i]` and offset `t[i]` ==
      `0` (s=+1) / `a.shape[i]−1` (s=−1) within 0.5 voxel; identity → SAME; else FOREIGN.
      (Reference implementation validated against live-Slicer files: scratchpad
      `analyze_roundtrip.py` of the 2026-07-22 probes.) Selector → green.
- [ ] **Commit:** `feat(image): Grid value object + signed-permutation grid_relation`

## Task 2: `grid_io.py` — `read_grid` + `assert_same_grid_on_disk`

- [ ] **Step 1 (red):** `tests/test_grid.py::TestReadGrid`: 3-D `.nii.gz`/`.nrrd` →
      `Grid` == `Image.read(load_data=False).grid`; 4-D `(L,X,Y,Z)` `.seg.nrrd` → spatial grid
      == `LayeredSegmentation.read_header` grid (NOT the list-axis row); 4-D NIfTI → spatial
      3-shape; RAS-native NIfTI vs LPS-native NRRD of one physical volume → equal LPS affines.
      `TestAssertSameGridOnDisk`: matching pass; mirrored pair raises `GeometryMismatchError`
      containing both `summary()` strings; the in-memory-guard contrast test (both objects
      consistently flipped in memory agree, disk assert raises).
- [ ] **Step 2 (green):** create `clarinet/services/image/grid_io.py`:
      `read_grid(path) -> Grid` — sniff NRRD dimensionality via `nrrd.read_header` `sizes`;
      4-D → `LayeredSegmentation.read_header` (`layered_segmentation.py:183`); else
      `Image.read(path, load_data=False)`; NIfTI 4-D → first 3 axes.
      `assert_same_grid_on_disk(path_a, path_b, *, atol=1e-4)` — `grid_relation` != SAME →
      `GeometryMismatchError` (`exceptions/domain.py:552`) with both summaries.
- [ ] **Step 3:** export `Grid`, `GridRelation`, `RelationKind`, `grid_relation`, `read_grid`,
      `assert_same_grid_on_disk` from `clarinet/services/image/__init__.py`.
- [ ] **Commit:** `feat(image): read_grid + assert_same_grid_on_disk (disk-level, 4-D-safe)`

## Task 3: Reader hardening — NRRD `space`, loud 4-D, `.grid` properties

- [ ] **Step 1 (red):** in `tests/test_image.py`: RAS-native NRRD (`space:
      right-anterior-superior`) reads to the correct LPS grid; unsupported `space` raises
      `ImageReadError`; `Image.read` on a 4-D `.seg.nrrd` raises `ImageReadError` (no NaN grid);
      `Image.grid`/`LayeredSegmentation.grid` return `Grid`; `same_grid`/`assert_same_grid`
      behavior unchanged (existing tests stay green).
- [ ] **Step 2 (green):** `image.py`: shared `_nrrd_space_to_lps(space, dirs, origin)` helper
      (LPS as-is; RAS/LAS flip the affected world components; else raise) used by `read_nrrd`
      (`image.py:431-447`) and `LayeredSegmentation._apply_grid_from_header`
      (`layered_segmentation.py:196`); `read_nrrd` raises on `len(sizes) != 3`
      (message names `LayeredSegmentation`/`read_grid`); add `.grid` properties; `same_grid`/
      `assert_same_grid` (`image.py:262/286`) delegate to `Grid`; `_grid_summary` → thin wrapper
      over `Grid.summary()`.
- [ ] **Commit:** `feat(image)!: honor NRRD space, loud 4-D read_nrrd, Grid properties`

## Task 4: `conform_seg_to_grid` — relation gate + 4-D repair

- [ ] **Step 1 (red):** `tests/test_image.py` (`-k conform`, baseline 4): FOREIGN pair raises
      `GeometryMismatchError` by default; `allow_resample=True` resamples it; REARRANGED (mirror
      AND in-plane transpose) repaired **exactly** (voxel-for-voxel vs a hand-computed index
      rearrangement); 4-D two-layer overlapping `.seg.nrrd` mirrored pair → repaired file keeps
      2 layers + `Segment{i}_Name`/`LabelValue`; SAME → no-op False. Audit the 4 existing tests:
      any that relied on silent foreign resample get `allow_resample=True`.
- [ ] **Step 2 (green):** `segmentation.py:665`: new signature
      `conform_seg_to_grid(seg_path, grid_path, *, out_path=None, atol=1e-4,
      allow_resample=False) -> bool`; classify via `read_grid` + `grid_relation`; 3-D REARRANGED →
      `reindex_to(reference, order=0)` (exact); 4-D → read layers via `LayeredSegmentation`,
      per-layer reindex, reassemble `from_layers` (`layered_segmentation.py:85`) carrying
      segment names/labels/layers, save; FOREIGN → raise unless `allow_resample`.
- [ ] **Commit:** `feat(image)!: conform_seg_to_grid relation gate + 4-D layer-preserving repair`

## Task 5: Bundle + helper `conform_to` + single-guard API

- [ ] **Step 1 (red):** `tests/test_slicer_helper_unit.py` (`_Dummy` path): `conform_to` with no
      bundle in namespace raises `SlicerHelperError`; `conform_to` pointing at a missing file
      raises; plain export (no kwarg) unchanged; `reference_volume=` → TypeError. Bundle test
      (wherever `build_correspondence_bundle` is covered): flattened bundle including `grid`
      still `exec`s in a bare namespace and `grid_relation` is callable.
- [ ] **Step 2 (green):**
      - `correspondence_bundle.py:14`: add `grid` to `_MODULES` (standalone module; keep `model`
        first for the engine).
      - `helper.py`: `_read_grid_on_disk(path) -> Grid` — `sitk.ImageFileReader` +
        `ReadImageInformation()` (LPS-native, faithful per probe P2; a 4-D `.seg.nrrd` arrives
        as 3-D vector image per P4); bundled `Grid` resolved via `globals()` with the same
        session-sticky guard as `subtract_segmentations`; raise `SlicerHelperError` when absent.
      - `export_segmentation` (`helper.py:225`) → `(name, output_path, *, conform_to: str | None
        = None)`: design D4 mechanics — classify node labelmap grid (RAS→LPS) vs
        `_read_grid_on_disk(conform_to)`; SAME → export directly; REARRANGED → hidden reference
        node `slicer.util.addVolumeFromArray(..., ijkToRAS=<file RAS matrix>)` (det<0 kept, probe
        P5) + temp segmentation on the file grid via exact per-segment index rearrangement
        (`arrayFromSegmentBinaryLabelmap(..., referenceVolumeNode=ref)` is exact for REARRANGED),
        copy names/colors/label values, native `exportNode`; FOREIGN → raise, no write;
        post-write `_read_grid_on_disk(output_path)` must be SAME else delete file + raise; temp
        nodes removed in `finally`; caller's node never mutated.
      - **Remove `reference_volume=`**; `assert_segmentation_matches_volume` (`helper.py:154`) →
        `_assert_segmentation_matches_volume`, called only from `load_segmentation`
        (`helper.py:1696`); fix all framework call sites.
- [ ] **Step 3:** update `.claude/rules/slicer-helper-api.md` (`export_segmentation` line, removed
      guard entries, bundle now carries grid symbols).
- [ ] **Commit:** `feat(slicer)!: conform_to export via bundled grid engine; drop reference_volume`

## Task 6: Live-Slicer round-trip (Release A gate)

- [ ] **Step 1:** `tests/integration/test_slicer_helper.py` (`@pytest.mark.slicer`): write a
      synthetic left-handed volume (`diag(1,1,-1)`-style, marker voxels); load; two overlapping
      segments; export twice — without `conform_to` → `grid_relation(out, volume)` REARRANGED
      with slice flip; with `conform_to` → SAME, det<0 written verbatim, 2 layers + names
      preserved, voxels physically coincident with the unconformed file; a FOREIGN `conform_to`
      (shifted grid volume file) → raises, no file. (Mirrors the 2026-07-22 probe scripts.)
- [ ] **Step 2:** `uv run pytest tests/integration/test_slicer_helper.py -m slicer -k conform`
      green on live Slicer (auto-skip ≠ done).
- [ ] **Commit:** `test(slicer): conform_to repairs the load-time mirror end-to-end`
- [ ] **Release A:** CHANGELOG `### Breaking` (helper API removal; conform gate; read_nrrd
      raises) + tag per `.claude/rules/release.md`; note the version.

## Task 7: Converter — in-plane IOP order, end-to-end

- [ ] **Step 1 (red):** `tests/test_orientation.py` / `tests/test_image.py`: standard axial
      synthetic series → emitted in-plane basis in IOP order and
      `read_dicom_series`-grid == `read_grid(emitted)` (round-trip consistency — catches a
      write-side-only reorient); before/after epoch pair → physically equivalent (bbox delta
      0.00 mm, exact `reindex_to` reproduction), relation REARRANGED not FOREIGN.
- [ ] **Step 2 (green):** `dicom_volume.py:61-77`: drop the `(1,2,0)` transpose +
      `d[:, [1,0,2]]` swap pair; array becomes SimpleITK `(z,y,x)` → `(x,y,z)` via
      `transpose(2,1,0)`; spacing `(sx,sy,sz)`; direction columns as SimpleITK reports.
      `ground_truth_slice_geometry` (`orientation.py:43`) untouched (slice column stays col 2).
- [ ] **Commit:** `feat(image)!: emit DICOM volumes in IOP in-plane order (end-to-end)`

## Task 8: Converter — canonical slice sense on the IOP-normal side + detector

Depends on Task 7 (direction columns must be IOP-ordered so `cross(d[:,0], d[:,1])` is the IOP
normal).

- [ ] **Step 1 (red):** tests: standard axial → `det > 0` + `grid_relation(emitted,
      ground_truth) == SAME`; IPP-opposing-normal synthetic → `det > 0`, positions preserved
      (marker voxels); negative-dominant-normal synthetic (e.g. row=(1,0,0), col=(0,−1,0)) →
      `det > 0`, positions preserved; degenerate normal → +dominant fallback + warning logged;
      re-conversion idempotent. `is_volume_misoriented`: canonical volumes (incl.
      negative-dominant normal) → False; pre-epoch mirrored volume → True; idempotent.
- [ ] **Step 2 (green):** `_canonicalize_slice_axis` (`dicom_volume.py:136-141`): flip condition
      `dot(slice_dir, n) < 0` with `n = cross(direction[:,0], direction[:,1])`; degenerate
      `|dot| < eps·|n|` → existing dominant-axis rule + warning; docstring updated (the
      "left-handed frame is valid" paragraph replaced by the D6 stance).
      `is_volume_misoriented` (`orientation.py:182-188`): expected origin = IPP endpoint with the
      smaller projection onto `n` (replaces feet-end selection; keep the
      `_AXIAL_DOMINANCE_THRESHOLD` refusal guard and `OrientationUnverifiable` contract).
- [ ] **Step 3 (audit):** raw-index and **spacing-order** consumers of DICOM-sourced volumes
      (`(sy,sx,sz)` → `(sx,sy,sz)`; slice axis stays axis 2): sweep framework callers of
      `read_dicom_series`/`Image.read_dicom_series`; each must read geometry from the
      affine/`Grid`, not positional convention. Fix or confirm each; list them in the PR body.
- [ ] **Commit:** `feat(image)!: canonical slice sense = IOP-normal side (det>0 universal)`

## Task 9: Live no-conform gate + Release B

- [ ] **Step 1:** extend the Task 6 slicer test: a converter-emitted volume (via synthetic DICOM
      series → `read_dicom_series` → save) loads into Slicer and a fresh segmentation exports
      `SAME` **without** `conform_to` (probe P6 as a permanent regression gate; a canonicalizing
      sitk or loader change would fail here).
- [ ] **Step 2:** CHANGELOG `### Breaking` (grid epoch, blast radius, conform-once remediation);
      extend `clarinet/docs/migration-orientation-0.10.17.md` (detect via
      `grid_relation`/`is_volume_misoriented`, repair via idempotent `conform_seg_to_grid`,
      guard via `conform_to`).
- [ ] **Commit:** `feat(image)!: canonical converter epoch — release B`
- [ ] **Release B:** tag; note the version.

## Task 10: `docs/grid-workflows.md` — rationale + probe evidence for future devs

New committed file (repo `docs/`, sibling of `image-service.md`; ships in the PR — openspec
artifacts do not). Narrative layer only; `image-service.md` stays the per-method reference,
`migration-orientation-0.10.17.md` the remediation guide — cross-link, don't duplicate.

- [ ] **Step 1 (outline + draft):** sections: (1) purpose & audience; (2) the grid model (LPS,
      `affine_4x4`, determinant/handedness, `Grid`); (3) conversion workflow (IOP in-plane order,
      IPP ground truth #247/#453, canonical sense = IOP-normal side, why `det > 0` universal);
      (4) segmentation↔volume grid binding and the Slicer author→export→re-read round-trip;
      (5) ITK load-time canonicalization and the mirror (with P1); (6) dev-API decision table
      (`read_grid`, `grid_relation`, in-memory vs disk asserts, `reindex_to`,
      `conform_seg_to_grid`, set-op `resample`, `export_segmentation(conform_to=)`);
      (7) relations catalogue keyed to `GridRelation` (same / rearranged:mirror /
      rearranged:transpose / foreign — arises / detect / repair).
- [ ] **Step 2 (design rationale — the "why" for future devs):** why a value object + enum +
      signed-permutation predicate (vocabulary gap: the epoch itself mass-produces transposes;
      oblique safety); why the single-guard export API (structural fail-open of
      `reference_volume=None`); why the IOP-normal canonical sense (geometry-preserving flip ≠
      mirror; det = n·s; #412 reconciliation — stability + fidelity, not the +dominant anchor);
      why enforcement lives at the write boundary.
- [ ] **Step 3 (probe evidence):** the 2026-07-22 live-Slicer 5.10.0 findings P1–P6 with
      methodology (synthetic det<0/det>0 volumes with marker voxels; node-vs-file matrices;
      sitk full + metadata reads; det<0 reference-geometry export rehearsal; two-layer
      preservation; voxel-coincidence check) — so the sitk-adapter and det<0-export assumptions
      stay re-checkable when Slicer upgrades.
- [ ] **Step 4 (traps, reduced):** IOP defines no det+1 frame (slice axis comes from IPP);
      pre-epoch files carry the in-plane-swap artifact — detect with `grid_relation`, never
      "fix" by editing direction alone; `resample=True` near-threshold verdicts may differ
      between runtimes; `volume.nii.gz` is regenerable — segs painted before/after an epoch sit
      on REARRANGED grids until conformed; degenerate-IOP series fall back to the +dominant
      sense and stay covered by `conform_to`.
- [ ] **Step 5 (cross-link + verify):** row in `clarinet/services/image/CLAUDE.md` "Detailed
      Documentation"; "See also" in `docs/image-service.md` canonicalization section; every
      cited symbol/`file:line` resolves against the tree; no duplication of the per-method
      reference.
- [ ] **Commit:** `docs(image): grid-workflows — lifecycle, rationale, probe evidence, traps`

## Final verification

- [ ] `./scripts/run_tests.sh tests/test_grid.py tests/test_image.py tests/test_orientation.py
      tests/test_slicer_helper_unit.py -q` green (pre-change baseline: 149 across the last
      three; `-k conform` was 4; grid selectors were 0).
- [ ] `uv run pytest -m slicer tests/integration/test_slicer_helper.py` green on live Slicer.
- [ ] `make check` clean; exports + docstrings complete; `grid.py` imports numpy/stdlib only
      (bundle exec test enforces).
- [ ] `docs/grid-workflows.md` complete (rationale + P1–P6 evidence sections present);
      CHANGELOG + migration doc updated; `.claude/rules/slicer-helper-api.md` updated.
- [ ] Grep the diff for project-specific names/paths/thresholds → none (framework-only).
- [ ] Grep ledger/artifacts for `THEORY`/`UNRECONCILED` → none open.
