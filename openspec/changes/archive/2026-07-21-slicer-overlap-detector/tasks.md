## 1. Correspondence bundle (clarinet-side, CI-testable)

- [x] 1.1 Write failing unit test `tests/test_slicer_correspondence_bundle.py`: `exec` the
      (not-yet-existing) bundle text in a namespace whose only third-party import is numpy;
      assert `build_overlap_graph` is defined and returns correct overlapping-pair edges on
      a toy 3-D integer labelmap pair; assert the text has no `from clarinet` line and at
      most one `from __future__ import annotations`.
- [x] 1.2 Implement `clarinet/services/slicer/correspondence_bundle.py` —
      `build_correspondence_bundle() -> str`: `inspect.getsource()` the modules in
      dependency order (`model` → `measures` → `matching` → `operations` → `graph`), strip
      lines matching `from clarinet.services.image.correspondence` and
      `from __future__ import`, concatenate, cache at module scope. Make 1.1 pass.

## 2. SlicerService opt-in

- [x] 2.1 Write failing test for `SlicerService.execute(..., include_correspondence=...)`:
      default (False) → composed payload excludes the bundle (existing behavior); True →
      payload contains the bundle and exactly one top-level `from __future__ import
      annotations`.
- [x] 2.2 Add the keyword-only `include_correspondence: bool = False` to `execute` /
      `_build_script` in `clarinet/services/slicer/service.py`; prepend the cached bundle
      when True; guarantee a single top-of-script `from __future__ import annotations`. Make
      2.1 pass.

## 3. `detect_overlaps` helper method

- [x] 3.1 Add `SlicerHelper.detect_overlaps(seg_a, seg_b, *, resample=False) -> list[dict]`
      to `clarinet/services/slicer/helper.py` (segment-analysis mixin): unwrap nodes;
      `_export_segments_labelmap` for both (inherits the PR #413 empty-vs-foreign-grid
      guards; a `None` array → empty-result path); spacing from `_image_node.GetSpacing()`
      reversed to (z,y,x); `build_overlap_graph`; map labels→names via `get_segment_names`
      (segment i → label i+1); compute `dice`/`iou` per edge; remove temp labelmap nodes in
      `finally`; raise `SlicerHelperError` if `build_overlap_graph` is not in `globals()`.
- [x] 3.2 Document the method: update `.claude/rules/slicer-helper-api.md` **and** its
      byte-identical golden copy `clarinet/docs/slicer-helper-api.md` (golden-guard test);
      add it to the key-methods list in `clarinet/services/slicer/CLAUDE.md`.

## 4. Slicer integration test

- [x] 4.1 Add `tests/integration/test_slicer_detect_overlaps.py` under
      `xdist_group("slicer")`: `execute(..., include_correspondence=True)` a script that
      loads a volume, creates two known overlapping segments, and calls `detect_overlaps`;
      assert overlap stats via `__execResult`; assert disjoint segments → `[]`; assert
      calling without the bundle raises `SlicerHelperError`.

## 5. Verify & finalize

- [x] 5.1 `./scripts/run_tests.sh -k "correspondence_bundle or detect_overlaps" -q`
      (redirect to a file), then `make check` (format + lint + typecheck); fix findings.
      After `make check`, re-read touched files before any further edit (ruff may reformat).
- [x] 5.2 Confirm no new runtime dependency was added and that the existing slicer test
      suite passes with the default `include_correspondence=False` path unchanged.
