## ADDED Requirements

### Requirement: Opt-in correspondence bundle in Slicer scripts

The system SHALL provide an opt-in mechanism to include the image correspondence engine in
a Slicer script payload, and SHALL exclude it by default so existing script behavior is
unchanged.

#### Scenario: Bundle excluded by default
- **WHEN** a script is executed without requesting the correspondence bundle
- **THEN** the composed payload SHALL NOT contain correspondence source, preserving current
  behavior

#### Scenario: Bundle included on request
- **WHEN** a script is executed with `include_correspondence=True`
- **THEN** the composed payload SHALL make the correspondence engine functions
  (`build_overlap_graph`, measures, matching strategies, operations) available in the script
  namespace

#### Scenario: Opt-in is a service-layer parameter only
- **WHEN** a script reaches Slicer through an HTTP route rather than a direct
  `SlicerService.execute` call
- **THEN** the bundle SHALL NOT be included, because no route requests it
- **AND** engine-backed helper methods SHALL therefore be unavailable on record-driven
  script paths

---

### Requirement: Correspondence bundle is Slicer-runtime compatible

The generated bundle text SHALL execute in a Python environment that provides only numpy
plus the standard library (the Slicer runtime), with no dependency on the `clarinet`
package.

#### Scenario: Bundle carries no framework imports
- **WHEN** the correspondence bundle text is generated
- **THEN** it SHALL contain no `from clarinet` import line
- **AND** it SHALL contain at most one top-level `from __future__ import annotations`

#### Scenario: Bundle executes under numpy-only
- **WHEN** the bundle text is `exec`'d in a namespace whose only third-party import is numpy
- **THEN** `build_overlap_graph` SHALL be defined
- **AND** it SHALL return the correct overlapping-pair edges for a known labelmap pair

---

### Requirement: Bundle derives from live source

The bundle SHALL be derived from the live `clarinet/services/image/correspondence/` modules
at generation time, not from a separately maintained flattened copy.

#### Scenario: Bundle reflects source changes
- **WHEN** a correspondence module's source changes
- **THEN** the generated bundle SHALL reflect that change without any edit to a separate
  bundle file

---

### Requirement: Non-destructive segment-overlap detector

`SlicerHelper` SHALL provide a `detect_overlaps` method that reports voxel overlap between
the segments of two segmentations without modifying either input's segment data. Applying
the source volume's reference geometry to an input, which the shared labelmap export
performs, is not a modification of segment data.

#### Scenario: Overlapping segments reported
- **WHEN** `detect_overlaps` is called on two segmentations that share voxels
- **THEN** it SHALL return one entry per overlapping (segment_a, segment_b) pair carrying
  `name_a`, `name_b`, `inter`, `size_a`, `size_b`, `dice`, `iou`, `centroid_distance_mm`
- **AND** neither input segmentation's segment data SHALL be modified

#### Scenario: Disjoint segments
- **WHEN** `detect_overlaps` is called on two segmentations with no shared voxels
- **THEN** it SHALL return an empty list

#### Scenario: Bundle not included
- **WHEN** `detect_overlaps` is called from a script that did not include the correspondence
  bundle, in a Slicer session where no earlier script included it either
- **THEN** it SHALL raise `SlicerHelperError` indicating `include_correspondence=True` is
  required

#### Scenario: Bundle persists across a Slicer session
- **WHEN** `detect_overlaps` is called from a script that did not include the bundle, but an
  earlier script in the same Slicer session did
- **THEN** it SHALL execute normally rather than raise, because Slicer reuses one exec
  namespace and the bundle's symbols are never removed from it

---

### Requirement: Detector inherits reference-grid guards

`detect_overlaps` SHALL reuse the existing labelmap export path so a foreign or flipped-grid
segmentation fails fast rather than silently reporting zero overlap, while a genuinely-empty
segmentation is tolerated.

#### Scenario: Foreign-grid input fails fast
- **WHEN** `detect_overlaps` is called with a segmentation whose voxels sit on a grid that
  does not overlap the reference volume extent
- **THEN** it SHALL raise `SlicerHelperError` rather than return zero-overlap results

#### Scenario: Empty input tolerated
- **WHEN** `detect_overlaps` is called with a genuinely-empty segmentation (no voxels on any
  grid)
- **THEN** it SHALL return an empty list without raising
