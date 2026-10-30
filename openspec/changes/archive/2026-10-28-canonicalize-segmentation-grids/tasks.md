# Tasks

Ordering: **Group 1 (grid core + repair) → Group 2 (Slicer export) → Release A → Group 3
(converter epoch) → Release B → Group 4 (docs) → Group 5 (gates)**. Groups 1–2 are Release A
(additive vocabulary + sanctioned safety breaks); Group 3 is the breaking grid epoch (Release B),
sequenced after so repair primitives exist before the epoch that needs them. Every group is
framework-only — no project-specific scripts, storage walks, or thresholds.

## 1. Grid core + guarded repair (segmentation-grid-inspection)

- [x] 1.1 `clarinet/services/image/grid.py` — pure core (numpy + stdlib ONLY, zero clarinet
      imports; bundle constraint): frozen `Grid` (shape + LPS affine; `spacing`/`direction`/
      `origin`/`summary()`), `GridRelation` (`SAME|REARRANGED|FOREIGN` + `perm`/`flips` detail),
      `grid_relation(a, b, *, atol)` via the signed-permutation predicate (design D2).
      Property tests: random grid × random signed permutation → REARRANGED with matching detail;
      identity → SAME; perturbed offset/rotation/shape → FOREIGN; oblique grids covered.
- [x] 1.2 `clarinet/services/image/grid_io.py` — `read_grid(path) -> Grid` (NIfTI + 3-D NRRD via
      `Image` metadata reads, 4-D NRRD via `LayeredSegmentation`; spatial shape only for 4-D
      NIfTI), `assert_same_grid_on_disk(path_a, path_b, *, atol=1e-4)` raising
      `GeometryMismatchError` with both `Grid.summary()` strings. Export new symbols from
      `clarinet/services/image/__init__.py`. Include the "catches what the in-memory guard
      misses" contrast test.
- [x] 1.3 Reader hardening: honor NRRD `space` (LPS as-is; RAS/LAS → LPS; else `ImageReadError`)
      in one shared helper used by `Image.read_nrrd` + `LayeredSegmentation._apply_grid_from_header`;
      `Image.read_nrrd` raises `ImageReadError` on `ndim != 3`; add `Image.grid` /
      `LayeredSegmentation.grid` properties; `same_grid`/`assert_same_grid` delegate to `Grid`
      (signatures unchanged); `_grid_summary` reduced to a `Grid.summary()` call.
- [x] 1.4 `conform_seg_to_grid` — relation gate (`FOREIGN` raises `GeometryMismatchError` unless
      `allow_resample=True`) + 4-D multi-layer support (per-layer exact rearrangement via
      `LayeredSegmentation`, names/labels/layers preserved). Update the 4 existing conform tests
      if any construct FOREIGN pairs; add REARRANGED-exactness and 4-D tests.

## 2. Slicer conform-on-export (slicer-conformed-export)

- [x] 2.1 Append `grid` to `_MODULES` in `correspondence_bundle.py` (dependency order:
      before graph-dependent modules is irrelevant — grid is standalone; keep `model` first).
      Verify the flattened bundle still `exec`s standalone (extend the existing bundle test).
- [x] 2.2 `helper.py`: `_read_grid_on_disk(path)` via `sitk.ImageFileReader.ReadImageInformation()`
      → bundled `Grid` (LPS; 4-D `.seg.nrrd` arrives as 3-D vector image — probe P4);
      `export_segmentation(name, output_path, *, conform_to: str | None = None)` implementing
      design D4 (classify → SAME direct / REARRANGED exact re-grid on temp node, names+colors+
      labels+layers preserved / FOREIGN raise; post-write disk assert; delete file on assert
      failure; temp nodes removed in `finally`; caller's node never mutated; bundle-absent →
      raise). **Remove `reference_volume=`**; rename `assert_segmentation_matches_volume` →
      `_assert_segmentation_matches_volume` (kept only for `load_segmentation`'s best-effort
      check); update all framework-internal call sites.
- [x] 2.3 Unit tests (`tests/test_slicer_helper_unit.py`, `_Dummy` path): `conform_to` without
      bundle raises; missing `conform_to` file raises; no-guard export unchanged;
      `reference_volume=` now a TypeError.
- [x] 2.4 Live-Slicer integration test (`tests/integration/test_slicer_helper.py`,
      `@pytest.mark.slicer`): synthetic **left-handed** volume + fresh two-segment overlapping
      segmentation; export without `conform_to` → `grid_relation` REARRANGED (slice flip);
      with `conform_to` → SAME, det<0 written verbatim, 2 layers + segment names preserved,
      voxel-exact vs the unconformed export; FOREIGN reference → raises, no file.
- [x] 2.5 **Release A**: CHANGELOG entries (see 4.1) + tag; note the version.

## 3. Canonical converter epoch (canonical-volume-conversion) — breaking, Release B

