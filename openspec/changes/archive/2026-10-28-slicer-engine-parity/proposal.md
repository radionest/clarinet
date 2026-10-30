## Why

`SlicerHelper.subtract_segmentations` and `Segmentation.difference` answer the same
question — "do these two ROIs overlap enough to count as one?" — with two different
implementations. They disagree on granularity (sum over the union vs. max over components),
on the threshold boundary (`>` vs `>=`), and the Slicer side cannot express a measure at
all: it has no `strategy=` parameter. Any change to the server-side matching rule silently
desyncs the two runtimes.

This change makes both call the same engine through the same entry point, with the same
parameter set, so the comparison rule is chosen once and honored everywhere.

## What Changes

- **`SlicerHelper.subtract_segmentations` runs the shared engine.** The hand-rolled voxel
  loop is replaced by `correspond()` → `Difference()` → `KeepPlan`, the same path
  `Segmentation.difference` uses. Only the apply step stays runtime-specific: segment
  removal in Slicer, `render()` server-side.
- **The scalars→strategy derivation moves into `correspondence/`.** It currently lives at
  `segmentation.py:506-510`, which can never reach Slicer. Both call sites use the shared
  helper; the bundle carries it automatically.
- **Granularity becomes an explicit parameter** with a per-component default matching
  `Segmentation.difference`. The legacy sum-over-union rule stays reachable as an explicit
  value.
- **Production script paths get the bundle.** Record-configured scripts must be able to
  call engine-backed helper methods without raising.

**`SlicerHelper.subtract_segmentations` signature**
- From: `(seg_a, seg_b, output_name=None, max_overlap=0, max_overlap_ratio=None, resample=False)`
- To: the same plus `strategy=None` and a granularity selector.
- Reason: parameter parity with `Segmentation.difference` is what makes "one engine" mean
  "one answer".
- Impact: **behavior change, not a semantics-preserving refactor.** Verdicts shift in three
  cases: a base segment overlapped by several disconnected subtracted components each below
  threshold but summing above it; exactly at the ratio threshold (legacy `>` becomes `>=`;
  the absolute boundary survives via the `+1` derivation); and both scalars set (legacy
  removed only when both thresholds were exceeded — the shared derivation gives the ratio
  precedence and ignores `max_overlap`, matching `Segmentation.difference`). Union
  granularity restores only the first case.

## Capabilities

### New Capabilities
- `slicer-comparison-engine`: `SlicerHelper` segment comparison backed by the shared
  correspondence engine, with a single parameter set across both runtimes.

### Modified Capabilities
- `slicer-overlap-detection`: "Opt-in correspondence bundle in Slicer scripts" — record-open
  and result-validator paths now supply the engine instead of leaving it unreachable outside
  tests. Ad-hoc execution keeps explicit opt-in.

## Impact

- **Code:** `clarinet/services/slicer/helper.py` (`subtract_segmentations`);
  `clarinet/services/image/correspondence/matching.py` (derivation moves in);
  `clarinet/services/image/segmentation.py` (call the shared derivation);
  `clarinet/api/routers/slicer.py` (bundle on script paths); tests under `tests/`.
- **APIs:** `subtract_segmentations` gains two parameters and changes behavior at the
  margin. `Segmentation.difference` is unchanged externally.
- **Docs:** `.claude/rules/slicer-helper-api.md` and its byte-identical golden copy
  `clarinet/docs/slicer-helper-api.md` (guarded by `tests/test_agent_scaffold.py:37` — edit
  both); key-methods list in `clarinet/services/slicer/CLAUDE.md`.
- **Dependencies:** none added.
- **Supersedes:** issue #462, whose "preserve `max_overlap`/`max_overlap_ratio` semantics"
  constraint is unimplementable as written — mapping onto `Segmentation.difference`'s
  strategies necessarily changes granularity and boundary behavior.
- **Out of scope:** strategy serialization (a name→class registry so a strategy can travel
  as configuration data); `merge_as_pool` and `binarize_and_split_islands`, which are not
  comparison operations.
