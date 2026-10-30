## ADDED Requirements

### Requirement: Uniqueness by relationship partitions

A `RecordType` SHALL declare `unique_by`, either a set of relationship partitions drawn
from `{"user", "parent"}` or `None`. When `unique_by` is a set, the system SHALL permit at
most one record of that type per distinct combination of the selected partitions' values,
evaluated **within the record's own DICOM level context** (patient / study / series,
selected by `RecordType.level`). When `unique_by` is `None`, the system SHALL impose no
partition uniqueness; cardinality is then governed by `max_records` alone.

The `"user"` partition binds `user_id`; the `"parent"` partition binds `parent_record_id`.
The level context is always part of the uniqueness scope and is never itself a partition.

#### Scenario: Second record with an identical partition-tuple is rejected
- **WHEN** a record of a type with `unique_by={"parent"}` is created and another record of
  that type with the same `parent_record_id` already exists in the same level context
- **THEN** creation SHALL fail with HTTP 409 and code `UNIQUE_PER_USER`

#### Scenario: Differing on any selected partition is permitted
- **WHEN** a type has `unique_by={"user","parent"}` and a new record differs from every
  existing record (in the same level context) on at least one selected partition
- **THEN** creation SHALL succeed

#### Scenario: None imposes no uniqueness
- **WHEN** a type has `unique_by=None` and `max_records` permits another record
- **THEN** creation SHALL succeed regardless of any existing record's user or parent

### Requirement: Partition set is a non-empty set or None

`unique_by` SHALL be treated as a set: order and duplicates SHALL NOT affect meaning. The
system SHALL canonicalize the value (sorted, de-duplicated) before persistence and
comparison, and SHALL reject any token outside `{"user", "parent"}`. An empty set SHALL be
rejected at config load with a message directing the author to `None` (no uniqueness) or
`max_records=1` (one per level context). "One per level" SHALL NOT be spelled as an empty
`unique_by`. The boolean `False` SHALL be accepted as equivalent to `None` — the TOML
spelling of "off", since TOML cannot express null — and `True` SHALL be rejected with a
teaching message naming `{"user"}` (the legacy behavior) and omission (the default).

#### Scenario: Reordered declarations are equivalent
- **WHEN** two RecordType configs declare `{"user","parent"}` and `{"parent","user"}`
- **THEN** the system SHALL treat them as identical and the config reconciler SHALL report
  no change between them

#### Scenario: Unknown partition is rejected
- **WHEN** a config declares `unique_by={"series"}`
- **THEN** config load SHALL fail with a validation error naming the invalid token

#### Scenario: Empty set is rejected with a teaching message
- **WHEN** a config declares `unique_by` as an empty set
- **THEN** config load SHALL fail with a message naming both `None` (no uniqueness) and
  `max_records=1` (one per level) as the intended alternatives

#### Scenario: False is the TOML spelling of off
- **WHEN** a TOML document declares `unique_by = false`
- **THEN** the effective `unique_by` SHALL be `None` and a round-trip through the TOML
  exporter SHALL preserve it (the exporter writes `false` for `None`)

#### Scenario: True is rejected with a teaching message
- **WHEN** a config declares `unique_by = true`
- **THEN** config load SHALL fail with a message naming `{"user"}` (the legacy
  `unique_per_user=True` behavior) and omission (the default) as the intended alternatives

### Requirement: Default partitions and parentless collapse

When `unique_by` is unspecified, the system SHALL default it to `{"user", "parent"}`. For a
record whose `parent_record_id` is `None`, the `"parent"` partition SHALL contribute a null
value, so the default SHALL behave identically to per-`user` uniqueness within the level
context. The `"parent"` partition SHALL NOT require `parent_required=True`.

#### Scenario: Default reproduces per-user uniqueness for parentless types
- **WHEN** a parentless type uses the default `unique_by` and a user already has a record
  of that type in the same level context
- **THEN** a second record for that same user in that context SHALL be rejected with 409

#### Scenario: Default admits distinct parents for parented types
- **WHEN** a `parent_required=True` type uses the default `unique_by` and two records share
  `user_id` and level context but have different `parent_record_id`
- **THEN** both SHALL be permitted

### Requirement: `max_records` is an orthogonal per-level quota

`max_records` SHALL remain a quota counted per level context, independent of `unique_by`.
Both constraints SHALL be enforced simultaneously; a creation SHALL be rejected if either
is violated. One-record-per-level cardinality SHALL be expressed as `max_records=1` with
`unique_by=None`, not through `unique_by`.

#### Scenario: Quota and uniqueness enforced together
- **WHEN** a STUDY-level type has `max_records=2` and `unique_by={"user"}`, and two records
  by two different users already exist for a study
- **THEN** a third record by a new user SHALL be rejected with 409 `RECORD_LIMIT_REACHED`

#### Scenario: Uniqueness rejects a duplicate user below the quota
- **WHEN** the same type has one record for user A and the quota is not yet reached
- **THEN** a second record for user A SHALL be rejected with 409 `UNIQUE_PER_USER`

### Requirement: `unique_per_user` deprecation and behavior-preserving migration

