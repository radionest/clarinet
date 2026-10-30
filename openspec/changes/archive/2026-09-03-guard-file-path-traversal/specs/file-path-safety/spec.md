## ADDED Requirements

### Requirement: Pattern literal text is validated at configuration load

A `FileDefinition.pattern` SHALL be rejected when the system loads it if the
literal text outside its placeholders could produce a path escaping the record's
working directory. With placeholders masked out, the remaining text MUST NOT be
absolute, MUST NOT contain a `..` component, a backslash or a NUL byte, MUST NOT
be empty or whitespace-only, MUST NOT end in a path separator, and MUST NOT have
a basename beginning with a dot. A literal `/` in the middle of a pattern SHALL
remain legal so that subdirectory patterns keep working.

This requirement is permanent and independent of the `{data.*}` restriction.

#### Scenario: Absolute literal pattern

- **WHEN** a configuration declares a file definition with pattern `/abs/mask.nrrd`
- **THEN** loading the configuration fails with an error naming the file definition and the reason

#### Scenario: Traversal in literal text

- **WHEN** a configuration declares a file definition with pattern `../x.nrrd`
- **THEN** loading the configuration fails

#### Scenario: Trailing separator

- **WHEN** a configuration declares a file definition with pattern `outputs/`
- **THEN** loading the configuration fails

#### Scenario: Hidden basename

- **WHEN** a configuration declares a file definition with pattern `.hidden.nrrd`
- **THEN** loading the configuration fails

#### Scenario: Subdirectory pattern remains legal

- **WHEN** a configuration declares a file definition with pattern `study_{study_uid}/mask.nrrd`
- **THEN** the configuration loads and the file definition resolves into a subdirectory of the working directory

Note the literal `study_` prefix. A literal `/` mid-pattern stays legal, but the
segment preceding it must not consist solely of an optional placeholder: the bare
`{study_uid}/mask.nrrd` form is rejected by "Patterns that degenerate when an
optional placeholder is absent" below.

#### Scenario: Multi-dot basename remains legal

- **WHEN** a configuration declares file definitions with patterns `seg_{id}.seg.nrrd` and `mask.seg.nrrd`
- **THEN** the configuration loads and both patterns render unchanged

### Requirement: `{data.*}` placeholders are rejected at configuration load

A `FileDefinition.pattern` SHALL be rejected when the system loads it if its
placeholder set contains `data` or any `data.*` name. The error MUST name the
offending placeholder and MUST suggest `{id}`, `{parent_id}` or `{user_id}` as
replacements.

This restriction is temporary, tracked by issue #552. Documentation MUST describe
the placeholder as temporarily rejected rather than removing it from the
documented pattern grammar.

#### Scenario: Data placeholder in a file registry configuration

- **WHEN** a file registry entry declares pattern `birads_{data.BIRADS_R}.txt`
- **THEN** loading the configuration fails with an error naming `data.BIRADS_R`

#### Scenario: Data placeholder through the API

- **WHEN** a client submits a file definition whose pattern contains `{data.timepoint}`
- **THEN** the request is rejected with 422 and the response names the offending placeholder

#### Scenario: Bare data placeholder

- **WHEN** a pattern contains `{data}` rather than a dotted field
- **THEN** loading the configuration fails

#### Scenario: Existing stored definitions are not re-validated

- **WHEN** the application starts against a database whose `filedefinition` rows contain a `{data.*}` pattern, and no configuration reload declares that pattern
- **THEN** startup succeeds and the stored rows are left untouched

#### Scenario: A stored definition that fails validation is skipped, not fatal

- **WHEN** a record type's file registry is read for serialization and one of its stored file definitions carries a pattern the validator now rejects
- **THEN** that definition alone is omitted from the served registry, the remaining definitions are served normally, and one WARNING is emitted naming the record type, the file definition and the reason
- **AND** the registry MUST NOT collapse to null because of a single rejected definition, because that would silently remove every file definition of an unrelated record type

