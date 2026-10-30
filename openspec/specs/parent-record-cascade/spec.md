# parent-record-cascade Specification

## Purpose
TBD - created by archiving change unique-within-constraints. Update Purpose after archive.
## Requirements
### Requirement: Deleting a parent record cascades to its children

The `record.parent_record_id` foreign key SHALL use `ON DELETE CASCADE`. Deleting a record
SHALL delete every record that references it as a parent, transitively, so that no record
is ever left with a dangling or null-ed `parent_record_id` that once pointed at a deleted
parent. This SHALL make `parent_record_id` a stable discriminator for output-path
resolution.

#### Scenario: Child is removed when its parent is deleted
- **WHEN** a parent record with one or more child records is deleted at the database level
- **THEN** the child records SHALL be deleted as well, leaving no child with a stale
  `parent_record_id`

#### Scenario: Application cascade delete is unchanged
- **WHEN** a record subtree is deleted through the record service's cascade delete path
- **THEN** the observable result SHALL be identical to the prior `ON DELETE SET NULL`
  behavior, because that path already collects and deletes the whole subtree

### Requirement: Bypass deletions are documented as audit-lossy

Because `RecordEvent` audit rows are written by the application layer, a deletion that
bypasses the record service (raw SQL, database restore) and relies on the FK cascade SHALL
remove child records without emitting audit events. This behavior SHALL be documented in
the changelog as an accepted trade of recoverable-but-orphaned for coherent-but-removed.

#### Scenario: Changelog documents the audit gap
- **WHEN** the change is released
- **THEN** the changelog SHALL state that FK-level cascade deletions do not produce
  `RecordEvent` rows, SHALL explain why the trade is accepted, and SHALL state that
  foreign-key enforcement is newly active on SQLite connections

### Requirement: SQLite connections enforce foreign keys

Every SQLite connection SHALL enable `PRAGMA foreign_keys=ON` via the engine's connect
hook, so that declared FK actions — the parent cascade and the existing `RecordFileLink`
cascade — behave identically on SQLite and PostgreSQL. At startup against a SQLite
database the system SHALL run `PRAGMA foreign_key_check` and log each reported violation
as a warning, without aborting startup (legacy databases may carry dangling rows from the
era without enforcement).

#### Scenario: DB-level cascade works on SQLite
- **WHEN** a parent record is deleted with a plain SQL `DELETE` on a SQLite database
  through the application engine
- **THEN** its child records SHALL be deleted by the FK cascade, matching PostgreSQL
  behavior

#### Scenario: Legacy dangling rows warn but do not block startup
- **WHEN** the application starts against a SQLite database containing rows that violate
  a foreign key (created before enforcement)
- **THEN** startup SHALL complete and each violation SHALL be logged as a warning

