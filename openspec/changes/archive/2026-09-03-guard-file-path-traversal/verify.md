# Verification — guard-file-path-traversal

Status of the group-9 gates, plus the residuals groups 12 and 13 deliberately
leave open. Written after the second review round (PR #556).

## 9.1 — behavioural test selector

```
./scripts/run_tests.sh -k "path_safety or files_facade or file_registry or path_uniqueness or file_validation" -q
```

**PASS — 213 passed, 0 failed**, re-run after the second- and third-round fixes below.
Broader unit sweep over every touched file (`test_path_safety`,
`test_record_service`, `test_record_type_model`, `test_file_validation`,
`test_config_primitives`, `test_files_facade`, `test_file_repository`,
`test_pipeline_context`, `test_file_registry_resolver`, `test_path_uniqueness`,
`test_unsafe_path_error_handler`): **402 passed**. Full `make test-fast` after
round 3: **3391 passed, 152 skipped, 0 failed**.

## 9.2 — `make check`

**PASS** (format + lint + typecheck clean).

## 9.3 — pre-release DB scan — **STILL OPEN, RELEASE GATE**

```sql
SELECT name, pattern FROM filedefinition WHERE pattern LIKE '%{data.%';
```

Not runnable from the development environment: it must be executed against each
**deployed** instance. Record one row per instance here before tagging a
release.

| Instance | Date | Rows returned | Action taken |
|---|---|---|---|
| _(unfilled)_ | | | |

**This gate is more load-bearing than originally written.** The task text said a
hit "needs the CHANGELOG migration applied by hand". A hit actually means the
definition is skipped from `RecordType.file_registry`, and everything reading
that registry loses sight of it — six distinct behaviours, enumerated in the
CHANGELOG Security section and in `docs/kb/files-and-anonymization.md`: the
`required=True, role=INPUT` block gate, checksums/`RecordFileLink` creation,
file-change triggers, output-file collection on delete (files are **left on
disk**), `ctx.files.resolve` in pipeline tasks (→ DLQ), and the download
endpoint. A WARNING in the log is not a cosmetic notice.

## 9.4 — #552 discoverability

**PASS.** `_reject_data_placeholders` carries the issue URL, and every doc site
written in group 8 references it: `CHANGELOG.md`,
`docs/kb/files-and-anonymization.md`, `.claude/rules/file-registry.md`,
`clarinet/docs/file-registry.md`, `clarinet/docs/agent/claude/definitions.md`,
`examples/project_template/.claude/rules/definitions.md`.

## 13.5 — what the `UnsafePathError` handler does NOT close

Accepted residuals, all documented in the exception's own docstring and in the
KB:

- **`base` remains in two `join_within` messages.** Under the unanonymized-path
  fallback the working directory can itself contain a raw patient id.
- **Worker paths bypass FastAPI entirely.** A `join_within` failure inside a
  TaskIQ task is caught by TaskIQ's receiver and logged with `exc_info=True`,
  which reaches loguru's `diagnose=True` console sink with a traceback and
  therefore with frame locals. Confirmed, not theorised.
- **In-framework broad `except`.** `slicer/context_hydration.py`,
  `schema_hydration.py` and `dicom.py` log with a traceback before the handler
  runs. The mechanism is confirmed; reachability needs project-authored
  hydrator code that touches `Files`, which no built-in hydrator does — so it
  is unverified *in this repo*.
- **Ruled out:** fire-and-forget RecordFlow dispatch. `_execute_action`'s
  blanket `except` logs `logger.error(f"...: {e}")` with no
  `.opt(exception=...)`, so no traceback and no frame locals.

## Residuals accepted outside group 13

- **`clarinet_storage_path` is honoured verbatim when well-formed.** The field
  is *by design* an absolute root disjoint from `settings.storage_path` (pinned
  by three tests, intentional since `2eb2448`), so there is no containment
  check — only "absolute, no `..`". A well-formed hostile absolute path set by
  an admin, or already in the database, is still followed.

## Second review round — findings resolved here

Raised in the PR body as open questions, then confirmed by review and fixed:

| Finding | Resolution |
|---|---|
| The "submitter produced it" rationale for echoing `exc.value` in the 422 is false once `{data.*}` is banned | Value dropped from **both** 422 details; the pinning test inverted to `not in`; four prose sites corrected |
| `{parent_id}.` / `{user_id}.` / `{parent_id}.{user_id}` render to `"."` and 500 at the join | `_reject_vanishing_placeholder_shapes` now checks raw `/`-segments for a bare `.` (pathlib collapses it, so `PurePosixPath(".").name` is `""` and the dot-leading branch never fired) |
| An unrecognised placeholder such as `{studyuid}` survives config load | New `_reject_unknown_placeholders`, with a drift test that rebuilds the catalogue from `fields_from`. **Non-collections only** — a collection substitutes `*` for every placeholder whatever its name, so `slice_{n}.dcm` is a legitimate idiom |
| The skipped-legacy-row residual understates its blast radius | Expanded to all six consequences in CHANGELOG + KB; the WARNING is now emitted once per process per definition instead of once per request |
| Stale docs after the first round's fixes | `@field_validator` → `@model_validator` (KB + `test_file_validation`), the self-contradicting leading-dot paragraph, the CHANGELOG's demo-records no-op, and the collection exemption added to five sites |
| `UnsafePathError` not re-exported from `clarinet.exceptions` | Added to the import block and `__all__` |
| `join_within` has no NUL check | Added — `normpath`/`is_relative_to` pass NUL through, so it previously surfaced as an untyped `ValueError` from a syscall |
| Three copies of the admin predicate | Collapsed into `dependencies.is_admin`; the `"admin" in roles` branch of the storage-path gate now has an integration test (verified to fail when the branch is removed) |
| `_template.py`'s "stdlib only" docstring is false | Corrected — it imports `clarinet.exceptions.domain`, which pulls fastapi (1077 modules measured) |

## Deliberately not changed (owner's ruling)

Scored by review as judgement calls rather than defects; left exactly as
shipped, with the reasoning written up separately for the owner:

- the `_sync_output_files` 422 that lands *after* `update_data` has committed;
- `check-files` answering 500 and 422 for the same underlying condition;
- the duplicate `get_with_relations` round trips between `_validate_output_paths`
  and `_sync_output_files`.


## Third review round (pre-merge, `e1c3ff6..44e9be1`) — BLOCK, then fixed

`pr-diff-reviewer` returned two blockers, both **verified by direct
reproduction** and both introduced by round 2. Each was a new config-load
rejection refusing patterns that resolve correctly today — i.e. an upgrade
turning into a startup abort:

| Pattern | `join_within` resolves to | Round-2 verdict | Now |
|---|---|---|---|
| `./mask.nrrd` | `<base>/mask.nrrd` | REJECT | accept |
| `sub/./mask.nrrd` | `<base>/sub/mask.nrrd` | REJECT | accept |
| `set{1}.nrrd` | `<base>/set{1}.nrrd` | REJECT | accept |
| `f_{0}.nrrd` | `<base>/f_{0}.nrrd` | REJECT | accept |

1. **Bare-`.` check was too broad.** It tested *every* `/`-segment; only the
   **last** can degenerate into the working directory, since `normpath`
   collapses an interior `./` away. Narrowed to `worst.split("/")[-1] == "."`,
   which still rejects all four vanishing shapes.
2. **Allowlist inherited a denylist's parser union.**
   `_reject_unknown_placeholders` copied
   `extract_placeholders(...) | _PLACEHOLDER_RE.findall(...)` from
   `_reject_data_placeholders`. Over-matching is safe for a denylist and unsafe
   for an allowlist: `Formatter().parse` reports numeric fields like `{1}`,
   which the regex never matches and the renderer therefore never substitutes —
   `set{1}.nrrd` renders literally. Now `_PLACEHOLDER_RE` alone; escaped braces
   (`{{studyuid}}`) are still caught, pinned by a dedicated test.

Also fixed from that round:

- **The pre-release audit no longer matches what gets skipped.** A
  `LIKE '%{data.%'` scan misses rows dropped by the two *new* rules, so it could
  report clean while the deployment degrades. CHANGELOG and KB now document
  running every stored pattern through `validate_file_pattern` (passing
  `is_collection`, or collections over-report). **This supersedes the 9.3 gate
  above.**
- **A fourth copy of the admin predicate** survived at `api/routers/sse.py:69`,
  gating admin-only SSE frames — while the new helper's docstring claimed it was
  the single definition. Routed through `is_admin`; `is_admin` added to
  `.claude/rules/api-deps.md`.
- **The NUL guard's stated failure mode was wrong.** `Path.is_file()` returns
  `False` for an embedded NUL on 3.13 rather than raising (verified), so the
  pre-fix symptom was a poisoned row reading as a *missing file*, not an untyped
  `ValueError`. Corrected in the comment, CHANGELOG and test.
- **Round 2's replacement rationale for withholding `exc.value` was itself
  overstated** — "the only fields a pattern can interpolate are stored identity
  fields that masking withholds". `fields_from` returns nine names, and masking
  touches three. The conclusion survives (only `patient_id`'s grammar admits
  `.`/`..`), the reasoning is now accurate. Fixed in `domain.py`,
  `record_service.py` and the KB.
- **Four more stale KB claims**: the stdlib-only `_template` leaf, the
  containment list omitting NUL, "three raise sites" (now four), and a second
  paragraph still saying the 422 surfaces the value.
- **Warn-once key now includes the pattern** — `sync_file_links` reassigns
  `pattern` in place, so keying on the definition name alone would silence the
  warning for an operator who edits a legacy row into a second bad pattern.
- **Test fixture de-duplicated** — `admin_role_client` hand-rolled
  `conftest.create_mock_user_with_role` and skipped its `expunge`, which
  `tests/CLAUDE.md` names as the `MissingGreenlet` cause.

Out-of-scope defects the reviewer surfaced, **not** fixed here and worth issues:
`FileValidator.validate` ignores `multiple=True` so a collection INPUT can never
validate (`file_validation.py:107-108`); `_sha256_safe` TOCTOU
(`_checksums.py:27-31`, previously raised on PR #428).


## Fourth review round (full branch, `bae4a0d..1c2dd1a`) — BLOCK, then fixed

One blocker, and it is the **mirror** of round 3's two. Round 3 found rules that
*refuse* patterns which resolve correctly; this one is an exemption that
*accepts* a pattern which then fails at request time. All three trace to the
same false premise — "a `multiple=True` definition is never rendered".

**Blocker.** `Files.resolve` had no `multiple` branch (unlike
`Files.checksums`), so it rendered collections. `build_slicer_context` loops
`resolve` over the whole registry catching `(KeyError, ValueError)`, and
`UnsafePathError` is deliberately neither — so a *legal* collection pattern
`{study_uid}/x.dcm` on a patient-level record rendered to `/x.dcm`, raised, and
took `POST /slicer/records/{id}/open` down with an uncaught 500 for every user
of that record, instead of degrading to `unresolved`. Reproduced end to end.

Fixed by making `resolve` refuse a collection with `ValueError` (naming
`Files.glob()`), which restores the designed degradation *without* widening the
Slicer handler to swallow `UnsafePathError` — that would have broken the
change's central never-degrade contract. Five existing test mocks needed
`multiple = False` pinned: `MagicMock` had been making the attribute truthy, the
exact pitfall `tests/CLAUDE.md` documents.

**The premise is now stated as an enforced invariant** rather than a passive
fact, in all six places that asserted it, and names the one violation still
open: `FileValidator.validate` renders collections too (issue #562).

Also corrected this round — every one a stale claim, several of them written or
missed by round 3's own repair commit:

| Claim | Was | Is |
|---|---|---|
| `AnonPathError` catch sites | four | five (this branch added `record_service.py:623`) |
| `validate_file_pattern` rule families | two | three |
| `join_within` raise sites naming neither `base` nor `rendered` | "the one remaining" | two |
| `UnsafePathError` raise sites | nine | eight |
| unknown-placeholder rule | "anything else is rejected" | only *name-shaped* placeholders; `set{1}.nrrd` and `seg_{studyuid:s}.nrrd` stay legal (verified) |
| `fields_from` supplies | "only stored identity fields" | nine names including `data`; the ban is on *patterns*, which is what makes the argument hold |
| bare-`.` rule | "all `/`-segments" | last segment only (round 3 narrowed it, docs lagged) |
| warn-once key | `(record type, definition)` | `(record type, definition, pattern)` |
| direct `join_within` callers | two | three (`_resolve_paths_for_file_def`) |
| `_template` leaf | "stdlib-only" in `files/__init__.py` | no longer, and `settings.py` imports it directly |

**Verification.** `make check` clean. `make test-fast`: **3341 passed, 204
skipped, 0 failed**.

*Note on a transient:* an intermediate run showed 22 `@slicer` failures. Verified
**not** caused by this branch — 3D Slicer had been running and stopped mid-run,
so tests that had already cleared the skip probe hit a dead socket (nothing
listening on port 2016). Run standalone they skip identically at the branch base
and on the branch. The clean run above has them skipping.

## Still open

- **9.3 / the pre-release pattern audit** (superseded form: validate every
  stored pattern, not a `{data.%` grep) — must run against deployed instances.
- **Issues #562 and #563** filed for out-of-scope defects found in rounds 3-4.
- Round 4's remaining low-confidence items (19) were reviewed and judged either
  already fixed here or not worth acting on; the substantive ones are in the
  table above.


## Fifth review round (`1c2dd1a..3ff5566`) — BLOCK, then fixed

**The round-4 fix was wrong, on a false premise I introduced.** Round 4's
blocker was real, but the repair rested on believing `build_slicer_context`
*degraded* when `resolve` failed. It does not: `unresolved` is a hard
`raise ScriptArgumentError`, rendered as 422 — semantics that predate this
branch. So making `Files.resolve` refuse collections did not restore a
degradation; it routed **every** collection into that raise.

Net effect of round 4 in isolation: it narrowed one failing shape (a
vanishing-placeholder collection → uncaught 500) and widened failure to *all*
collections → 422 on `POST /slicer/records/{id}/open` and the validator-script
path, where the base returned 200. Reproduced: `slice_{n}.dcm` and `seg_*.nrrd`
build a context fine at `1c2dd1a` and raise at `3ff5566`.

**Correct fix.** The defect was the unfiltered registry loop, not `resolve`:

- `services/slicer/context.py` now skips `fd.multiple` before calling `resolve`.
  A collection never had a meaningful value in that single-path layer — it
  rendered with every placeholder blanked (`slice_.dcm`).
- `Files.resolve`'s guard stays: it is a correct contract check, and the
  docstring no longer claims callers "degrade to unresolved".
- `Files.exists` branches to `glob` for a collection, rather than inheriting the
  raise — the shipped task pattern
  (`examples/project_template/.claude/CLAUDE.md:99`) tells every project to call
  `ctx.files.exists(output_file_def)`.
- `RecordQuery.file_path` converts the `ValueError` into `PipelineStepError`, so
  a collection on the worker path stays classified instead of escaping to TaskIQ
  as an undeclared failure.

**Also fixed from that round:**

| Item | Resolution |
|---|---|
| Four *new* "degrades to `unresolved`" claims written by round 4 | Reworded in `facade.py`, CHANGELOG, KB and the facade test |
| Vacuous test assertion (`not isinstance(exc.value, UnsafePathError)` inside `pytest.raises(ValueError)` can never fail — `UnsafePathError` is not a `ValueError`) | Replaced with `type(exc.value) is ValueError`, and the duplicate test folded in |
| Fourth surviving "stdlib-only" copy at `settings.py:188` — in the very text round 4 wrote naming `settings` as the importer | Corrected |
| `tests/test_path_safety.py:311` still carried the absolute "it never renders" | Reworded to "meant to be globbed", naming #562 as the open violation |
| Shipped downstream API reference (`clarinet agent init/update`) still documented `ctx.files.resolve` as always returning a Path | Both `workflows.md` copies updated |
| Unqualified "safe" claims about the collection exemption | Now say *enforced*, naming #562 |
| Five unqualified copies of the unknown-placeholder rule | Qualified to *name-shaped* placeholders in all five |
| CHANGELOG warn-once claim still said `(record type, definition)` | Now the triple |

**Confirmed correct by the reviewer, no action:** all ten round-4 numbers and
wordings verify at HEAD; the five `multiple = False` mock pins were *required*
(without them `resolve` raises `ValueError`, which `pytest.raises(UnsafePathError)`
cannot catch, so those four path-safety tests would have errored out instead of
exercising their guard) and none was weakened; the `Files.resolve` call-site
enumeration was complete; `FileValidator.validate` is correctly named as the sole
remaining collection renderer.

**Dimensions that ran clean:** past-PR sweep (PR #556 has zero human review
comments; nine merged PRs checked), revert/regression check, and the
import/async/auth/config/migration axes.

## Standing lesson for this branch

Five rounds, three blockers, and every one is the same shape: **a claim about
what the code does, believed without checking, that then licensed a change.**

- Rounds 3-4: "a `multiple=True` definition is never rendered" — false;
  `Files.resolve` and `FileValidator.validate` both rendered them.
- Round 5: "`build_slicer_context` degrades to `unresolved`" — false; it raises.

Neither was visible to the test suite, because no in-tree config declares a
collection. Before changing behaviour on the strength of a sentence in a
docstring or a doc page, read the code it describes.

## Sixth review round (full branch, `bae4a0d..829ac78`) — With fixes, then fixed

Fresh full-range review (superpowers `requesting-code-review`). No Critical
finding and no traversal bypass: the reviewer attempted absolute, `../`,
split `..` + literal `/`, NUL, backslash, coerced-list, empty, `.`, `sub/../x`,
a hostile persisted `RecordFileLink.filename`, a relative or `..`-laden
`clarinet_storage_path`, the `{name}` path parameter of the output-files
endpoint (a registry key, never a path), a `FileDef` handed straight to
`ctx.files.resolve`, and every route that could carry `clarinet_storage_path`.
Windows semantics verified with `PureWindowsPath` + `ntpath.normpath`. ~25 doc
claims checked against HEAD; all but the ones below held.

**Important — the same defect class as rounds 3-5, at the site round 5 named as
"the one violation still open".** `FileValidator.validate` still rendered
collections, and this branch's own `join_within` at its existence probe turned
that render into a raise. A config-load-legal collection whose leading segment
is an optional placeholder — `{study_uid}/slice_{n}.dcm` on a patient-level
record, `{parent_id}/…` on a parentless one, `{user_id}/…` on an unclaimed one
— rendered to `/slice_.dcm` and raised `UnsafePathError` (500) from
`POST /api/records` (after `create_with_relations` had already committed the
row: `pending`, no `created` event, never `blocked`), submit, `check_files`,
`validate-files` and the `preparing→pending` exit. Pre-branch the same input
answered "file not found" → `blocked`. Reproduced for all three placeholders.

Fixed by the reviewer's fallback rather than its primary suggestion: the
primary was to fix #562 here (glob and match), which the branch had deliberately
kept out of scope and which needs a decision about what filename a
`RecordFileLink` records for a collection member. `validate` now branches on
`multiple` before rendering: a required collection is reported `missing`
without a disk probe (the verdict rendering already reached — a wildcard
rendered to nothing), an optional one is skipped. Five tests, RED first
(four `UnsafePathError`s and one render-log assertion), then GREEN.

Claims corrected with it: the `file_validation.py` join comment said a legacy
row could reach the raise — it cannot, `RecordType.file_registry` re-validates
and skips it, so the join is a backstop with no in-tree producer (the test
class docstring said the same and was fixed); CHANGELOG, KB and the
path-safety test docstring no longer name `FileValidator.validate` as the open
renderer; the shipped residual now says what is true — a required collection
INPUT still cannot validate, by design until #562.

**Minor, fixed:** `os.path.isabs` platform claim is Python-3.12-specific —
`ntpath.isabs("/custom/storage")` is `False` on 3.13 (verified on the 3.13.11
venv; CI pins 3.12), so a POSIX-style override row is refused on a 3.13 Windows
host. Qualified in `_resolver.py` and the test, which is now `skipif` on that
combination with the reason spelled out. Spec scenario "Data placeholder
through the API" had no HTTP-level test — added on `POST /api/records/types`.
The `{data.*}` ban is documented as absolute but a `FileDef` passed directly to
`ctx.files.resolve()` never becomes a `FileDefinitionRead` — one KB sentence
says so and why it is scope reduction, not containment. Stale citations:
`services/slicer/context.py:60,282` → `60,294`; the `test_record_service.py`
docstring's `facade.py:203-205` now names the `fd.multiple` branch of
`Files.checksums` instead of a line range; `tasks.md` 2.2 still listed the
dot-leading-basename rejection D4 removed.

**Minor, not fixed (follow-ups):** Python-mode config load surfaces the
rejection as a raw `pydantic.ValidationError` (`python_loader.py` does not
wrap; TOML mode does at `bootstrap.py`) — the definition name only appears in
pydantic's truncated `input_value` dump. Pre-existing for every Python-mode
validation error, and already stated in `validate_file_pattern`'s docstring.
`RecordService.create_record` runs `validate_record_files` after the commit,
so any exception from it leaves a half-initialised record; a future
`PUT /records/{id}` would silently reopen `clarinet_storage_path` because the
admin-only gate is a single route dependency on `POST`.

**Verification (reviewer, at `829ac78`):** targeted suites 444 passed;
`make test-unit` 3330 passed, 9 skipped; `make check` clean. Post-fix, at
`c6ee108`: targeted suites (file_validation, file_repository, record_service,
path_safety, record_crud) 270 passed; `make test-unit` 3336 passed, 9 skipped,
0 failed; `make check` clean.
