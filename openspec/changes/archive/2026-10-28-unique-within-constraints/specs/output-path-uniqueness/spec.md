## ADDED Requirements

### Requirement: `{parent_id}` file-pattern placeholder

The file-pattern field builder SHALL expose a `{parent_id}` placeholder resolving to the
record's `parent_record_id` FK column value. Resolution SHALL NOT require loading the
parent record and SHALL be identical across every subsystem that renders the pattern
(pipeline context, record service, slicer context). For a record whose `parent_record_id`
is `None`, the placeholder SHALL render as an empty string under lenient rendering
(strict rendering raises, unchanged) — the validator never relies on it in that case,
because the parent discriminator is only demanded when `parent_required=True`.

#### Scenario: Placeholder renders the FK value without a parent fetch
- **WHEN** a record with `parent_record_id=4711` resolves a pattern
  `second_review_{parent_id}_{user_id}.seg.nrrd`
- **THEN** the rendered filename SHALL contain `4711` and SHALL NOT depend on whether the
  parent record was loaded

#### Scenario: `{origin_type}` remains available
- **WHEN** a pattern uses `{origin_type}`
- **THEN** it SHALL continue to resolve to the parent's record-type name (falling back to
  the record's own type name when no parent is available)

### Requirement: Output-path uniqueness validation at config load

At config load the system SHALL reject a `RecordType` whose singular OUTPUT file pattern
cannot distinguish two records that the cardinality constraints permit to coexist.
Rejection SHALL fail fast and SHALL name the offending RecordType and FileDef. A pattern
containing `{id}` SHALL always satisfy validation, because `record.id` is globally unique.

Otherwise, for each singular OUTPUT FileDef the output path (the directory implied by
`FileDef.level` together with the pattern's placeholders) MUST carry:
- a discriminator for every partition in `unique_by` — `"user"` satisfied by `{user_id}`;
  `"parent"` satisfied by `{parent_id}` and required only when `parent_required=True` —
  `{origin_type}` SHALL count as a parent discriminator only for a FileDef carrying the
  explicit opt-out, because it distinguishes parent *types*, not two same-type parents;
- the record's own level UID (`{series_uid}` or `{study_uid}`) whenever `FileDef.level` is
  coarser than the record's level, because records in distinct level contexts coexist and
  would otherwise share one directory under one name — this requirement is independent of
  `unique_by`, since level context is always part of the uniqueness scope;
- `{id}` when `unique_by=None` and `max_records` permits more than one record per level
  context, because nothing else distinguishes two such records.

The static guarantee covers only what the config proves: a `parent_required=False` type
whose records nevertheless carry parents MAY still collide (the validator SHALL NOT demand
a parent discriminator for such types — a null partition cannot distinguish records — and
this limitation SHALL be documented).

#### Scenario: Parent-scoped output without a parent discriminator is rejected
- **WHEN** a `parent_required=True` type has `unique_by={"parent"}` and a singular OUTPUT
  pattern lacking `{parent_id}` and `{id}` (and not opted out)
- **THEN** config load SHALL fail with an error naming the RecordType and FileDef

#### Scenario: Adding the discriminator passes validation
- **WHEN** the same type's OUTPUT pattern includes `{parent_id}`
- **THEN** config load SHALL succeed

#### Scenario: `{origin_type}` alone does not satisfy the parent partition
- **WHEN** a `parent_required=True` type has `unique_by={"parent"}` and a singular OUTPUT
  pattern whose only parent-related placeholder is `{origin_type}`, without the opt-out
- **THEN** config load SHALL fail, because two same-type parents would render one filename

#### Scenario: No uniqueness with a multi-record quota requires `{id}`
- **WHEN** a type has `unique_by=None`, `max_records>1`, and a singular OUTPUT pattern
  without `{id}`
- **THEN** config load SHALL fail

#### Scenario: Coarser FileDef level requires an explicit UID
- **WHEN** a SERIES-level type owns a PATIENT-level singular OUTPUT FileDef whose pattern
  omits `{series_uid}` and `{id}`
- **THEN** config load SHALL fail because two series' records would resolve into one
  patient directory under one name

### Requirement: Collection files are exempt

A FileDef with `multiple=True` SHALL be exempt from output-path uniqueness validation,
because its pattern is globbed with placeholders replaced by wildcards and therefore
denotes a directory filter rather than a single-record identity.

#### Scenario: Collection output is not flagged
- **WHEN** a type with `unique_by={"parent"}` owns a `multiple=True` OUTPUT FileDef whose
  pattern lacks `{parent_id}`
- **THEN** config load SHALL succeed

### Requirement: Explicit per-file opt-out for author-known discriminators

A file binding (`FileRef` in config, `RecordTypeFileLink` in the DB, surfaced on
`FileDefinitionRead`) SHALL carry an `allow_path_collision` marker that exempts **that
FileDef alone** from output-path uniqueness validation, for a pattern whose uniqueness the
framework cannot statically prove (an author-known-unique `{data.FIELD}` placeholder, or
`{origin_type}` when parents of the type are guaranteed to differ in record type). The
opt-out SHALL be deliberate and discoverable at the file binding it excuses; sibling
FileDefs of the same RecordType SHALL remain validated.

#### Scenario: Opt-out suppresses the error for the marked file
- **WHEN** a type's OUTPUT file relying on `{data.timepoint}` for uniqueness carries
  `allow_path_collision=True` on its binding
- **THEN** config load SHALL succeed without a path-uniqueness error for that FileDef

#### Scenario: Sibling files stay validated
- **WHEN** the same type owns a second singular OUTPUT FileDef without the marker whose
  pattern lacks the required discriminators
- **THEN** config load SHALL still fail naming that second FileDef