Note: the pattern and the offending placeholder name MAY appear in this WARNING.
A `FileDefinition.pattern` is administrator-authored configuration and a
`{data.FIELD}` placeholder names a schema field, not a record value — this is
categorically different from the substituted values covered by the
value-exclusion requirement above.

### Requirement: Substituted values cannot contribute path separators

When a file pattern is rendered for a path, every substituted value SHALL be
checked after type coercion and MUST be rejected if it contains `/`, `\` or a NUL
byte, or if the whole value is `.` or `..`. The check MUST apply at the single
substitution point used by the renderer, so that no rendering path can bypass it.

#### Scenario: Absolute value absorbs the base

- **WHEN** a record's `patient_id` is `/etc/passwd` and a pattern substitutes it into a path
- **THEN** rendering raises `UnsafePathError` rather than producing `/etc/passwd`

#### Scenario: Relative traversal in a value

- **WHEN** a substituted value is `../../etc/passwd`
- **THEN** rendering raises `UnsafePathError`

#### Scenario: Value is exactly a parent reference

- **WHEN** a record's `patient_id` is `..` and the pattern supplies the literal separator, as in `{patient_id}/mask.nrrd`
- **THEN** rendering raises `UnsafePathError`, because `PATIENT_ID_REGEX` permits `..` and the ban on `{data.*}` does not cover this vector

#### Scenario: NUL byte in a value

- **WHEN** a substituted value contains a NUL byte
- **THEN** rendering raises `UnsafePathError` before the value reaches the operating system layer

#### Scenario: Backslash on POSIX

- **WHEN** a substituted value contains a backslash and the server runs on POSIX
- **THEN** rendering raises `UnsafePathError`, because the resolved path may be consumed by 3D Slicer on a Windows workstation

#### Scenario: Coerced collection joining to a separator

- **WHEN** a substituted value is a collection whose coercion would produce `a/b`
- **THEN** rendering raises `UnsafePathError`, because the check runs after coercion

### Requirement: Patterns that degenerate when an optional placeholder is absent

`{parent_id}`, `{user_id}`, `{study_uid}` and `{series_uid}` are optional: a
parentless, unassigned, patient-level or study-level record legitimately has
none, and lenient rendering substitutes an empty string rather than raising.
A `FileDefinition.pattern` SHALL therefore be rejected at configuration load if
its worst-case render -- every optional placeholder erased, every other
placeholder masked to a non-empty sentinel -- is an empty name, a path with an
empty segment, or a name whose basename begins with a dot.

The error MUST show the degenerate render, name the reason, name which optional
placeholders drove it, and suggest adding literal text to the affected segment.

This requirement is permanent. It is what allows the working-directory join to
enforce containment only, without a dot-leading or empty-name branch of its own.

#### Scenario: Optional placeholder is the whole basename

- **WHEN** a configuration declares a file definition with pattern `{parent_id}.txt`
- **THEN** loading the configuration fails, because a parentless record renders it to `.txt`

#### Scenario: Optional placeholder is a whole path segment

- **WHEN** a configuration declares a file definition with pattern `{study_uid}/mask.nrrd`
- **THEN** loading the configuration fails, because a patient-level record renders it to `/mask.nrrd`

#### Scenario: Literal text rescues the same pattern

- **WHEN** a configuration declares file definitions with patterns `report_{parent_id}.pdf` and `study_{study_uid}/mask.nrrd`
- **THEN** the configuration loads

#### Scenario: Never-absent placeholder needs no literal prefix

- **WHEN** a configuration declares a file definition with pattern `{id}.nrrd`
- **THEN** the configuration loads, because `{id}` is populated for any record that exists

### Requirement: The per-record storage-root override is well-formed and admin-only

`Record.clarinet_storage_path` redirects a record's entire working directory to
an arbitrary absolute root. Because the containment check is purely lexical, it
is only as trustworthy as its own base: a relative or `..`-carrying base such as
`../../../../etc` satisfies containment for every name joined onto it, making the
proof vacuous. The system SHALL therefore reject a value that is not absolute or
not normalized, at the resolution choke point, so the check covers every consumer
and every row regardless of how it was created.

Setting the field SHALL be restricted to an administrator at record creation.
A caller who omits the field is unaffected.

The system SHALL NOT require the value to be contained under the configured
storage path. The field is by design a root disjoint from it -- an admin-only
per-record storage-root override, not a subdirectory selector -- so a well-formed
absolute value is honoured verbatim. This residual is accepted deliberately.

#### Scenario: Malformed override is refused at resolution

- **WHEN** a record carries a `clarinet_storage_path` that is relative or carries a `..` component
- **THEN** resolving that record's working directories raises `UnsafePathError`

#### Scenario: Non-admin may not choose the root

- **WHEN** a non-admin caller creates a record with a non-null `clarinet_storage_path`
- **THEN** the request is refused with 403

#### Scenario: Disjoint absolute root stays legal

- **WHEN** an admin sets a well-formed absolute `clarinet_storage_path` outside the configured storage path
- **THEN** the record's working directories resolve under that root

### Requirement: A rendered name is confined to the working directory

Joining a rendered name onto a working directory SHALL produce a path strictly
inside that directory. The check MUST be purely lexical, performing no filesystem
access, and MUST reject a result that falls outside the base, equals the base, or
contains a `..` component. It MUST apply wherever the system joins a rendered name
onto a working directory, including path resolution, existence checks and checksum
computation.

The join MUST NOT reject a dot-leading basename, and MUST NOT carry a dedicated
empty-name branch. Both shapes are what a legitimately absent optional placeholder
renders to under lenient rendering, so rejecting them here turns "file not found"
into a hard failure on every render-then-join path. They are rejected at
configuration load instead (see "Patterns that degenerate when an optional
placeholder is absent"), so an administrator learns about it at startup rather
than a user meeting a 500 mid-request. An empty rendered name still cannot pass:
it normalises to the base itself, which the equality check rejects.

#### Scenario: Whole-pattern placeholder renders empty

- **WHEN** lenient rendering resolves a whole-pattern placeholder to an empty string, making the joined path equal to the working directory
- **THEN** the join raises `UnsafePathError`

#### Scenario: Checksum computation uses the same guard

- **WHEN** a checksum is requested for a file definition whose rendered name would escape the working directory
- **THEN** the operation raises `UnsafePathError` and no file outside the directory is read or hashed

#### Scenario: Existence probe uses the same guard

- **WHEN** existence is checked for a file definition whose rendered name would escape the working directory
- **THEN** the operation raises `UnsafePathError` and no path outside the directory is probed

#### Scenario: Resolution stays synchronous and syscall-free

- **WHEN** the Slicer context builder resolves every file definition in the registry
- **THEN** containment is enforced without any filesystem access during the join

### Requirement: Persisted filenames are confined on replay

A filename read back from `RecordFileLink.filename` SHALL be subject to the same
containment check when it is joined onto a working directory. A traversal
persisted before this capability existed MUST be refused at read time rather than
followed. No data migration is required.

#### Scenario: Hostile persisted filename

- **WHEN** a pipeline context joins a stored filename of `../../etc/passwd` onto a working directory
- **THEN** the join raises `UnsafePathError` and the path is not followed

### Requirement: A path violation never degrades into a fallback

Path violations SHALL raise a dedicated `UnsafePathError` that is a sibling of
`AnonPathError` and MUST NOT be a subclass of it, so that the existing handlers
which degrade on `AnonPathError` never absorb a traversal.

#### Scenario: Lenient reader does not retry a poisoned path

- **WHEN** a path violation occurs while building a lenient `Files` reader, which retries with a raw-UID fallback on `AnonPathError`
- **THEN** the error propagates instead of triggering the fallback retry

#### Scenario: Cache and CLI handlers do not swallow it

- **WHEN** a path violation occurs in a DICOMweb cache path, a cache pipeline task, or the anonymization CLI — each of which logs and skips on `AnonPathError`
- **THEN** the error propagates instead of being recorded as a routine skip

#### Scenario: Slicer context does not fold it into an argument error

- **WHEN** a path violation occurs inside the Slicer context builder, which catches `KeyError` and `ValueError`
- **THEN** the error propagates, because `UnsafePathError` is neither

### Requirement: Violations surface according to who caused them

A path violation caused by user-submitted record data SHALL be reported to that
user as 422. Anywhere else the error MUST propagate as a server-side failure: a
500 from an API route, or the normal retry-then-dead-letter path for a pipeline
task.

#### Scenario: User submits data that poisons a rendered path

- **WHEN** a record submission renders an output-file pattern and a substituted value violates the path rules
- **THEN** the API responds 422 rather than 500

#### Scenario: Administrator misconfiguration reaches a route

- **WHEN** a path violation occurs outside record submission and reaches an API route
- **THEN** the response is a 500, because a poisoned pattern is a misconfiguration or an attack, not a per-record user condition

#### Scenario: Pipeline task hits a violation

- **WHEN** a pipeline task raises a path violation
- **THEN** the task fails and follows the normal retry and dead-letter contract

### Requirement: Violation logs exclude the offending value

On the record-submit path, the system SHALL log one WARNING carrying the record
id, the file definition name and the violation reason before raising. Other raise
sites -- the working-directory join, the storage-root resolver and the
file-validation existence probe -- do NOT log before raising; the violation
surfaces through the exception and, on the API path, through its dedicated
handler, which logs the message plus the request method and path but has no
record or file-definition context to name.

Wherever such a log is emitted, it MUST NOT contain the offending value, because
record data may carry PHI.

The offending value MUST NOT appear in the exception's message either. It SHALL
travel as a structured attribute on the exception, so a caller reporting the
failure to the submitter can reach it while no log sink renders it. This is
required because a `ConfigurationError` reaching a route is logged with its full
traceback by the API exception handler, and the log scrubber redacts only
credential-shaped text, never PHI — so a value placed in the message would reach
persistent logs by that path.

#### Scenario: PHI-bearing value is rejected

- **WHEN** a substituted value drawn from record data violates the path rules
- **THEN** the emitted WARNING names the record, the file definition, the placeholder key and the reason, and does not contain the value

#### Scenario: Value is absent from the exception message

- **WHEN** a path violation is raised for a value drawn from record data
- **THEN** the value is absent from the exception's message and is retrievable only from a structured attribute on the exception

#### Scenario: The structured attribute still reaches diagnose-enabled sinks

- **WHEN** a path violation is raised on a path that does not reach the API's exception handlers -- a worker task, a background task, or a broad `except` that logs with a traceback
- **THEN** the value MAY still be disclosed, because the console sink renders frame locals into tracebacks

Keeping the value off the exception message is therefore necessary but NOT
sufficient. The guarantee holds only for a violation that reaches the API's
exception handlers uncaught, where a dedicated handler logs the message without
attaching a traceback. This residual is confirmed, not theoretical, on the worker
path. Any future claim that the value cannot reach a log sink MUST name this
boundary.

### Requirement: Non-path renderers stay unguarded

The stateless renderer used to build Slicer script arguments SHALL NOT apply the
substituted-value path checks, because script arguments may legitimately be
absolute paths. It MUST NOT be used to join a working directory.

#### Scenario: Absolute path as a Slicer script argument

- **WHEN** a Slicer script argument template renders a value that is an absolute path
- **THEN** rendering succeeds unchanged

### Requirement: Collection globbing is unaffected

Globbing a collection pattern SHALL remain outside these checks, because every
placeholder is replaced by a wildcard before the glob runs and no user-supplied
value reaches the filesystem.

#### Scenario: Collection pattern globbed

- **WHEN** a file definition marked as a collection is globbed
- **THEN** placeholders are replaced by wildcards and the operation behaves exactly as before this capability
