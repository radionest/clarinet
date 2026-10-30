## ADDED Requirements

### Requirement: Single comparison engine for segment subtraction

`SlicerHelper.subtract_segmentations` SHALL derive its removal decision from the same
`correspond()` entry point and `KeepPlan` consumption used by `Segmentation.difference`.
Hand-rolled voxel-overlap counting SHALL NOT remain in the helper.

#### Scenario: Equivalent inputs yield equivalent decisions
- **WHEN** both call sites are given the same label arrays, spacing, and engine parameters
- **THEN** the set of labels selected for removal SHALL be identical

#### Scenario: Divergence is confined to the apply step
- **WHEN** a `KeepPlan` has been computed
- **THEN** the Slicer path SHALL apply it by removing segment objects
- **AND** the image-service path SHALL apply it by painting voxels via `render()`
- **AND** no other step SHALL differ between the two runtimes

#### Scenario: No hand-rolled fallback
- **WHEN** `subtract_segmentations` is called in a namespace where the correspondence
  bundle is absent
- **THEN** it SHALL raise `SlicerHelperError` naming the missing bundle
- **AND** it SHALL NOT fall back to computing the overlap by another method
- **AND** it SHALL raise even when either source is empty — failure depends on script
  setup, not on operand content

---

### Requirement: One engine parameter set across both runtimes

`SlicerHelper.subtract_segmentations` and `Segmentation.difference` SHALL accept the
identical engine parameter set: `strategy`, `max_overlap`, `max_overlap_ratio`, `resample`,
and the granularity selector. Parameters that exist only to manage Slicer nodes, such as
`output_name`, SHALL NOT be part of that set.

#### Scenario: Strategy override honored inside Slicer
- **WHEN** a Slicer script calls `subtract_segmentations` with an explicit `strategy`
  constructed from bundle symbols
- **THEN** the removal decision SHALL be computed with that strategy
- **AND** the result SHALL match `Segmentation.difference` given the same strategy and
  equivalent arrays

#### Scenario: Parameter parity is enforced automatically
- **WHEN** the engine parameter set of either call site is changed without the other
- **THEN** a test SHALL fail identifying the mismatched parameter names

---

### Requirement: Shared derivation from scalar thresholds to a matching strategy

The derivation from `max_overlap` / `max_overlap_ratio` to a `MatchingStrategy` SHALL live
in `clarinet/services/image/correspondence/` and SHALL be the single implementation used by
both call sites. It SHALL be present in the generated Slicer bundle.

#### Scenario: Bundle exposes the derivation
- **WHEN** the correspondence bundle is `exec`'d in a namespace whose only third-party
  import is numpy
- **THEN** the derivation helper SHALL be defined and callable

#### Scenario: Identical scalars produce identical strategies
- **WHEN** both call sites are given the same `max_overlap` and `max_overlap_ratio`
- **THEN** they SHALL obtain matching strategies, including the same measure and the same
  `min_score`

#### Scenario: Ratio takes precedence over the absolute threshold
- **WHEN** both `max_overlap` and `max_overlap_ratio` are provided
- **THEN** the derived strategy SHALL use the coverage measure with
  `min_score = max_overlap_ratio` and SHALL ignore `max_overlap`, on both call sites alike
- **AND** the legacy Slicer rule requiring both thresholds to be exceeded SHALL NOT be
  preserved

#### Scenario: Explicit strategy takes precedence
- **WHEN** a caller supplies `strategy` together with scalar thresholds
- **THEN** the supplied strategy SHALL be used and the scalars SHALL be ignored on both
  call sites alike

---

### Requirement: Overlap granularity is explicit with a per-component default

How the subtracted segmentation enters the overlap graph SHALL be an explicit parameter
offering per-component and flattened-union behavior. The default SHALL be per-component,
matching `Segmentation.difference`.

#### Scenario: Default matches the image-service engine
- **WHEN** `subtract_segmentations` is called without specifying granularity
- **THEN** each subtracted segment SHALL enter the graph as its own label
- **AND** a base segment SHALL be removed when its highest single-pair score meets the
  threshold

#### Scenario: Union granularity restores the legacy granularity only
- **WHEN** flattened-union granularity is selected
- **THEN** all subtracted segments SHALL enter the graph as one label
- **AND** each base segment SHALL be scored against their combined extent
- **AND** the threshold boundary and scalar precedence SHALL still follow the shared
  derivation — exact-ratio-threshold and both-scalars-set verdicts differ from the legacy
  rule even under union granularity

#### Scenario: Fragmented sub-threshold overlap distinguishes the two values
- **WHEN** a base segment is overlapped by several disconnected subtracted components,
  each individually below the threshold but jointly above it
- **THEN** the per-component default SHALL keep the base segment
- **AND** flattened-union granularity SHALL remove it

---

### Requirement: Grid handling at the engine boundary

On the default path both runtimes SHALL refuse mismatched grids, so the engine only ever
receives operands that were not resampled. Opt-in resampling SHALL remain runtime-native
and is a documented divergence, not a silent one.

#### Scenario: Default path refuses mismatched grids on both sides
- **WHEN** the operands' grids differ and resampling was not requested
- **THEN** the Slicer path SHALL raise `SlicerHelperError` at export
- **AND** the image-service path SHALL raise `GeometryMismatchError`
- **AND** no resampling SHALL occur in either runtime

#### Scenario: Matched grids feed the engine identical operands
- **WHEN** the operands share a grid
- **THEN** the label arrays SHALL enter `correspond()` without any resampling step, so
  equal scenes yield equal arrays and, per the equivalent-decisions scenario, equal
  verdicts

#### Scenario: Opt-in resampling is runtime-native and documented
- **WHEN** `resample=True` re-grids a mismatched input
- **THEN** each runtime SHALL use its native resampling — labelmap re-export in Slicer,
  nearest-neighbour `reindex_to` server-side
- **AND** verdicts near the decision threshold MAY differ between runtimes on this path
- **AND** the helper documentation SHALL name this divergence

---

### Requirement: Empty sources stay tolerated

The engine-backed helper SHALL preserve the empty-vs-foreign-grid guard semantics: a
genuinely empty source is a warned no-op, while a foreign-grid source still raises at
export, before the engine runs.

#### Scenario: Empty base segmentation is a warned no-op
- **WHEN** the base segmentation exports no voxels
- **THEN** no segments SHALL be removed and no error SHALL be raised
- **AND** a warning SHALL be logged

#### Scenario: Empty subtracted segmentation removes nothing
- **WHEN** the subtracted segmentation exports no voxels
- **THEN** the base segmentation SHALL be returned unchanged
