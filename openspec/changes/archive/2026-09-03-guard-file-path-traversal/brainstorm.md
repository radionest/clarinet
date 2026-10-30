<!--
Raw capture of the interactive brainstorming (superpowers:brainstorming).
Decision-log format: background → verified attack surface → decision chain Q1-Q5 → approved design → trade-offs.
Source: GitHub issue radionest/clarinet#521.
-->

# Brainstorm — path-traversal guards for rendered file paths

## Background — issue #521

> `clarinet/files/facade.py:150` — `resolve()` joins the working dir with an
> unsanitized `render_template()` result, while the sibling `_safe_render`
> (`clarinet/files/_storage.py:310-322`) applies the `/`, `\\`, `..` and leading-dot
> checks. A `FileDefinition.pattern` embedding `{data.FIELD}` interpolates
> user-submitted `record.data` with no character validation, so a resolved path can
> escape the record working directory — readable via
> `GET /records/{id}/output-files/{name}`, writable via Slicer/pipeline, and
> deletable via `record_service` unlink and the new `on_grid_mismatch="delete"`.
> Found during pre-PR review of `worktree-grid-conformance-guards`.

The core claim holds. `Files.resolve` is:

```python
def resolve(self, file_def: FileDefArg, **overrides: Any) -> Path:
    fd = self._lookup(file_def)
    working_dir = self._dirs[fd.level or self._level]
    filename = _template.render_template(
        fd.pattern, {**self._fields, **overrides}, mode=_template.RenderMode.LENIENT
    )
    path = working_dir / filename          # <-- unchecked
```

`_safe_render` in the same package proves the project already knows the checks are
needed — it just applies them to *directory* segments only, never to the basename.

### One claim in the issue is wrong

The **read** path named in the issue is already guarded.
`resolve_output_file` (`record_service.py:989`), `clear_output_files` (:1027) and
`delete_record_cascade` all funnel through `_resolve_paths_for_file_def`
(:929-967), which ends in:

```python
def _filter_in_sandbox(paths: list[Path], sandbox: Path) -> list[Path]:   # :59-66
    sandbox_resolved = sandbox.resolve()
    return [p for p in paths if p.resolve().is_relative_to(sandbox_resolved)]
```

That filter is symlink-aware (`Path.resolve()`), added 2026-05-06 in commit
`10479c2` / PR #253. The download endpoint, the clear path and cascade delete are
therefore **not** exploitable. The write and delete claims are accurate.

## Verified sink inventory

| Sink | Guarded today? | Effect of a poisoned value |
|---|---|---|
| `resolve_output_file` (download endpoint) | ✅ `_filter_in_sandbox` | none |
| `clear_output_files`, `delete_record_cascade` | ✅ `_filter_in_sandbox` | none |
| `slicer/context.py:200` `str(f.resolve(fd))` | ❌ | path shipped to the analyst's Slicer → read **and** write outside the working dir |
| pipeline `ctx.files.resolve(...)` (e.g. `convert_series.py:60`) | ❌ | server-side write to an arbitrary path |
| `grid_policy.py:64` + `:109` `subject.unlink()` (open PR) | ❌ | server-side **delete** of an arbitrary path |
| `Files.checksums()` (`facade.py:203-206`), `Files.exists()` (:155) | ❌ | read / hash / existence-probe outside the dir |
| `file_validation.py:102,110` | ❌ | existence probe; the traversal string is then persisted as `RecordFileLink.filename` |
| `pipeline/context.py:146` `f.dir(level) / link.filename` | ❌ | replays the persisted hostile filename |

`glob_file_paths()` in `_patterns.py` substitutes `*` for every placeholder, so the
glob path is not user-influenced — out of scope.

## Untrusted inputs

1. **`{data.FIELD}` — primary.** `fields_from()` (`_patterns.py`) builds
   `data = {**parent.data, **record.data}`; `_resolve_dotted` in `_template.py`
   walks the dotted path. The only constraint on those values is the record type's
   JSON Schema, which no one writes with filesystem safety in mind.
