## Why

The image-service `correspondence/` overlap engine is pure numpy/stdlib and already backs
`Segmentation` set-ops, yet Slicer's `helper.py` re-implements overlap logic by hand in
`subtract_segmentations`. There is no supported way to run the real, tested engine inside a
Slicer script. This change adds an opt-in way to bundle the engine into the Slicer runtime
plus a non-destructive overlap detector, so overlap analysis inside Slicer can use one
shared implementation instead of a hand-rolled duplicate — and unblocks the later DRY
refactor (#462).

## What Changes

- **New** `clarinet/services/slicer/correspondence_bundle.py` — flattens the correspondence
  package to injectable text via `inspect.getsource()` (single source of truth; cached
  once).
- **New** `SlicerHelper.detect_overlaps(seg_a, seg_b, *, resample=False) -> list[dict]` —
  non-destructive overlap detector reusing the existing `_export_segments_labelmap` bridge;
  returns one `{name_a, name_b, inter, size_a, size_b, dice, iou, centroid_distance_mm}` per
  overlapping segment pair; raises `SlicerHelperError` if the bundle is absent.

**`SlicerService.execute` signature**
- From: `execute(script, context=None)` — helper always prepended; correspondence
  unavailable in Slicer scripts.
- To: `execute(script, context=None, *, include_correspondence=False)` — when `True`, the
  correspondence bundle is prepended (with exactly one top-level
  `from __future__ import annotations`).
- Reason: make the overlap engine available in Slicer scripts without bloating every
  payload.
- Impact: non-breaking — the default preserves current behavior.

## Capabilities

### New Capabilities
- `slicer-overlap-detection`: opt-in bundling of the image correspondence engine into the
  Slicer runtime, plus a non-destructive segment-overlap detector helper method.

### Modified Capabilities
<!-- None — openspec/specs/ is empty; no existing spec requirements change. -->

## Impact

- **Code:** `clarinet/services/slicer/correspondence_bundle.py` (new);
  `clarinet/services/slicer/service.py` (`execute` opt-in);
  `clarinet/services/slicer/helper.py` (`detect_overlaps`); tests under `tests/`.
- **APIs:** `SlicerService.execute` gains a keyword-only parameter (non-breaking); one new
  public helper method.
- **Dependencies:** none added — correspondence is numpy/stdlib; numpy is bundled in Slicer.
- **Reuses (unchanged):** `clarinet/services/image/correspondence/` (read via
  `inspect.getsource`); the `_export_segments_labelmap` VTK→numpy bridge in `helper.py`.
- **Deferred / related:** #461 (decouple image service in place), #462 (reimplement
  `subtract_segmentations`/merge/binarize on the shared engine).
