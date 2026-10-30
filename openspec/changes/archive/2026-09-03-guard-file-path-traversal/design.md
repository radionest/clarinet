## Context

`Files.resolve` renders a `FileDefinition.pattern` and joins the result onto the
record's working directory without checking it:

```python
filename = _template.render_template(fd.pattern, {**self._fields, **overrides},
                                     mode=_template.RenderMode.LENIENT)
path = working_dir / filename          # unchecked
```

`fields_from` builds the render context as `data = {**parent.data, **record.data}`,
so a pattern containing `{data.FIELD}` interpolates user-submitted content whose
only constraint is the record type's JSON Schema — which nobody writes with
filesystem safety in mind. `{patient_id}` is a secondary vector: `PATIENT_ID_REGEX`
is `^[A-Za-z0-9._\-^]{1,64}$`, which permits `..`. A leading `/` is worse than
`..`, because `pathlib` absorbs the base outright:
`Path("/data/rec") / "/etc/passwd" == Path("/etc/passwd")`.

**Current state of the sinks.** One claim in issue #521 is wrong: the read path it
names is already guarded. `resolve_output_file`, `clear_output_files` and
`delete_record_cascade` all funnel through `_resolve_paths_for_file_def` into
`_filter_in_sandbox`, a symlink-aware `Path.resolve()` containment filter added in
PR #253. The unguarded sinks are:

| Sink | Effect of a poisoned value |
|---|---|
| `slicer/context.py:200` `str(f.resolve(fd))` | path shipped to the analyst's Slicer → read **and** write outside the working dir |
| pipeline `ctx.files.resolve(...)` | server-side write to an arbitrary path |
| `grid_policy.py` `subject.unlink()` | server-side **delete** of an arbitrary path |
| `Files.checksums()`, `Files.exists()` | read / hash / existence probe outside the dir |
| `file_validation.py` | existence probe; the string is then persisted as `RecordFileLink.filename` |
| `pipeline/context.py:146` | replays that persisted hostile filename |

**Constraints.** `Files.resolve` is synchronous and hot — `build_slicer_context`
loops it over the whole file registry — so the guard on the join must not perform
syscalls. `clarinet/files/_template.py` is a stdlib-only leaf imported by
`Settings` validators; anything added there must keep that property or the package
import graph acquires a cycle.

## Goals / Non-Goals

**Goals:**

- No rendered path can escape the record's working directory, at any sink.
- A pattern that could produce such a path is refused when the configuration is
  loaded, not when the first record hits it.
- A traversal never degrades into a fallback, a skip, or a logged warning that
  reads as routine.
- A user's own bad submission returns 422; an administrator's bad pattern is a
  server error.