2. **`{patient_id}` — secondary.** `PATIENT_ID_REGEX` (`models/patient.py:26`) is
   `^[A-Za-z0-9._\-^]{1,64}$` — it permits `..`.
3. Structurally safe: `{id}`, `{parent_id}`, `{user_id}`, DICOM UIDs, type names.

A leading `/` in a value is worse than `..`, because `pathlib` absorbs the base
entirely: `Path("/data/rec") / "/etc/passwd" == Path("/etc/passwd")`.

## Decision chain

### Q1 — Scope → all render→join sites, not just `Files.resolve`

`resolve()` is the loudest but not the only hole; `checksums()`, `exists()`,
`render()`, `render_for()` and the persisted-filename replay in
`pipeline/context.py:146` all reach the filesystem from the same rendered string.
Fixing one leaves a working bypass.

### Q2 — Failure mode → raise at runtime, **plus** a static check at config load

Silently sanitizing (strip separators, replace with `_`) would collide two distinct
records onto one file and hide the misconfiguration. Raising is the only honest
answer. But raising *only* at runtime means a bad `FileDefinition.pattern` ships to
production and detonates on the first record — so the pattern itself is validated
when it is loaded.

### Q3 — `{data.FIELD}` in patterns → **banned** (user reversed an earlier answer)

Initially answered "keep it, guard the values". Reversed while answering Q4:

> "Also I think we should prevent using Record.data$field in constructing of path."

Confirmed as a hard reject at config load (Q5). Consequences stated and accepted:

- It is a **breaking change** — `{data.FIELD}` is advertised in
  `file_schema.py:44` and in `.claude/rules/file-registry.md`, so downstream
  configs using it stop loading.
- It does **not** make the runtime guard redundant: `{patient_id}` still permits
  `..`, and `RecordFileLink.filename` rows persisted before the fix still get
  replayed.

### Q4 — Where the guard lives → **A: guard the substituted values, then assert containment on the join**

Alternatives considered:

- **B — check the final rendered string.** Rejected: it cannot distinguish an
  admin-authored literal `/` from a user-injected one, so `{study_uid}/mask.nrrd`
  would break. That pattern shape is real and exercised by
  `tests/test_config_primitives.py:55`.
- **C — containment check only (`_filter_in_sandbox` everywhere).** Rejected as the
  sole mechanism: it needs `Path.resolve()` (syscalls) at every call site, and
  `Files.resolve` is sync and hot — `build_slicer_context` loops it over the whole
  registry.

A gives both: values are refused before they can mean anything to the filesystem,
and the join is still checked lexically as a backstop.

### Q5 — Ban severity → hard reject at config load

Not a deprecation warning, not a runtime-only rejection. The pattern is refused
when the `FileDefinition` is constructed.

### Q6 — Reversibility → hard ban kept, but shaped for a one-commit reversal

The ban exists to shrink *this* PR's verification surface, not because
`{data.*}` is unfixable. It is scheduled to return in a separate PR once the
non-traversal corner cases are handled. Q6 asks what that later PR will cost.

