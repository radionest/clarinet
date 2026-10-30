## Why

Issue #521: `Files.resolve` joins a record's working directory with an
unsanitized `render_template` result. A `FileDefinition.pattern` embedding
`{data.FIELD}` interpolates user-submitted `record.data` with no character
validation, so a resolved path can escape that directory — a server-side write
through any pipeline task, a server-side delete through `grid_policy.unlink()`,
and read plus write on the analyst's workstation through the Slicer context. A
leading `/` in a value is worse than `..`, because `pathlib` discards the base
entirely.

The download and cascade-delete sinks are already covered by
`_filter_in_sandbox`, added in PR #253; the render→join sites never were. The
sibling `_safe_render` proves the project already knows the checks are needed —
it just applies them to directory segments and never to the basename.

## What Changes

**Value substitution in file patterns**
- From: `render_template` substitutes any coerced value verbatim, including
  `/`, `\`, NUL and `..`.
- To: an opt-in `path_safe=True` keyword rejects those in the single existing
  `_replace` closure, so no render path can bypass it.
- Reason: the substituted value is the injection point; refusing it there
  means nothing downstream has to reason about the value's origin.
- Impact: non-breaking for every legitimate pattern. `Files.render_template`
  (the static one feeding `slicer_script_args`) deliberately keeps
  `path_safe=False`, because script arguments may be absolute paths.

**Joining a rendered name onto a working directory**
- From: `working_dir / filename`, unchecked.
- To: `join_within(base, rendered)` — purely lexical (`os.path.normpath`, no
  syscalls), rejecting a result outside `base`, equal to `base`, carrying a
  `..` component. It deliberately does NOT reject a dot-leading basename --
  that check moved to config load, because a dot-leading name is what a
  legitimately absent optional placeholder renders to.
- Reason: the value guard cannot see what a literal pattern segment
  contributes; containment cannot distinguish an admin `/` from a user `/`.
  Neither layer is sufficient alone.
- Impact: `study_{study_uid}/mask.nrrd` still resolves into a subdirectory and
  `mask.seg.nrrd` still renders. NOT fully non-breaking, as first scoped: the
  bare `{study_uid}/mask.nrrd` form is now rejected at config load by the
  vanishing-placeholder rule added mid-execution (a patient-level record would
  render it to `/mask.nrrd`). Any pattern whose whole path segment or whole
  basename is an optional placeholder needs literal text added. CHANGELOG.md
  carries the migration.

**`{data.*}` placeholders in `FileDefinition.pattern`**
- From: advertised and supported.
- To: rejected at config load by a `pattern` validator on
  `FileDefinitionRead`.
- Reason: scope reduction, not the security fix — the runtime guard closes the
  traversal on its own. The ban defers the non-traversal corner cases (length
  limits, Windows-reserved basenames, unicode normalization, PHI in filenames,
  mutability of `record.data`) to #552.
- Impact: **breaking** for any downstream config using `{data.*}`. Zero such
  configs exist on the development machine. Deliberately shaped for a
  one-commit reversal; documentation marks it *temporarily rejected*, never
  removes the placeholder from the grammar.

**Unsafe literal text in a pattern**
- From: unvalidated.
- To: rejected at config load — absolute prefix, `..` component, backslash,
  NUL, empty or whitespace-only, trailing separator, dot-leading basename.
  A literal `/` mid-pattern stays legal.
- Reason: a bad pattern otherwise ships to production and detonates on the
  first record.
- Impact: non-breaking; this rule is permanent and survives #552.

**Persisted filenames**
- From: `pipeline/context.py` joins a stored `RecordFileLink.filename`
  verbatim.
- To: the same containment check applies, so a traversal persisted before this
  change is refused at read time instead of being followed.
- Impact: no migration and no schema change — the guard is the remediation.

## Capabilities

### New Capabilities

- `file-path-safety`: how a `FileDefinition.pattern` is validated when a
  configuration is loaded, how substituted values are constrained when that
  pattern is rendered, how the rendered name is confined to the record's
  working directory, and which error surfaces where when any of it fails.

### Modified Capabilities

None. `slicer-overlap-detection` is the only existing spec and is unaffected.

## Impact

**Code**

- `clarinet/files/_template.py` — new `assert_path_safe_value`, `join_within`,
  `validate_file_pattern`; `path_safe` keyword on `render_template`.
- `clarinet/files/__init__.py` — export `validate_file_pattern` through the
  existing lazy `__getattr__`, so no caller imports the private leaf.
- `clarinet/files/facade.py` — `resolve`, `checksums`, `render`, `render_for`.
- `clarinet/services/pipeline/context.py` — containment on the persisted
  filename join.
- `clarinet/models/file_schema.py` — `pattern` validator on
  `FileDefinitionRead` (**not** on `FileDefinition`: it is `table=True`, so
  SQLModel skips validation there — verified).
- `clarinet/exceptions/domain.py` — `UnsafePathError`, subclassing
  `ConfigurationError` directly and explicitly **not** `AnonPathError`, whose
  four catch sites all degrade into a fallback or a skip.
- `clarinet/services/record_service.py` — catch at `_missing_output_links` and
  re-raise `UNPROCESSABLE_ENTITY`, so a user's own bad data is a 422.

**Behaviour reaching operators**

Anywhere else, `UnsafePathError` propagates: a 500 from a router, a retry then
DLQ for a pipeline task. A poisoned pattern is an administrator
misconfiguration or an attack, not a per-record UX condition.

**Documentation** — CHANGELOG (breaking entry plus migration), the `pattern`
docstring, `docs/kb/files-and-anonymization.md`, and eight `{data.FIELD}`
mentions across seven files (`.claude/rules/file-registry.md` and its
byte-identical mirror `clarinet/docs/file-registry.md`,
`clarinet/docs/agent/claude/{definitions,workflows}.md`,
`examples/project_template/.claude/rules/{definitions,workflows}.md`).

**Not affected** — `Files.glob`, because `glob_file_paths` substitutes `*` for
every placeholder; `config/path_uniqueness.py`, which already refuses to count
`{data.*}` as a uniqueness discriminator; existing `filedefinition` rows, which
are never re-validated on load.
