## REMOVED Requirements

### Requirement: Opt-in correspondence bundle in Slicer scripts

**Reason**: The engine became part of the record-driven script paths: the record-open path and
the result-validator path now compose their payloads with the correspondence bundle included,
so the old contract — "the bundle is a service-layer parameter only; no route requests it;
engine-backed helpers are therefore unavailable on record-driven script paths" — no longer
describes the system.

**Migration**: The replacement requirement below keeps the ad-hoc opt-in contract (excluded by
default, available on explicit request) and adds the record-path supply guarantee.

## ADDED Requirements

### Requirement: Correspondence bundle availability across script paths

The system SHALL provide a mechanism to include the image correspondence engine in a Slicer
script payload. Script-execution paths that run record-configured scripts SHALL make the engine
available, so engine-backed helper methods are usable outside tests. Ad-hoc script execution
SHALL continue to exclude the engine unless it is explicitly requested.

#### Scenario: Ad-hoc execution excludes the bundle by default
- **WHEN** an ad-hoc script is executed without requesting the correspondence bundle
- **THEN** the composed payload SHALL NOT contain correspondence source

#### Scenario: Ad-hoc execution can request the bundle
- **WHEN** an ad-hoc script is executed with the correspondence bundle requested
- **THEN** the composed payload SHALL make the correspondence engine functions
  (`build_overlap_graph`, measures, matching strategies, operations) available in the script
  namespace

#### Scenario: Record open path supplies the engine
- **WHEN** a record type's Slicer script is executed through the record-open path
- **THEN** the correspondence engine SHALL be present in the script namespace
- **AND** an engine-backed helper method SHALL NOT raise for a missing bundle

#### Scenario: Result validator path supplies the engine
- **WHEN** a record type's result-validator script is executed
- **THEN** the correspondence engine SHALL be present in the script namespace