The `unique_per_user` boolean SHALL be removed as the canonical field and accepted as a
deprecated compatibility input on **every** RecordType construction path — the config
primitive (`RecordDef`), TOML documents, and API create/update bodies — translated before
validation (a `mode="before"` validator on `RecordTypeCreate` and `RecordTypeOptional`).
When supplied it SHALL be translated and SHALL emit a deprecation warning: `True` SHALL
map to `{"user"}` and `False` SHALL map to `None`. It SHALL NOT be silently ignored on any
path. Existing persisted rows SHALL migrate by the same mapping and SHALL NOT adopt the
parent-inclusive default. An explicitly declared `unique_by` SHALL take precedence over
the legacy flag.

#### Scenario: Legacy True maps to per-user uniqueness
- **WHEN** a config declares `unique_per_user=True` and does not declare `unique_by`
- **THEN** the effective `unique_by` SHALL be `{"user"}` and a deprecation warning SHALL be
  emitted

#### Scenario: Legacy False maps to no uniqueness
- **WHEN** a config declares `unique_per_user=False` and does not declare `unique_by`
- **THEN** the effective `unique_by` SHALL be `None`

#### Scenario: Explicit `unique_by` overrides the legacy flag
- **WHEN** a config declares both `unique_by={"parent"}` and `unique_per_user=True`
- **THEN** the effective `unique_by` SHALL be `{"parent"}`

#### Scenario: Legacy TOML documents keep working
- **WHEN** a TOML-mode project loads a `.toml` RecordType carrying `unique_per_user = false`
  (as written by the previous exporter) and no `unique_by`
- **THEN** the effective `unique_by` SHALL be `None`, a deprecation warning SHALL be
  emitted, and the type SHALL NOT silently adopt the parent-inclusive default

### Requirement: Creation-time uniqueness runs only on a fully bound partition tuple

The uniqueness check SHALL evaluate only when every selected partition is bound: the
`"parent"` partition is always bound (`None` is a value), while the `"user"` partition is
bound only when the candidate `user_id` is set. When `"user"` is selected and the record
is created without a user (unassigned/system records forming a claiming pool), the
creation-time check SHALL be skipped entirely — pools of unassigned records are permitted
— and the invariant among assigned records SHALL be enforced at assignment time (see the
assignment requirement). Types selecting only `"parent"` SHALL enforce at creation
regardless of assignment, since their tuple is always bound.

#### Scenario: Unassigned records of a user-partitioned type pool up
- **WHEN** a type has `unique_by={"user"}` (or the default) and two records are created
  without a user in the same level context
- **THEN** both creations SHALL succeed

#### Scenario: Parent-only types dedupe unassigned children
- **WHEN** a `parent_required=True` type has `unique_by={"parent"}` and two records are
  created without a user for the same `parent_record_id` in the same level context
- **THEN** the second creation SHALL fail with 409 `UNIQUE_PER_USER`

#### Scenario: An unassigned record does not block an assigned creation
- **WHEN** a type with `unique_by={"user","parent"}` has an unassigned record with
  `parent_record_id=P` and user U creates a record with `parent_record_id=P` in the same
  level context
- **THEN** the creation SHALL succeed (the unassigned record does not match on the user
  axis)

### Requirement: Uniqueness is enforced at user assignment

Assigning a user to an existing record — claim, explicit assignment, or
`inherit_user_from_parent` — SHALL be rejected with 409 `UNIQUE_PER_USER` if another
record of the same type in the same level context matches the candidate on every selected
partition, with `"user"` bound to the assignee and `"parent"` bound to the record's own
`parent_record_id` (compared NULL-safely). Claimable-pool queries (counts and record
listings offered for claiming) SHALL apply the same predicate, so a listed record is
always claimable by the requesting user.

#### Scenario: Claim rejected when the user holds a same-partition record
- **WHEN** a type has `unique_by={"user","parent"}`, user U already has a record with
  `parent_record_id=P` in a level context, and U claims an unassigned record with
  `parent_record_id=P` in that context
- **THEN** the claim SHALL fail with 409 `UNIQUE_PER_USER`

#### Scenario: Claim allowed when a selected partition differs
- **WHEN** the same user U instead claims an unassigned record with `parent_record_id=Q`
  in that context
- **THEN** the claim SHALL succeed

#### Scenario: Claimable listings agree with the claim outcome
- **WHEN** user U (holding a record with parent P) requests the claimable pool containing
  an unassigned record with parent P and another with parent Q
- **THEN** only the parent-Q record SHALL be offered to U, while a user with no records of
  the type SHALL be offered both

### Requirement: `shared_editing` requires a user-free partition set

`shared_editing=True` SHALL be rejected at validation when `"user"` is in the effective
`unique_by` (ownership churn contradicts per-user uniqueness — the successor of the
`shared_editing` ⟹ `unique_per_user=False` rule).

#### Scenario: Shared editing conflicts with the user partition
- **WHEN** a config declares `shared_editing=True` with the default `unique_by`
- **THEN** validation SHALL fail naming both fields

#### Scenario: Shared editing works with a parent-only partition
- **WHEN** a config declares `shared_editing=True` and `unique_by={"parent"}`
- **THEN** validation SHALL succeed
