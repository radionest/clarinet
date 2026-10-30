## Context

The image-service overlap engine (`clarinet/services/image/correspondence/`) is pure
numpy + stdlib and already backs `Segmentation` set-ops. 3D Slicer runs its own Python
(numpy + scipy + vtk bundled; **no** skimage/pynrrd/clarinet), and `helper.py` is injected
into Slicer **as text** (with a `_Dummy` fallback so it stays importable outside Slicer).
`helper.py` currently hand-rolls voxel-overlap logic in `subtract_segmentations`.

This change makes the real, tested engine available inside Slicer scripts and adds a
non-destructive overlap detector as its first consumer. Hard constraints: only
`correspondence/` is text-injectable (numpy/stdlib only); `segmentation.py`/
`layered_segmentation.py` pull skimage/pynrrd, which stock Slicer lacks. Slicer runs
Python 3.9.

## Goals / Non-Goals

**Goals:**
- Opt-in bundling of the correspondence engine into the Slicer script payload, with the
  correspondence package as the single source of truth (no maintained flattened copy).
- One non-destructive `SlicerHelper.detect_overlaps` returning per-segment-pair overlap
  statistics.
- Deterministic CI proof of Slicer-runtime compatibility (exec the bundle in a numpy-only
  namespace — no live Slicer needed).

**Non-Goals:**
- Decoupling the image service from clarinet — deferred to #461.
- Replacing `subtract_segmentations`/`merge_as_pool`/`binarize_and_split_islands` with the
  engine — deferred to #462.
- Injecting `Segmentation`/`Image`/`LayeredSegmentation` (skimage/pynrrd unavailable in
  Slicer).
- Connected-component-level granularity (segment-level only for now).
- Physical extraction to a standalone imaging library.

## Decisions

### D1: This change is the Slicer feature only; decoupling ships separately
- **Choice:** implement the bundle + detector now; image-service decoupling is issue #461.
- **Rationale:** the feature does not depend on decoupling — `correspondence/` is already
  pure, and the detector reuses `helper.py`'s own VTK→numpy bridge, not `Image`/
  `Segmentation`.
- **Alternative rejected:** fold decoupling into this change — couples a small feature to a
  broader refactor and slows delivery.

### D2: Runtime-flatten the real correspondence source (single source of truth)
- **Choice:** `inspect.getsource()` the 5 modules in dependency order (model → measures,
  matching, operations, graph), strip intra-package `from clarinet…correspondence` imports
  and per-module `from __future__` lines, concatenate, cache once.
- **Rationale:** the injected text **is** the real module source — no golden-copy sync trap
  (cf. the byte-identical `clarinet/docs/*.md` maintenance burden).
- **Alternative rejected:** a hand-maintained flattened bundle file — drifts from source.

### D3: Exactly one top-level `from __future__ import annotations`
- **Choice:** the composed Slicer script carries one, at the very top; per-module ones are
  stripped by the bundler.
- **Rationale:** Slicer runs Python 3.9; `X | None` parameter annotations (in operations.py
  / graph.py) are evaluated at definition time without the future import → `TypeError`. One
  import at the top of the concatenated exec unit stringizes all annotations in the unit.
- **Alternative rejected:** keep per-module imports (illegal mid-unit → `SyntaxError`);
  rewrite annotations to `typing.Optional` (needlessly edits the real source).

### D4: Opt-in via `SlicerService.execute(..., include_correspondence=False)`
- **Choice:** keyword-only flag; the bundle is prepended only when `True`. The detector
  raises `SlicerHelperError` if invoked without the bundle present.
- **Rationale:** keeps every other script lean (helper.py is already ~2782 lines); explicit
  opt-in per the user's "optional way" framing.
- **Alternative rejected:** always bundle (bloats every payload); per-record-type config
  (premature — no consumer needs it yet).

### D5: Detector reuses the export bridge; segment-level granularity
- **Choice:** `detect_overlaps(seg_a, seg_b, *, resample=False) -> list[dict]` reuses
  `_export_segments_labelmap` (shared reference geometry + the PR #413 empty-vs-foreign-grid
  guards) → integer labelmaps → `build_overlap_graph`. Each segment = one label = one graph
  component. Returns `{name_a, name_b, inter, size_a, size_b, dice, iou,
  centroid_distance_mm}` per overlapping pair.
- **Rationale:** DRY with existing helper VTK code; inherits battle-tested guards; matches
  `subtract_segmentations` semantics.
- **Alternative rejected:** connected-component-level (scipy.ndimage.label first) — richer
  but diverges from existing semantics; documented as a future option.

### Constraints on the deferred engine unification (#462)

