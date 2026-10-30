# Tasks — slicer-engine-parity

Specs: `specs/slicer-comparison-engine/spec.md`, `specs/slicer-overlap-detection/spec.md`.
Decisions D6–D10: `design.md`.

## 1. Shared scalars→strategy derivation (D7)

- [x] 1.1 Add `strategy_from_thresholds(max_overlap=0, max_overlap_ratio=None)` to `clarinet/services/image/correspondence/matching.py` (imports `measures` — `Coverage`/`AbsoluteOverlap`; ratio precedence, `+1` absolute boundary) and replace the inline derivation at `clarinet/services/image/segmentation.py:506-510` with a call to it
- [x] 1.2 Extend `tests/test_slicer_correspondence_bundle.py`: derivation is defined and callable after exec'ing the bundle in a numpy-only namespace; identical scalars produce equal strategies (measure type + `min_score`); ratio takes precedence when both scalars are set

## 2. Granularity parameter on the server engine (D6, D8)

- [x] 2.1 Add the granularity selector to `Segmentation.difference` (keyword-only, default = labels enter the graph as-is, matching current behavior; union value flattens `other`'s labels to one before `correspond()`)
- [x] 2.2 Unit tests (server side): fragmented sub-threshold overlap is kept under the default and removed under union; default-path results unchanged vs main

## 3. Engine-backed `subtract_segmentations` (D6, D8)

- [x] 3.1 Rewrite `SlicerHelper.subtract_segmentations` (`clarinet/services/slicer/helper.py:2009`): bundle-presence guard raises `SlicerHelperError` first (before export, so it fires even on empty operands); keep `_export_segments_labelmap` export with its grid/empty guards; apply granularity labeling to `arr_b`; decide removals via `correspond()` → `Difference()` → `KeepPlan`; keep the `output_name` / in-place apply paths; add `strategy` + granularity parameters
- [x] 3.2 Unit tests (no Slicer, `_Dummy` namespace): missing-bundle raise, including with empty inputs; engine-decision equivalence — bundle-exec'd `correspond`+`Difference` on arrays yields the same removal set as `Segmentation.difference` on the same arrays (incl. exact-ratio boundary `>=` and both-scalars ratio precedence)
- [x] 3.3 Update live-Slicer integration tests (`tests/integration/test_slicer_helper.py`) to opt in to the bundle; adapt legacy-semantics tests (`test_setop_tolerates_emptied_source`, grid-guard tests); add live tests for strategy override and union granularity

## 4. Parameter parity enforcement (D10)

- [x] 4.1 Add a parity test asserting the engine parameter names of `subtract_segmentations` and `Segmentation.difference` match, excluding the documented runtime-only names (`seg_a`/`seg_b`/`output_name` vs `other`)

## 5. Bundle on production script paths (D9)

- [x] 5.1 `clarinet/api/routers/slicer.py`: `/records/{id}/open` (line 201) and `/records/{id}/validate` (line 282) execute with `include_correspondence=True`; `SlicerExecRequest` gains `include_correspondence: bool = False` and `/exec` (line 62) forwards it
- [x] 5.2 `clarinet/api/routers/record.py`: submit-path validator execute (line 529, `_process_submission`) passes `include_correspondence=True`
- [x] 5.3 Tests: open/validate/submit paths assert the bundle reaches the composed script (extend `tests/integration/test_record_submit_execresult.py` for the submit path); `/exec` default excludes the bundle and the request flag includes it

## 6. Docs and verification

- [x] 6.1 Update `.claude/rules/slicer-helper-api.md` (`subtract_segmentations` entry: engine-backed, new parameters, bundle requirement, resample divergence note, the three verdict shifts) and re-copy byte-identical to `clarinet/docs/slicer-helper-api.md` (golden guard in `tests/test_agent_scaffold.py`)
- [x] 6.2 Update `clarinet/services/slicer/CLAUDE.md` (key-methods line, router endpoint notes) and the `Segmentation.difference` / derivation docstrings
- [x] 6.3 Full gates: `make check` + unit/fast suites green; PR body names the three verdict-shift cases (fragmented sub-threshold overlap, exact ratio threshold, both scalars set)
