# Slicer Overlap Detector — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this
> plan task-by-task. Read `design.md` (decisions/risks) and `specs/slicer-overlap-detection/spec.md`
> (acceptance scenarios) alongside it.

**Goal:** Make the image `correspondence/` overlap engine available inside 3D Slicer via an
opt-in text bundle, and add a non-destructive `SlicerHelper.detect_overlaps` first consumer.

**Architecture:** A clarinet-side bundler flattens the (already-pure) correspondence
package to injectable text via `inspect.getsource()`; `SlicerService.execute` opt-in
prepends it (numpy is bundled in Slicer). `detect_overlaps` reuses helper.py's existing
`_export_segments_labelmap` VTK→numpy bridge → `build_overlap_graph`.

**Tech Stack:** Python 3.12 (clarinet) / 3.9 (Slicer runtime); numpy; pytest + pytest-xdist
(`xdist_group("slicer")`); 3D Slicer HTTP exec.

---

## Task 1: Correspondence bundle

- [ ] **Step 1 (red):** Write `tests/test_slicer_correspondence_bundle.py`. Build the bundle,
      assert: no line contains `from clarinet`; at most one `from __future__ import
      annotations`. Then `exec(bundle, ns)` with `ns = {"__name__": "_bundle"}` (numpy
      importable) and assert `"build_overlap_graph" in ns`. Call
      `ns["build_overlap_graph"](a, b, spacing=(1,1,1))` on two toy `np.uint8` labelmaps with
      a known 1-voxel overlap; assert one edge with `inter == <known>`. Run → fails (module
      missing).
- [ ] **Step 2 (green):** Create `clarinet/services/slicer/correspondence_bundle.py`:
      ```python
      import inspect
      from clarinet.services.image.correspondence import graph, matching, measures, model, operations
      _MODULES = (model, measures, matching, operations, graph)  # dependency order: model first
      _cache: str | None = None
      def build_correspondence_bundle() -> str:
          global _cache
          if _cache is not None:
              return _cache
          out: list[str] = []
          for mod in _MODULES:
              skip_paren = False
              for line in inspect.getsource(mod).splitlines():
                  s = line.strip()
                  if skip_paren:                       # inside a stripped ( ... ) import
                      if ")" in s: skip_paren = False
                      continue
                  if s.startswith("from clarinet.services.image.correspondence"):
                      if "(" in s and ")" not in s: skip_paren = True
                      continue
                  if s.startswith("from __future__ import"):
                      continue
                  out.append(line)
              out.append("")
          _cache = "\n".join(out)
          return _cache
      ```
      ⚠️ **Trap:** `matching.py` and `graph.py` import `.model` with a *multi-line*
      parenthesized list — a single-line strip leaves dangling `    Component,` lines →
      `SyntaxError`. The `skip_paren` state machine above removes the whole statement. Run →
      passes.
- [ ] **Step 3:** Commit — `feat(slicer): flatten correspondence engine to injectable bundle`.

## Task 2: SlicerService opt-in

- [ ] **Step 1 (red):** In the slicer service tests, add a case: build the script for a
      trivial payload with `include_correspondence=False` → assert bundle text absent; with
      `True` → assert bundle present AND the composed script starts with exactly one
      `from __future__ import annotations` (count occurrences == 1). Run → fails.
- [ ] **Step 2 (green):** In `clarinet/services/slicer/service.py`, add keyword-only
      `include_correspondence: bool = False` to `execute` and thread into `_build_script`.
      When True, prepend `build_correspondence_bundle()` (import + cache once, mirroring the
      helper-text cache). Ensure a single top-level `from __future__ import annotations`:
      the composed unit is `helper text` (already starts with it) + context + bundle + user
      script — since the bundle's per-module futures are stripped (Task 1) and helper.py's
      top-of-file future governs the whole exec unit, this holds; add an assertion/guard if
      composition order places the bundle before helper. Run → passes.
- [ ] **Step 3:** Commit — `feat(slicer): opt-in correspondence bundle in execute()`.