Not implemented here. Recorded because they were settled while designing this change and
they bound the shape of #462.

#### D6: One engine implies one parameter set
- **Choice:** `SlicerHelper.subtract_segmentations` (`helper.py:2009`) and
  `Segmentation.difference` both call `correspond()` (`graph.py`, already in the bundle),
  consume the resulting `KeepPlan`, and expose the identical engine parameter set:
  `strategy`, `max_overlap`, `max_overlap_ratio`, `resample`, plus the D8 granularity
  parameter. `output_name` stays Slicer-only — node handling, not engine.
- **Rationale:** a shared threshold value is not a shared rule. Per-side defaults would
  ship one engine that returns two answers.
- **Consequence:** the apply step is the only permitted divergence — `render()` to numpy
  server-side, segment removal in Slicer. Both sit below `KeepPlan`, which is plain data.

#### D7: Scalars→strategy derivation moves into `correspondence/`
- **Choice:** move the derivation at `segmentation.py:506-510` into
  `correspondence/matching.py` (importing `measures`); both call sites use it.
- **Rationale:** `segmentation.py` can never reach Slicer (skimage/pynrrd), so leaving the
  derivation there forces a second copy — one engine, two interpretations of its
  parameters.
- **Bundle-safe, no bundler change:** `build_correspondence_bundle` strips every
  `from clarinet` / `import clarinet` line, not only `.model` ones, and
  `_MODULES` (`correspondence_bundle.py:14`) already orders `measures` before `matching`.

#### D8: Granularity is a labeling parameter, not a strategy
- **Choice:** expose how B enters the graph — per-segment, or flattened to one label.
  Default per-component, matching `Segmentation.difference`.
- **Rationale:** `Measure` is `(PairStats) -> float` and never sees sibling edges, so no
  strategy can express the current segment-vs-union rule. Flattening B to a single label
  reproduces it exactly, which puts the knob at the export step.
- **Consequence:** #462 is a behavior change, not a semantics-preserving refactor. Verdicts
  shift where one A segment overlaps several disconnected B components each below
  threshold (union sums, engine takes the max), and at the threshold itself
  (`ratio > max_overlap_ratio` vs `score >= min_score`). D6 forbids resolving this with a
  per-side default; union must be an explicit parameter value.

#### D9: Engine-backed subtract needs the bundle on production script paths
- **Choice:** `/records/{id}/open` and `/records/{id}/validate`
  (`api/routers/slicer.py:201,282`) pass `include_correspondence=True`, or a `RecordType`
  field drives it. Ships in the same change as #462. No hand-rolled fallback.
- **Rationale:** no router passes the flag today, so an engine-backed
  `subtract_segmentations` would raise on every record script. A fallback path would
  reintroduce the divergence D6 exists to remove.
- **Cost:** 13 KB bundle against a 120 KB helper payload (~11%) — always-on is acceptable.

## Risks / Trade-offs

- [Risk] Slicer's numpy/Python too old for the engine → Mitigation: the engine uses only
  ancient, stable numpy (`bincount`/`unique`/`broadcast_to`/`linalg.norm`/`iinfo`) and no
  syntax newer than 3.9-safe (no `Self`, no PEP 695). The CI bundler test execs the bundle
  in a numpy-only namespace and proves this deterministically.
- [Risk] Axis-order mismatch corrupts `centroid_distance_mm` → Mitigation: reverse
  `volume.GetSpacing()` to (z,y,x) to match the exported array; Dice/IoU are count-based and
  unaffected; the Slicer integration test covers it.
- [Trade-off] The runtime flattener couples the bundler to correspondence's import structure
  — intra-package imports are stripped regardless of target, but a new one must respect the
  `_MODULES` concatenation order → Accept: the bundler unit test asserts no `from clarinet`
  survives, so a cross-import that breaks the exec unit fails loudly.
- [Trade-off] The bundle adds ~200 lines to opted-in scripts → Accept: opt-in only,
  negligible next to helper.py.

## Migration Plan

N/A — this change makes no deployment, DB, or endpoint changes. It is purely additive: a
new module, a new keyword-only parameter whose default preserves current behavior, and a
new helper method. No migration, no rollback steps beyond reverting the commit.

## Open Questions

None blocking. Return shape settled as `list[dict]` for JSON / `__execResult` friendliness
rather than a dataclass.

Deferred with #462, not settled here: whether a strategy must be constructible from
configuration rather than from script text. D6 is satisfied either way — bundle symbols are
in scope, so a Slicer script can construct `ThresholdMatch(...)` inline and the parameter
sets still match. A name→class registry in `correspondence/` is only required if the choice
has to travel as data (settings, context injection, audit trail).