**The ban is not what closes #521.** `assert_path_safe_value` + `join_within`
(Section 1) reject `/`, `\`, NUL and `.`/`..` and refuse any escape from the
working dir whatever the value's origin. What the ban forecloses is the
*non-traversal* set: length limits, Windows-reserved basenames (they reach the
analyst's machine through the Slicer path), unicode normalization, PHI in
filenames. Section 1 must therefore be complete on its own merits and must not
lean on Section 2 for the security argument.

Note also that `config/path_uniqueness.py` already refuses to count `{data.*}`
as a discriminator, so a `{data.*}`-only non-collection OUTPUT pattern already
fails config load today unless `allow_path_collision=True`. The surface being
banned is smaller than it looks.

**Reversal surface, measured 2026-08-09:**

| Layer | Cost to un-ban | One-way? |
|---|---|---|
| Rule (a) — one branch in one shared function | delete it | no |
| Tests *for* the ban | delete / invert | no |
| Tests broken *by* the ban — only two files: `tests/test_file_validation.py:289` (builds `FileDefinitionRead`), `tests/test_path_uniqueness.py:86,98` (builds a `RecordTypeCreate`) | restore the placeholder | no |
| Doc mentions — 8 sites in 7 files (below) | re-edit the same 8 | churn ×2 |
| CHANGELOG breaking entry | a second entry saying "re-allowed" | **append-only** |
| Downstream config using `{data.*}` → rewritten pattern → different resolved filename → files on disk unreachable, `RecordFileLink.filename` rows stale | a second disk migration nobody will perform | **the real one-way door** |
| `filedefinition` DB rows | none — table models skip validation on load | no |

Nine of the ten `{data.*}` hits in the test suite are raw `render_template`
calls that never construct a model, so the ban does not touch them
(`test_file_patterns.py`, `test_path_template_renderer.py`,
`test_files_facade.py:134`, `test_files_fields_from.py:36`,
`test_pipeline_context.py:155`).

**The one-way door is currently unarmed.** Every Clarinet downstream project on
the development machine — `clarinet_carcinomatosis`,
`clarinet_epigastric_segmentation`, `clarinet_lymphoma_muscles`,
`clarinet_nir_liver` — was scanned across configs (TOML/JSON) and `plan/`
Python for `{data.*}` inside a file-registry pattern: **zero hits**. Every
occurrence in the workspace is Clarinet's own tests and docs.

> **THEORY: no deployed database holds a `{data.*}` `FileDefinition` row.** The
> scan covers checked-out configs only. A row created through `POST /file-definitions`
> on a production instance would not appear in it, and such a project would hit the
> ban at its next config reconcile. Confirm by querying `SELECT name, pattern FROM
> filedefinition WHERE pattern LIKE '%{data.%'` on each deployed instance before
> release; refute → that project needs the migration note applied by hand.

## Approved design

### Section 1 — the runtime guard (approved)

Two primitives, both in `clarinet/files/_template.py` (stdlib-only leaf, no import
cycle risk):

**`assert_path_safe_value(key, value)`** — runs on each *coerced* value, i.e. after
`coerce_field_value`, so a list joining to `"a/b"` is caught. Rejects:

- `/` or `\` anywhere in the value
- `\x00`
- the whole value being `.` or `..`

Exposed through a new opt-in `path_safe: bool = False` keyword on
`render_template`, hooked into the single existing `_replace` closure — one
substitution point, so no path can bypass it. `Files.render_template` (the static
one, used for `slicer_script_args` at `slicer/context.py:87`) keeps
`path_safe=False`, because script args may legitimately be absolute paths.

**`join_within(base: Path, rendered: str) -> Path`** — purely lexical
(`os.path.normpath`, no filesystem access), so `Files.resolve` stays sync and cheap.
Rejects when the joined result:

- is not under `base` after normalisation
- equals `base` (LENIENT rendering a whole-pattern placeholder to `""`)
- has any `..` component
- has a **basename** starting with `.`

Basename-level dot checking is deliberate — a dot elsewhere (`mask.seg.nrrd`) is
normal, and checking the whole rendered string would break subdirectory patterns.

The two primitives are **not** both applicable everywhere — a site that renders but
does not join gets only the value guard, and a site that joins a string it did not
render gets only the containment check:

| Site | `path_safe=True` | `join_within` | Note |
|---|---|---|---|
| `facade.py:144` `resolve()` | ✅ | ✅ | the reported hole |
| `facade.py:203-206` `checksums()` (singular branch) | ✅ | ✅ | second `working_dir / filename` join |
| `facade.py:168` `render()` | ✅ | — | returns `str`, no base to join against |
| `facade.py:172` `render_for()` (static) | ✅ | — | same; `record_service.py:97` and `file_validation.py:102` inherit it |
| `pipeline/context.py:146` | — | ✅ | joins a **persisted** `link.filename`, nothing is rendered here |
| `facade.py:154` `exists()` | inherited | inherited | delegates to `resolve()`; no separate change |
| `facade.py:182` `render_template()` (static) | ❌ deliberate | — | Slicer script args may be absolute paths |
| `facade.py:157` `glob()` | n/a | n/a | `glob_file_paths` substitutes `*`; not user-influenced |

`_filter_in_sandbox` stays exactly where it is as the symlink-aware second layer.

### Section 2 — the config-time ban and static check (approved)

One shared **`validate_file_pattern(pattern: str) -> str`**, also in the
stdlib-only `_template` leaf, exported from `clarinet/files/__init__.py` through
the existing lazy `__getattr__` (same treatment as `PLACEHOLDER_REGEX`), so no
caller imports a private leaf and the no-cycle property holds.

Attached as `@field_validator("pattern")` on **`FileDefinitionRead`**
(`models/file_schema.py:164`) — and **only** there.

> **Correction to an earlier draft of this section**, which said to attach it to
> `FileDefinition` (`models/file_schema.py:59`) as well, "the same pair that already
> duplicates `validate_name_is_identifier`". That pairing is a mirage: `FileDefinition`
> is `table=True`, and SQLModel skips Pydantic validation on table models, so its
> `validate_name_is_identifier` never fires either. Verified 2026-08-09:
>
> ```
> FileDefinition:     NO validation on __init__
> FileDefinitionRead: validated -> ValidationError
> ```
>
> Adding a second validator there buys nothing and would read to the next maintainer
> as an enforcement point that does not exist.

One validator covers every real entry point: `config/primitives.py:283` (`FileDef`)
and `utils/file_registry_resolver.py:117` (`FileRegistryEntry`) both construct
`FileDefinitionRead`, as do the API POST/PATCH paths. Existing `filedefinition` rows
are never re-validated on load, so no database is invalidated by the ban.

**Rule (a) — the ban.** Reject any pattern whose placeholder set (via
`extract_placeholders`) contains `data` or any `data.*` name. The error names the
offending placeholder and suggests `{id}` / `{parent_id}` / `{user_id}`.

**Rule (b) — literal-text safety.** Mask the placeholders out, then reject the
remaining literal text on: absolute prefix, a `..` component, a backslash, NUL,
empty/whitespace-only, a trailing separator, or a basename starting with a dot. A
literal `/` mid-pattern stays legal, so `{study_uid}/mask.nrrd` keeps working.

Raised as `ValueError`, matching `validate_name_is_identifier` on the same models →
422 on the API path, `ConfigLoadError` on the config path. No new exception type is
needed at config-load time.

Precedent for config-time pattern validation already exists:
`config/path_uniqueness.py` + `models/record_type.py:365-382`
(`validate_output_path_uniqueness`, lazily imported inside a
`model_validator(mode="after")` to dodge the config↔models cycle).

Four documentation edits ship in the same PR: the CHANGELOG breaking-change entry
with migration, the `pattern` docstring at `file_schema.py:44`, the placeholder line
in `.claude/rules/file-registry.md`, and `docs/kb/files-and-anonymization.md`.

### Section 3 — error contract, tests, blast radius (approved)

**The exception.** New `UnsafePathError` in `exceptions/domain.py`, subclassing
`ConfigurationError` **directly** — a sibling of `AnonPathError` (:473), explicitly
not a subclass. Four sites catch `AnonPathError` and degrade:

| Site | Degradation |
|---|---|
| `files/facade.py:101` (`Files.for_reader`) | retries with raw-UID fallback |
| `services/dicomweb/cache.py:206` | logs, skips |
| `services/pipeline/tasks/cache_dicomweb.py:97` | logs, skips |
| `cli/anon.py:232` | logs, `counters['failed'] += 1` |

If a traversal raised something those catch, `for_reader` would silently retry the
poisoned path and the rest would log it as routine. A traversal must never degrade
into a fallback.

**Where it surfaces.** The project registers no FastAPI exception handlers, so a
`ClarinetError` reaching a router is a 500. That is correct for the Slicer context
builder (note `slicer/context.py:200` catches `(KeyError, ValueError)` —
`UnsafePathError` is neither, so it propagates rather than being folded into
`ScriptArgumentError`) and for pipeline tasks (failure → retry/DLQ per the normal
contract). A poisoned pattern is an admin misconfiguration or an attack, not a
per-record UX condition.

The one exception is **record submit**: `_missing_output_links`
(`record_service.py:97`) renders with user-submitted data, so the service catches
`UnsafePathError` there and re-raises `UNPROCESSABLE_ENTITY` from
`clarinet.exceptions.http`. A user's own bad data returns 422, not a server error.

**Logging.** One WARNING before raising, carrying the record id, the file-definition
name, the offending placeholder key and the violation reason — **not the value**.
Record data may carry PHI (see `.claude/rules/logging-pii.md`). The value goes into
the exception message, which reaches only the caller who submitted it.

**Tests.** New `tests/test_path_safety.py` for the two primitives and the pattern
validator (pure unit, stdlib-only, mirroring `test_path_template_renderer.py` and
`test_path_uniqueness.py`); extensions to `tests/test_files_facade.py` for the
facade sites, and to `tests/test_config_primitives.py` and
`tests/test_file_registry_resolver.py` for config-load rejection. Coverage:

1. Absolute absorption — a value of `/etc/passwd` is rejected (worst case: pathlib
   discards the base).
2. Relative traversal — `../../etc/passwd`, and the split form where the value is
   `..` and the pattern supplies the literal `/`.
3. Hidden basename — a rendered basename of `.ssh` / `.bashrc` is rejected.
4. NUL byte — rejected up front, not at an unpredictable point in the OS layer.
5. Empty render under LENIENT — a whole-pattern placeholder rendering to `""` makes
   the joined path equal the base → rejected.
6. Backslash on POSIX — rejected even though POSIX does not treat it as a separator;
   Slicer runs on the analyst's machine, which may be Windows.
7. The `{data.*}` ban — on `FileDefinition`, on `FileDefinitionRead`, and through
   both config paths (`FileDef`, `FileRegistryEntry`).
8. Literal-text rules — `/abs/mask.nrrd`, `../x.nrrd`, trailing separator,
   `.hidden.nrrd` all rejected at config load.
9. **Regressions that must keep working** — `{study_uid}/mask.nrrd` still resolves
   into a subdirectory; `seg_{id}.seg.nrrd` and `mask.seg.nrrd` still render;
   `Files.render_template` still accepts an absolute path for Slicer script args
   (`path_safe=False`).
10. **Proof the ban alone is insufficient** — `patient_id = ".."` is permitted by
    `PATIENT_ID_REGEX`, and `Files.resolve` must still reject it.

`tests/test_path_template_renderer.py` and `tests/test_storage_paths.py` already
assert `_safe_render`'s messages — check for match regressions.

**Blast radius.**

- Breaking for downstream projects whose file registry uses `{data.*}`: config stops
  loading with an error naming the placeholder. CHANGELOG migration — replace with
  `{id}` / `{parent_id}` / `{user_id}`, or declare one `FileDefinition` per variant.
- `Files.render_for` becomes path-safe, so a caller using it to render a non-path
  string containing a separator would now raise. Its only callers are
  `record_service.py:97` and `file_validation.py:102`, both genuine path uses.
- Any `RecordFileLink.filename` row already persisted with a traversal is now
  rejected at read time in `pipeline/context.py:146` instead of being followed. No
  migration and no DB schema change — the guard *is* the remediation — but it earns
  a CHANGELOG line.
- Cost is negligible: `join_within` is `os.path.normpath` plus a prefix comparison,
  no syscalls, so `Files.resolve` stays sync and cheap inside the
  `build_slicer_context` loop.

### Section 4 — reversal shaping (approved)

Four requirements, so the follow-up PR is a deletion rather than an
archaeology exercise. None of them cost anything now.

1. **One deletion point.** Rule (a) lives in its own
   `_reject_data_placeholders(pattern: str) -> None`, called from
   `validate_file_pattern` on the line above rule (b). Its docstring names the
   follow-up issue and states that rule (b) is permanent while rule (a) is not.
   Un-banning is: delete the function, delete the call.
2. **Ban cases in one list.** The `{data.*}` rejection tests are driven by a
   single module-level list in `tests/test_path_safety.py` (e.g.
   `BANNED_DATA_PATTERNS`), and the two collateral edits
   (`tests/test_file_validation.py:289`, `tests/test_path_uniqueness.py:86,98`)
   carry a `# see #552` comment at the changed line. Un-banning moves strings
   between two lists.