- The `{data.*}` ban is reversible in one commit (#552).

**Non-Goals:**

- Replacing `_filter_in_sandbox`. It stays exactly where it is as the
  symlink-aware second layer at the delete and serve sinks.
- Symlink-aware checking inside `Files.resolve`. Lexical containment only — see D4.
- The non-traversal corner cases of user data in filenames: length limits,
  Windows-reserved basenames, unicode normalization, PHI in filenames,
  mutability of `record.data`. The ban defers all of them to #552.
- Guarding `Files.render_template`, the static renderer feeding
  `slicer_script_args`. Script arguments may legitimately be absolute paths.
- Any database migration. No schema changes, no data backfill.

## Decisions

### D1 — Scope: every render→join site, not only `Files.resolve`

- **Choice**: guard `resolve`, `checksums`, `render`, `render_for`, and the
  persisted-filename join in `pipeline/context.py`.
- **Rationale**: `resolve()` is the loudest hole but not the only one. Fixing it
  alone leaves a working bypass through `checksums()` — which performs its own
  `working_dir / filename` join — and through the replay of a
  `RecordFileLink.filename` row written before the fix.
- **Alternative rejected**: patch `Files.resolve` only. Smallest diff, but the
  issue is a class of bug, not one line.

### D2 — Failure mode: raise, and check statically at config load

- **Choice**: raise at runtime, and additionally validate the pattern when the
  `FileDefinition` is constructed.
- **Rationale**: silently sanitizing (strip separators, replace with `_`) would
  collide two distinct records onto one file and hide the misconfiguration.
  Raising is the only honest answer. But raising *only* at runtime means a bad
  pattern ships to production and detonates on the first record.
- **Alternative rejected**: sanitize-and-continue. Turns a security bug into a
  silent data-loss bug.

### D3 — Guard placement: value guard **plus** containment on the join

- **Choice**: `assert_path_safe_value(key, value)` on each *coerced* value (after
  `coerce_field_value`, so a list joining to `"a/b"` is caught), reached through a
  new opt-in `path_safe: bool = False` keyword on `render_template` hooked into
  the single existing `_replace` closure — one substitution point, so no render
  path can bypass it. Then `join_within(base, rendered)` on the join.
- **Rationale**: neither layer suffices alone. The value guard cannot see what a
  literal pattern segment contributes; containment cannot distinguish an
  admin-authored `/` from a user-injected one.
- **Alternatives rejected**:
  - *Check the final rendered string.* Cannot tell an admin `/` from a user `/`,
    so a subdirectory pattern such as `{study_uid}/mask.nrrd` would break.

    > **Correction.** The brainstorm justified this by calling that shape "real
    > and exercised by `tests/test_config_primitives.py:55`". That citation is
    > false — the file is 44 lines long, and a search of `tests/`, `clarinet/`
    > and `examples/` finds **no** file definition anywhere whose pattern
    > contains a `/`, nor any documented example of one. Keeping a mid-pattern
    > literal `/` legal is therefore a conservative choice — not breaking a
    > shape the code permits today — rather than a defence of exercised
    > behaviour. It also means subdirectory patterns have **no** regression
    > coverage at present, which makes the test in task 7.9 new coverage rather
    > than a re-run. Whether such a pattern works end to end is untested:
    > nothing in `Files.resolve` creates the intermediate directory.
  - *Containment only, `_filter_in_sandbox` everywhere.* Needs `Path.resolve()`
    syscalls at every call site, and `Files.resolve` is sync and looped over the
    whole registry by `build_slicer_context`.

### D4 — `join_within` is purely lexical

- **Choice**: `os.path.normpath` plus a prefix comparison, no filesystem access.
  Rejects a result that is not under `base`, equals `base` (LENIENT rendering a
  whole-pattern placeholder to `""`), or carries a `..` component. Containment
  only.
- **Rationale**: keeps `Files.resolve` sync and cheap. Symlink games matter at the
  delete and serve sinks, and `_filter_in_sandbox` already covers those with a
  real `Path.resolve()`.
- **AMENDED mid-execution — basename-level dot checking was REMOVED from the
  join.** As first designed this bullet read "*Basename-level dot checking is
  deliberate*". Implementing it proved the opposite: a dot-leading basename is
  exactly what a legitimately absent optional placeholder renders to under
  LENIENT mode (`{parent_id}.txt` for a parentless record), so rejecting it at
  the join turned "file not found" into a hard 500 on every render-then-join
  path. The check moved to configuration load, where it judges the pattern's
  worst-case render and reaches the administrator at startup instead of a user
  mid-request. See the vanishing-placeholder requirement in `specs/`.

### D5 — `{data.*}` is banned at config load, as a hard reject

- **Choice**: reject any pattern whose placeholder set contains `data` or any
  `data.*` name. Not a deprecation warning, not a runtime-only rejection. The
  error names the offending placeholder and suggests `{id}` / `{parent_id}` /
  `{user_id}`.
- **Rationale**: scope reduction. The runtime guard (D3) closes the traversal on
  its own; the ban removes the non-traversal corner-case set from this change's
  verification surface so the security fix can ship without also settling them.
- **The ban does not make the runtime guard redundant**: `{patient_id}` still
  permits `..`, and `RecordFileLink.filename` rows persisted before the fix still
  get replayed.
- **Alternative rejected**: a settings escape hatch
  (`CLARINET_ALLOW_DATA_IN_FILE_PATTERNS`). Un-banning would be a default flip and
  no downstream would ever be forced into a disk migration — but a
  security-relevant flag outlives its purpose, and the measured reversal cost (D6)
  is low enough not to need one.

### D6 — The ban is shaped for a one-commit reversal

- **Choice**: rule (a) lives in its own `_reject_data_placeholders()` whose
  docstring names #552 and states that the sibling literal-text rule is permanent;
  rejection tests are driven by one module-level list; documentation says
  *temporarily rejected — see #552* at all eight sites rather than dropping the
  placeholder from the grammar.
- **Rationale**: the measured reversal surface is one function, two collateral
  test files, eight doc lines and a CHANGELOG entry. The only genuinely one-way
  part is a downstream project that rewrites its patterns and renames files on
  disk — and a downstream author who reads *removed* does exactly that, while one
  who reads *temporarily rejected* substitutes `{id}` and waits. The wording is
  the mitigation.
- **Verified**: no configuration on the development machine uses `{data.*}` in a
  file-registry pattern — `clarinet_carcinomatosis`,
  `clarinet_epigastric_segmentation`, `clarinet_lymphoma_muscles` and
  `clarinet_nir_liver` were scanned across TOML, JSON and `plan/` Python. Every
  occurrence in the workspace is Clarinet's own tests and documentation.

### D7 — The validator attaches to `FileDefinitionRead` only

- **Choice**: one `@field_validator("pattern")`, on `FileDefinitionRead`.
- **Rationale**: `FileDefinition` is `table=True`, and SQLModel skips Pydantic
  validation on table models. Verified:

  ```
  FileDefinition:     NO validation on __init__
  FileDefinitionRead: validated -> ValidationError
  ```

  Its existing `validate_name_is_identifier` never fires either. A second
  validator there would buy nothing and read to the next maintainer as an
  enforcement point that does not exist. One validator still covers every real
  entry point: `config/primitives.py` (`FileDef`) and
  `utils/file_registry_resolver.py` (`FileRegistryEntry`) both construct
  `FileDefinitionRead`, as do the API POST/PATCH paths.
- **Consequence**: existing `filedefinition` rows are never re-validated on load,
  so no deployed database is invalidated by the ban.

### D8 — Literal-text validation masks placeholders out first

- **Choice**: mask the placeholders, then reject the remaining literal text on
  absolute prefix, a `..` component, a backslash, NUL, empty or whitespace-only,
  a trailing separator, or a dot-leading basename. A literal `/` mid-pattern stays
  legal.
- **Rationale**: keeps `{study_uid}/mask.nrrd` working while catching a pattern
  that is unsafe before any value is substituted. This rule is permanent — it
  survives the #552 reversal.
- **Precedent**: `config/path_uniqueness.py` plus
  `models/record_type.py::validate_output_path_uniqueness` already perform
  config-time pattern validation, lazily imported inside a
  `model_validator(mode="after")` to dodge the config↔models cycle.

### D9 — `UnsafePathError` subclasses `ConfigurationError` directly

- **Choice**: a new exception in `exceptions/domain.py`, a *sibling* of
  `AnonPathError` and explicitly not a subclass of it.
- **Rationale**: four sites catch `AnonPathError` and degrade —
  `Files.for_reader` retries with a raw-UID fallback, `dicomweb/cache.py` and
  `tasks/cache_dicomweb.py` log and skip, `cli/anon.py` counts a failure. If a
  traversal raised something those catch, `for_reader` would silently retry the
  poisoned path and the rest would log it as routine. A traversal must never
  degrade into a fallback.
- **Alternative rejected**: reuse `AnonPathError`. Smaller diff, exactly wrong
  semantics.

### D10 — Where it surfaces: 500 or DLQ, except on record submit

- **Choice**: the project registers no FastAPI exception handlers, so a
  `ClarinetError` reaching a router is a 500 — correct for the Slicer context
  builder and for pipeline tasks, where failure means retry then DLQ per the
  normal contract. The one exception is record submit: `_missing_output_links`
  renders with user-submitted data, so `RecordService` catches `UnsafePathError`
  there and re-raises `UNPROCESSABLE_ENTITY`.
- **Rationale**: a poisoned pattern is an administrator misconfiguration or an
  attack, not a per-record UX condition — but a user's own bad data is, and
  deserves a 422 rather than a server error.
- **Note**: `slicer/context.py:200` catches `(KeyError, ValueError)`.
  `UnsafePathError` is neither, so it propagates rather than being folded into
  `ScriptArgumentError`.

### D11 — Log the reason, never the value

- **Choice**: one WARNING before raising, carrying the record id, the
  file-definition name, the offending placeholder key and the violation reason.
  Not the value.
- **Rationale**: `record.data` may carry PHI (`.claude/rules/logging-pii.md`).
  The value goes into the exception message, which reaches only the caller who
  submitted it.

## Risks / Trade-offs

- **[Risk]** A downstream project using `{data.*}` cannot load its configuration
  after upgrading. → *Mitigation*: CHANGELOG migration note naming the
  replacements; zero affected configurations found on the development machine;
  documentation marks the ban temporary so nobody re-architects a file registry
  around its absence.
- **[Risk]** A deployed database holds a `{data.*}` `FileDefinition` row created
  through the API rather than through a configuration file. → *Mitigation*: rows
  are never re-validated on load (D7), so the instance keeps running; the ban
  bites at the next config reconcile. See Open Questions for the pre-release
  check.
- **[Risk]** `Files.render_for` becomes path-safe, so a caller using it to render
  a non-path string containing a separator would now raise. → *Mitigation*: it has
  exactly two callers, `record_service.py` and `file_validation.py`, both genuine
  path uses. Verified by inspection.
- **[Risk]** A `RecordFileLink.filename` row already persisted with a traversal is
  now rejected at read time instead of being followed. → *Mitigation*: this is the
  remediation, not a regression. No migration; it earns a CHANGELOG line.
- **[Trade-off]** Lexical containment instead of `Path.resolve()` inside
  `Files.resolve`. → Accepted: `resolve()` is sync and looped over the whole
  registry, and `_filter_in_sandbox` already provides the symlink-aware check at
  the sinks where symlink games actually matter.
- **[Trade-off]** Two layers rather than one. → Accepted: neither is sufficient
  alone (D3).
- **[Trade-off]** `Files.render_template` stays unguarded. → Accepted: Slicer
  script arguments legitimately contain absolute paths, and it never feeds a
  working-directory join.
- **[Trade-off]** Shipping a ban known to be scheduled for reversal. → Accepted:
  reverting costs one function, two test files and eight doc lines; shaping for
  that reversal up front is cheaper than the corner-case work the ban defers.

## Migration Plan

No database migration and no schema change — the guard *is* the remediation.

1. Ship the runtime guard and the config-time validator together. Splitting them
   would leave either a validated pattern with unguarded values or a guard that
   only fires after a bad pattern reaches production.
2. On deploy, a configuration containing `{data.*}` or unsafe literal text fails
   at load with `ConfigLoadError` naming the placeholder. This is fail-fast by
   design: the process does not start with a known-unsafe file registry.
3. **Before release**, run the Open Questions check against each deployed
   instance and apply the CHANGELOG migration by hand where it hits.
4. **Rollback**: revert the commit. Nothing persisted changes, so there is no
   state to unwind — an instance downgraded after running with the guard behaves
   exactly as it did before.

**Acceptance**: `tests/test_path_safety.py` passes; `make check` clean; the
regression set in the test plan still resolves (`{study_uid}/mask.nrrd` into a
subdirectory, `seg_{id}.seg.nrrd` and `mask.seg.nrrd` rendering,
`Files.render_template` still accepting an absolute path).

## Open Questions

**THEORY: no deployed database holds a `{data.*}` `FileDefinition` row.** The
scan behind D6 covers checked-out configurations only. A row created through
`POST /file-definitions` on a production instance would not appear in it, and
that project would hit the ban at its next config reconcile.

- *Confirm or refute by*: `SELECT name, pattern FROM filedefinition WHERE pattern
  LIKE '%{data.%'` on each deployed instance, before release.
- *If refuted*: that project needs the CHANGELOG migration applied by hand, and
  its on-disk files renamed to match the replacement pattern.

Everything else from the brainstorm is settled. #552 tracks the deferred
corner-case work and is not a blocker for this change.
