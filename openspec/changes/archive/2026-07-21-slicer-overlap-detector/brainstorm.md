<!-- Raw capture of superpowers:brainstorming output (decision log). -->

# Brainstorm — Slicer correspondence bundle + overlap detector

## Background / trigger

Prior investigation established that the image service's overlap-detection engine
(`clarinet/services/image/correspondence/`) is **runtime-compatible with 3D Slicer**:
its only third-party dependency is numpy (graph.py, operations.py); measures/matching/
model are pure stdlib. numpy + scipy are bundled in Slicer (helper.py already imports
them unguarded). The engine already runs, hand-rolled, inside Slicer today —
`SlicerHelper.subtract_segmentations` computes `overlap = np.sum(mask_a & (arr_b > 0))`
with `max_overlap`/`max_overlap_ratio`, the same params `Segmentation.difference` maps
onto `ThresholdMatch(AbsoluteOverlap()/Coverage("a"))`.

User idea: "an optional way to create a Slicer helper with the correspondence module.
Maybe the better architectural move is to separate the Image service from clarinet into
a new library."

## Exploration findings (fact-checked)

- `correspondence/` is already a pure library — numpy + stdlib; cross-imports never leave
  its own subpackage; **zero coupling to clarinet**.
- The whole image service is **two imports** from standalone: it reaches the rest of the
  framework only via `clarinet.exceptions.domain` and `clarinet.utils.logger`. No models,
  DB, settings, or repositories.
- Inbound coupling is tiny: within the framework only
  `pipeline/tasks/convert_series.py` truly imports the image service (the helper.py hit is
  the string `clarinet.services.image.conform_seg_to_grid` in an error message). Downstream
  `clarinet_nir_liver` has ~6 call sites.
- Logger sever is safe: `clarinet/utils/logger.py:308` installs an `InterceptHandler`
  routing stdlib logging into loguru (precedent: `PacsHelper` uses
  `logging.getLogger("clarinet.slicer.pacs")`).
- Exceptions: `ImageError(ClarinetError)` → ImageReadError/ImageWriteError/
  GeometryMismatchError. **No** exception handler is keyed on `ClarinetError`/`ImageError`
  (all ~34 handlers in `exception_handlers.py` target specific classes + an `Exception`
  catch-all), so dropping the base is behavior-safe.
- Blocker for a clean exception re-home: `clarinet/services/image/__init__.py` **eagerly**
  imports skimage/pynrrd/nibabel/SimpleITK, so `clarinet.exceptions.domain` re-exporting
  from `clarinet.services.image.exceptions` would poison the foundational exceptions module
  (and risk cycles). Clean re-home needs a lazy `__init__` first → belongs with extraction.
- Slicer-injectable boundary: only `correspondence/` (numpy/stdlib). `segmentation.py`/
  `layered_segmentation.py` pull skimage/pynrrd → not text-injectable into stock Slicer.

## Decision chain

- **Q1 — extract to a separate library, or decouple in place?**
  → **Decouple in place.** A separate distribution buys only independent versioning and
  non-clarinet consumers — neither needed now — at the cost of packaging, cross-repo version
  pinning, and migrating convert_series.py + nir_liver (~6 sites). In-place decoupling
  captures ~all the value (library-grade, portable), is reversible, and is a strict
  prerequisite for extraction (never wasted). YAGNI: defer physical extraction.

- **Q2 — how far does the Slicer side go?**
  → **Bundle correspondence + one overlap-detector method.** Opt-in mechanism to include
  correspondence in the Slicer script, plus one thin `SlicerHelper` method (non-destructive
  overlap detector via `build_overlap_graph`) as first consumer, reusing the existing
  `_export_segments_labelmap` VTK→numpy bridge. Replacing the hand-rolled
  `subtract_segmentations` with the engine (DRY) is valuable but touches guard-heavy
  battle-tested code → deferred to an issue.

- **Q3 — sequencing?**
  → **Ship the feature (A) first; decoupling (B) becomes a tracked issue.** The feature does
  NOT depend on the decoupling (correspondence is already pure; the detector uses helper.py's
  own VTK bridge, not Image/Segmentation). So the two are separable PRs.

## Outcomes

- **This change (Workstream A):** correspondence bundler + `SlicerService` opt-in +
  `SlicerHelper.detect_overlaps` + tests.
- **Deferred, tracked:** #461 decouple image service in place; #462 reimplement
  subtract_segmentations/merge/binarize on the shared engine.

## Design trade-offs carried into design.md

- **Single source of truth for the bundle** — `inspect.getsource()` the real correspondence
  modules and flatten at runtime, vs. a maintained flattened copy (golden-copy sync trap).
  Chose runtime flatten: the injected text IS the real code.
- **`from __future__ import annotations`** must appear exactly once at the top of the
  composed Slicer script (Slicer runs Python 3.9; `X | None` param annotations would be
  evaluated at def-time otherwise). Strip per-module ones; rely on one top-level import.
- **Granularity** — segment-level (each segment = one label = one graph component), matching
  existing helper semantics; connected-component-level is a documented future option.
- **Axis order** — exported labelmap arrays are (z,y,x); reverse `volume.GetSpacing()` so
  `centroid_distance_mm` is correct (Dice/IoU are count-based, axis-agnostic).
