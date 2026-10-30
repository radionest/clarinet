# Spec Delta: agent-doc-delivery

## ADDED Requirements

### Requirement: The payload is split into managed documents and seed documents

The agent documentation payload SHALL be partitioned into two sets with distinct
ownership. **Managed** documents are framework-owned: they are written into the
project's managed rules directory, carry a managed header, and are rewritten on
every update. **Seed** documents are project-owned: they are written once to
their target location, carry no managed header, and are never rewritten.

#### Scenario: Managed documents are installed with a header

- **WHEN** agent docs are installed into a project
- **THEN** each managed document is present in the managed rules directory and begins with the managed header naming the clarinet version

#### Scenario: Seed document is installed without a header

- **WHEN** agent docs are installed into a project
- **THEN** the project overview is present at the project's `.claude/CLAUDE.md` and carries no managed header

#### Scenario: Seed document is not installed as a managed rule

- **WHEN** agent docs are installed into a project
- **THEN** no `overview.md` is present in the managed rules directory

---

### Requirement: Seed documents are written only when absent

A seed document SHALL be written only if its target file does not already exist.
An existing target MUST be left byte-for-byte unchanged, and the installer MUST
report that it was preserved.

#### Scenario: Existing project overview is preserved on install

- **WHEN** agent docs are installed into a project whose `.claude/CLAUDE.md` already exists with user-authored content
- **THEN** that file's content is unchanged and the installer reports it as preserved

#### Scenario: Seed survives an update

- **WHEN** the update operation is run against a project whose `.claude/CLAUDE.md` has been edited by the user
- **THEN** the file's content is unchanged

---

### Requirement: Update prunes managed documents no longer in the payload

The update operation SHALL remove files from the managed rules directory that
carry the managed header but are no longer part of the framework payload, so a
project's managed directory reflects the installed framework version.

#### Scenario: Dropped managed document is removed

- **WHEN** a project's managed rules directory contains a managed document that the current payload no longer provides, and the update operation is run
- **THEN** that file is removed and the removal is reported

#### Scenario: Project-owned files in the managed directory are not removed

- **WHEN** the managed rules directory contains a file that does not carry the managed header, and the update operation is run
- **THEN** that file is left in place

---

### Requirement: A previously managed overview is migrated, not deleted

When the update operation would prune an `overview.md` from the managed rules
directory, it SHALL move that file to the project's `.claude/CLAUDE.md` if no
file exists at that path, rather than deleting it. The migrated file MUST have
the managed header stripped. If a project overview already exists at the target
path, the managed copy MAY be removed.

#### Scenario: Overview migrated for a project upgrading from an earlier version

- **WHEN** the update operation runs against a project that has a managed `overview.md` and no `.claude/CLAUDE.md`
- **THEN** `.claude/CLAUDE.md` exists carrying the former overview's content without the managed header, and the managed copy is gone

#### Scenario: User edits to the managed overview are preserved by the migration

- **WHEN** the managed `overview.md` was edited by the user before the update
- **THEN** the migrated `.claude/CLAUDE.md` contains those edits

#### Scenario: Migration does not clobber an existing overview

- **WHEN** the update operation runs against a project that has both a managed `overview.md` and an existing `.claude/CLAUDE.md`
- **THEN** the existing `.claude/CLAUDE.md` is unchanged
