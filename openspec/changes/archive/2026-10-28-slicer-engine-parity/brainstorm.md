<!-- Decision log from the /opsx:explore session that produced this change. -->

# Brainstorm — one comparison engine for Slicer and the image service

## Trigger

Requirement stated by the user: *Clarinet and `Clarinet.SlicerHelper` must use the same
overlap-counting engine, and consequently one set of parameters for initializing it.*

`SlicerHelper.subtract_segmentations` (`helper.py:2009`) hand-rolls voxel overlap and takes
only `max_overlap` / `max_overlap_ratio`. `Segmentation.difference` runs the correspondence
engine and accepts `strategy=`. Promoting a different measure (e.g. `OverlapCoefficient`)
on the server side therefore desyncs the two.

## Findings (fact-checked against main @ 15465a5)

- **The engine is already injectable.** `build_correspondence_bundle()` flattens
  model/measures/matching/operations/graph (13 KB) via `inspect.getsource()`;
  `SlicerService.execute(..., include_correspondence=True)` prepends it. Every measure,
  matching strategy and set operation is therefore available inside Slicer today.
- **The shared entry point already exists.** `Segmentation.difference` calls
  `correspond(...)` → `Difference()` → `KeepPlan` → `render()`. `correspond` lives in
  `graph.py`, which is in the bundle, so Slicer can call the identical function.
- **`KeepPlan` is the correct seam.** It is plain data (`tuple[tuple[int, int], ...]`).
  Everything above it is shareable; only the apply step differs — paint voxels server-side,
  remove segment objects in Slicer. That divergence is unavoidable and correct.
- **The two engines disagree today in three ways**, only one of which is the measure:
  1. *Granularity* — Slicer scores each base segment against `arr_b > 0` (sum over the
     union of all subtracted segments); the engine scores per label pair (max over
     components). Diverges when one base segment is overlapped by several disconnected
     subtracted components.
  2. *Boundary* — `ratio > max_overlap_ratio` (Slicer) vs `score >= min_score` (engine).
  3. *Grid* — different resampling paths shift voxel counts near the threshold.
- **No strategy can express the current Slicer rule.** `Measure` is
  `(PairStats) -> float` and never sees sibling edges, so the sum-over-union rule is not
  reachable through `strategy=`. Flattening the subtracted segmentation to a single label
  before building the graph reproduces it exactly — so granularity belongs at the labeling
  step, not in the strategy.
- **The scalars→strategy derivation is stranded.** It sits at `segmentation.py:506-510`,
  inside a module that can never reach Slicer (skimage/pynrrd). Leaving it there forces a
  second copy — one engine with two interpretations of its parameters.
- **Moving it is bundle-safe with no bundler change.** `build_correspondence_bundle` strips
  every `from clarinet` / `import clarinet` line, not only `.model` ones, and `_MODULES`
  (`correspondence_bundle.py:14`) already orders `measures` before `matching`.
- **The bundle has no production entry point.** `api/routers/slicer.py` calls
  `service.execute(...)` at lines 62, 201 and 282 without the flag, and the `/exec` request
  body has no field for it. An engine-backed `subtract_segmentations` would raise on every
  record script unless this is fixed in the same change.

## Rejected

- Adding `strategy=` without unifying the engine — makes the signatures match while the
  semantics still diverge on granularity and boundary.
- A hand-rolled fallback when the bundle is absent — a silent second path defeats the
  requirement more thoroughly than an honest divergence.
- Strategy serialization (name→class registry) — not needed for the requirement; bundle
  symbols are in scope, so a script can construct the strategy inline. Deferred until a
  choice must travel as data.

## Origin of the design decisions

D6–D9 were first recorded on `slicer-overlap-detector/design.md` while that change was
still the active context; they are restated in this change's `design.md`, which is where
they bind implementation.