- [x] 3.1 `dicom_volume.py`: drop the `d[:, [1,0,2]]` in-plane swap **end-to-end** (array +
      direction together; `read_dicom_series` and emitted-file re-read agree). Never re-orient at
      write time only.
- [x] 3.2 `_canonicalize_slice_axis`: canonical sense = IOP-normal side
      (`dot(slice_dir, cross(d[:,0], d[:,1])) < 0` → geometry-preserving flip); degenerate
      normal → +dominant fallback + warning. Tests: standard axial → det>0 +
      `grid_relation(emitted, ground_truth) == SAME`; IPP-opposing-normal synthetic → det>0,
      positions preserved; negative-dominant-normal synthetic → det>0, positions preserved;
      re-conversion grid-stable; before/after epoch pair → physically equivalent, REARRANGED,
      zero voxel drift (`reindex_to` round-trip).
- [x] 3.3 `is_volume_misoriented`: expected origin = IPP endpoint with smaller projection onto
      the IOP normal (was: feet-end); idempotence + negative-dominant-normal tests.
- [x] 3.4 Audit raw-index consumers of DICOM-sourced arrays **and** spacing-order consumers
      (`(sy,sx,sz)` → `(sx,sy,sz)`); confirm each reads grid from the affine or fix it. Slice
      axis stays axis 2.
- [x] 3.5 Extend the live-Slicer test: fresh segmentation against a converter-emitted volume
      exports `SAME` **without** `conform_to` (probe P6 as a regression gate).
- [x] 3.6 **Release B**: CHANGELOG Breaking + migration-doc section (4.2) + tag.

## 4. Documentation (committed — ships in the feature PRs, not only in openspec/)

- [x] 4.1 CHANGELOG `## Unreleased` → `### Breaking`: Release A (helper API removal +
      `conform_seg_to_grid` FOREIGN default + `read_nrrd` 4-D/space raises) and Release B
      (converter grid epoch, blast radius, conform-once remediation) entries.
- [x] 4.2 Extend `clarinet/docs/migration-orientation-0.10.17.md`: the conversion-orientation
      epoch — detect via `grid_relation`/`is_volume_misoriented`, repair via idempotent
      `conform_seg_to_grid`, guard future exports via `conform_to`.
- [x] 4.3 **New `docs/grid-workflows.md`** — the narrative layer for future devs; must carry the
      *rationale and evidence*, not just API: (a) grid lifecycle (conversion, LPS, seg↔volume
      binding, Slicer author→export→re-read); (b) dev-API decision table (`read_grid`,
      `grid_relation`, in-memory vs disk-level asserts, `reindex_to`, `conform_seg_to_grid`,
      set-op `resample`, `export_segmentation(conform_to=)`); (c) relations catalogue keyed to
      `GridRelation` (same / rearranged:mirror / rearranged:transpose / foreign — how each
      arises, detect, repair); (d) **Design rationale** — why a value object + enum + signed-
      permutation predicate, why the single-guard export API (structural fail-open), why the
      IOP-normal canonical sense (geometry-preserving flip ≠ mirror; #412 reconciliation);
      (e) **Probe evidence** — the 2026-07-22 live-Slicer 5.10 findings P1–P6 with methodology
      (synthetic det<0/det>0 volumes, round-trip, conform rehearsal), so the sitk-adapter and
      det<0-export claims stay checkable; (f) reduced traps catalogue (IOP defines no det+1
      frame; pre-epoch files carry the in-plane-swap artifact; near-threshold `resample=True`
      verdicts; regenerable `volume.nii.gz` across epochs; degenerate-IOP fallback). Cross-ref
      `image-service.md` + `migration-orientation-0.10.17.md`; do not duplicate them.
- [x] 4.4 Cross-link: row in `clarinet/services/image/CLAUDE.md` "Detailed Documentation";
      "See also" from `docs/image-service.md`'s canonicalization section; update
      `.claude/rules/slicer-helper-api.md` (`export_segmentation` line, removed guard,
      bundle-carried grid symbols).

## 5. Verification gates

- [x] 5.1 `./scripts/run_tests.sh tests/test_grid.py tests/test_image.py tests/test_orientation.py
      tests/test_slicer_helper_unit.py -q` green (baseline before this change: 149 collected
      across the last three; `-k "grid_relation or read_grid or GridRelation"` collects 0 —
      TDD red baseline; `-k conform` collects 4).
- [x] 5.2 Live-Slicer tests (2.4, 3.5) pass against a live Slicer (`-m slicer`); no skips
      counted as done.
- [x] 5.3 `make check` clean; new public symbols exported from
      `clarinet/services/image/__init__.py` with docstrings; `grid.py` import-audit (numpy +
      stdlib only) enforced by the bundle exec test.
- [x] 5.4 Docs complete per 4.1–4.4; every symbol/`file:line` cited by `grid-workflows.md`
      resolves against the tree; no project-specific names, paths, or thresholds in the diff.