3. **Docs mark it temporary, never delete it.** Every site says *temporarily
   rejected — see #552*, keeping the placeholder visible in the grammar. A
   downstream author who reads "removed" re-architects their file registry and
   renames files on disk; one who reads "temporarily rejected" substitutes
   `{id}` and waits. That distinction is the only thing standing between the
   ban and the one-way door in Q6. The 8 sites:

   | File | Line |
   |---|---|
   | `.claude/rules/file-registry.md` | 21 |
   | `clarinet/docs/file-registry.md` (byte-identical mirror of the above — keep in sync) | 21 |
   | `clarinet/docs/agent/claude/definitions.md` | 41 |
   | `clarinet/docs/agent/claude/workflows.md` | 129 |
   | `examples/project_template/.claude/rules/definitions.md` | 42, 66 |
   | `examples/project_template/.claude/rules/workflows.md` | 129 |
   | `clarinet/models/file_schema.py` (docstring) | 44 |

   `examples/project_template/.claude/rules/definitions.md:66` is the
   `allow_path_collision` row, which cites `{data.FIELD}` as its motivating
   example — it needs a different example, not a temporary-rejection note.
   `docs/kb/files-and-anonymization.md` has no existing mention; it gains a new
   paragraph covering both the guard and the ban.
4. **The follow-up issue is #552** — opened 2026-08-10, before the docs are
   written, since eight doc sites and the CHANGELOG entry reference its number.
   It carries the deferred corner-case list: length limits, Windows-reserved
   basenames, unicode normalization, PHI in filenames, mutability of
   `record.data` (a value-derived path changes on every submit, orphaning the
   previous file and staling its `RecordFileLink.filename` row), and collision
   behaviour under `allow_path_collision=True`. That mutability point may
   resolve #552 as `wontfix` for OUTPUT files while reinstating the placeholder
   for INPUT files — an acceptable outcome, recorded there.

## Trade-offs accepted

| Trade-off | Why accepted |
|---|---|
| Banning `{data.*}` breaks downstream configs | Migration is mechanical, and no config on the development machine uses it (Q6). The ban buys a smaller verification surface for this PR, not the traversal fix itself — which Section 1 delivers alone. |
| The ban ships knowing it will be reverted | Reverting costs one function deletion, two test files and eight doc lines (Q6). Shaping for that reversal up front (Section 4) is cheaper than the corner-case work the ban defers. |
| Lexical containment instead of `Path.resolve()` in `Files.resolve` | `resolve()` is sync and looped over the whole registry. `_filter_in_sandbox` already provides the symlink-aware check at the delete/serve sinks, which is where symlink games actually matter. |
| Two layers (value guard + containment) rather than one | Neither alone is sufficient: the value guard cannot see what a literal pattern segment contributes, and lexical containment cannot distinguish an admin `/` from a user `/`. |
| `Files.render_template` stays unguarded | Slicer script args legitimately contain absolute paths; guarding it would break `slicer/context.py:87`. It never feeds a working-dir join. |
| Runtime guard retained despite the ban | `{patient_id}` still permits `..`, and pre-fix `RecordFileLink.filename` rows still get replayed. |