## Task 3: `detect_overlaps` helper method

- [ ] **Step 1:** Add to the segment-analysis mixin in `clarinet/services/slicer/helper.py`:
      ```python
      def detect_overlaps(self, seg_a, seg_b, *, resample=False):
          if "build_overlap_graph" not in globals():
              raise SlicerHelperError(
                  "detect_overlaps requires the correspondence bundle; "
                  "call execute(..., include_correspondence=True).")
          node_a, node_b = self._unwrap_node(seg_a), self._unwrap_node(seg_b)
          labelmap_b, arr_b = self._export_segments_labelmap(node_b, "_ov_b", what="seg_b", resample=resample)
          try:
              labelmap_a, arr_a = self._export_segments_labelmap(node_a, "_ov_a", what="seg_a", resample=resample)
          except SlicerHelperError:
              slicer.mrmlScene.RemoveNode(labelmap_b); raise
          try:
              if arr_a is None or arr_b is None:      # genuinely-empty source (PR#413) → no-op
                  return []
              sx, sy, sz = (self._image_node.GetSpacing() if self._image_node is not None else (1., 1., 1.))
              graph = build_overlap_graph(arr_a, arr_b, spacing=(sz, sy, sx))  # array axes are (z,y,x)
              names_a, names_b = get_segment_names(node_a), get_segment_names(node_b)
              results = []
              for e in graph.edges:
                  denom = e.size_a + e.size_b
                  union = denom - e.inter
                  results.append({
                      "name_a": names_a[e.a - 1], "name_b": names_b[e.b - 1],  # label i+1 → segment i
                      "inter": e.inter, "size_a": e.size_a, "size_b": e.size_b,
                      "dice": (2 * e.inter / denom) if denom else 0.0,
                      "iou": (e.inter / union) if union else 0.0,
                      "centroid_distance_mm": e.centroid_distance})
              return results
          finally:
              slicer.mrmlScene.RemoveNode(labelmap_a); slicer.mrmlScene.RemoveNode(labelmap_b)
      ```
      ⚠️ **Trap:** reverse `GetSpacing()` (x,y,z)→(z,y,x) or `centroid_distance_mm` is wrong
      (Dice/IoU are count-based, unaffected). `_export_segments_labelmap` labels segment *i*
      as *i+1*, so map names with `e.a - 1`.
- [ ] **Step 2:** Update docs (golden-copy pair — edit both, they are byte-identical per the
      golden-guard test): `.claude/rules/slicer-helper-api.md` and
      `clarinet/docs/slicer-helper-api.md`; add to key-methods in
      `clarinet/services/slicer/CLAUDE.md`.
- [ ] **Step 3:** Commit — `feat(slicer): non-destructive detect_overlaps via correspondence`.

## Task 4: Slicer integration test

- [ ] **Step 1:** Add `tests/integration/test_slicer_detect_overlaps.py`,
      `@pytest.mark.xdist_group("slicer")`. Script (via `execute(..., include_correspondence=True)`)
      loads a small volume, creates two segmentations with a known overlapping segment pair,
      calls `helper.detect_overlaps`, returns results through `__execResult`. Assert: the
      overlapping pair present with `inter > 0` and `0 < dice <= 1`; a disjoint pair → `[]`;
      calling with `include_correspondence` omitted → `SlicerError`/`SlicerHelperError`.
- [ ] **Step 2:** Commit — `test(slicer): detect_overlaps integration on live Slicer`.

## Task 5: Verify & finalize

- [ ] **Step 1:** `./scripts/run_tests.sh -k "correspondence_bundle or detect_overlaps" -q
      > /tmp/test-slicer-overlap.txt 2>&1`; then `make check`. After `make check`, re-Read
      touched files before further edits (ruff may reformat). Fix findings.
- [ ] **Step 2:** Confirm: no new runtime dependency; existing slicer suite green with the
      default `include_correspondence=False` path unchanged. Run `pr-diff-reviewer` before the
      first `gh pr create`.
