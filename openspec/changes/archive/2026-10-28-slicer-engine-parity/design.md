## Context

The correspondence engine is already injectable into Slicer (`build_correspondence_bundle`,
shipped with `detect_overlaps` in PR #463) and already exposes the shared entry point
`correspond()` in `graph.py`. What is missing is that `subtract_segmentations` does not use
it, and cannot be told which measure to apply.

Decisions D6–D9 were first recorded on `slicer-overlap-detector/design.md` as constraints on
deferred work; they are restated here, where they bind implementation. Full derivation is in
this change's `brainstorm.md`.

## Goals / Non-Goals

**Goals:**
- One engine path for the removal decision across both runtimes: every verdict-affecting
  step — scalars→strategy derivation, granularity labeling, graph build, matching, set
  operation — executes the same bundled source.
- The runtime-specific ends are decision-free, not a tolerated disagreement: operand
  export runs before the engine sees arrays, and verdict materialization consumes a fixed
  `KeepPlan` it cannot alter.
- One parameter set, including a strategy override, on both call sites.
- Granularity made explicit rather than implied by which runtime you are in.

**Non-Goals:**
- Strategy serialization / configuration-driven strategy selection.
- `merge_as_pool`, `binarize_and_split_islands` — not comparison operations.
- Changing `Segmentation.difference`'s external behavior.

## Decisions

### D6: One engine implies one parameter set
- **Choice:** `subtract_segmentations` and `Segmentation.difference` both call
  `correspond()`, consume the resulting `KeepPlan`, and expose the identical engine
  parameter set: `strategy`, `max_overlap`, `max_overlap_ratio`, `resample`, plus the D8
  granularity parameter. `output_name` stays Slicer-only — node handling, not engine.
- **Rationale:** a shared threshold value is not a shared rule. Per-side defaults would ship
  one engine that returns two answers.
- **Consequence:** the only permitted divergences are the decision-free ends — operand
  export above the engine, and the apply step below `KeepPlan` (`render()` to numpy
  server-side, segment removal in Slicer). `KeepPlan` is plain data; neither end can
  alter a verdict.

### D7: Scalars→strategy derivation moves into `correspondence/`
- **Choice:** move the derivation at `segmentation.py:506-510` into
  `correspondence/matching.py` (importing `measures`); both call sites use it.
- **Rationale:** `segmentation.py` can never reach Slicer (skimage/pynrrd), so leaving the
  derivation there forces a second copy — one engine, two interpretations of its parameters.
- **Bundle-safe, no bundler change:** `build_correspondence_bundle` strips every
  `from clarinet` / `import clarinet` line, not only `.model` ones, and `_MODULES`
  (`correspondence_bundle.py:14`) already orders `measures` before `matching`.

### D8: Granularity is a labeling parameter, not a strategy
- **Choice:** expose how the subtracted segmentation enters the graph — per-segment, or
  flattened to one label. Default per-component, matching `Segmentation.difference`.
- **Rationale:** `Measure` is `(PairStats) -> float` and never sees sibling edges, so no
  strategy can express the current segment-vs-union rule. Flattening to a single label
  reproduces it exactly, which puts the knob at the export step.
- **Consequence:** this is a behavior change. D6 forbids resolving it with a per-side
  default; union must be an explicit parameter value.

### D9: Engine-backed subtract needs the bundle on production script paths
- **Choice:** `/records/{id}/open` and `/records/{id}/validate`
  (`api/routers/slicer.py:201,282`) make the bundle available, and `/exec` (line 62) can
  request it. No hand-rolled fallback.
- **Rationale:** no router passes the flag today, so an engine-backed
  `subtract_segmentations` would raise on every record script. A fallback path would
  reintroduce the divergence D6 exists to remove.
- **Cost:** 13 KB bundle against a 120 KB helper payload (~11%) — always-on is acceptable
  for record paths.

### D10: Parameter parity is enforced by test, not by convention
- **Choice:** a test asserts the engine parameter names of `subtract_segmentations` and
  `Segmentation.difference` match, excluding the documented runtime-only names.
- **Rationale:** the two live in different modules with different import constraints; only
  an executable check keeps them from drifting apart again.

## Risks / Trade-offs

- [Risk] Downstream scripts silently change verdicts at the margin → Mitigation: the shift
  is confined to three named cases — fragmented sub-threshold overlap (restored by union
  granularity), exactly at the ratio threshold (legacy `>` becomes `>=`; the absolute
  boundary is preserved by the `+1` derivation), and both scalars set (legacy removed only
  when both thresholds were exceeded; the shared derivation gives the ratio precedence).
  Union granularity restores only the first; the release notes must name all three.
- [Risk] `resample=True` re-gridding stays runtime-native — labelmap re-export in Slicer
  vs nearest-neighbour `reindex_to` server-side — so near-threshold verdicts can differ
  between runtimes on that opt-in path → Mitigation: the default refuses mismatched grids
  on both sides, and the spec documents the divergence instead of leaving it silent.
- [Risk] Always-on bundling grows every record script payload → Accept: ~11% over a payload
  that already carries the full helper.
- [Trade-off] Moving the derivation adds an intra-package import that the bundler's
  concatenation order must satisfy → Accept: the existing numpy-only exec test fails loudly
  on a wrong order.

## Migration Plan

No DB or endpoint changes. The behavior shift is the migration: callers relying on
sum-over-union select that granularity explicitly. Rollback is reverting the commit.

## Open Questions

Whether record script paths always receive the bundle or opt in via a `RecordType` field.
Always-on is simpler and the payload cost is negligible; a field is warranted only if some
deployment needs to keep scripts minimal.
