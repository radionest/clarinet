## 1. Exception type

- [x] 1.1 Add `UnsafePathError` to `clarinet/exceptions/domain.py`, subclassing `ConfigurationError` directly. Docstring must state that it is a sibling of `AnonPathError` and deliberately **not** a subclass, naming the four sites that degrade on `AnonPathError` (`files/facade.py` `for_reader`, `services/dicomweb/cache.py`, `services/pipeline/tasks/cache_dicomweb.py`, `cli/anon.py`)
- [x] 1.2 Add a regression test asserting `not issubclass(UnsafePathError, AnonPathError)` — the guard against a future refactor collapsing the two

## 2. Path-safety primitives (`clarinet/files/_template.py`)

Keep the module stdlib-only; it is imported by `Settings` validators and must not acquire package dependencies.

- [x] 2.1 `assert_path_safe_value(key: str, value: str) -> None` — reject `/`, `\`, NUL anywhere in the value, and a whole value of `.` or `..`. Raise `UnsafePathError` naming *key* and the reason
- [x] 2.2 `join_within(base: Path, rendered: str) -> Path` — `os.path.normpath` plus prefix comparison, no filesystem access. Reject a result outside *base*, equal to *base*, carrying a `..` component, or holding a NUL byte. AMENDED (D4): the dot-leading-basename rejection was REMOVED from the join — it is what an absent optional placeholder renders to — and moved to `validate_file_pattern`'s worst-case render rule at config load
- [x] 2.3 `_reject_data_placeholders(pattern: str) -> None` — reject a placeholder set containing `data` or any `data.*` name, via `extract_placeholders`. Error names the placeholder and suggests `{id}` / `{parent_id}` / `{user_id}`. Docstring names issue #552 and states that this rule is temporary while the literal-text rule in 2.4 is permanent
- [x] 2.4 `validate_file_pattern(pattern: str) -> str` — call `_reject_data_placeholders`, then mask placeholders out and reject the remaining literal text on absolute prefix, `..` component, backslash, NUL, empty/whitespace-only, trailing separator, dot-leading basename. A literal `/` mid-pattern stays legal. Raise `ValueError` (not `UnsafePathError`) to match `validate_name_is_identifier` on the same models → 422 on the API path, `ConfigLoadError` on the config path
- [x] 2.5 Add `path_safe: bool = False` to `render_template`, calling 2.1 on each coerced value inside the existing `_replace` closure — one substitution point, so no render path can bypass it
- [x] 2.6 Export `validate_file_pattern`, `assert_path_safe_value` and `join_within` from `clarinet/files/__init__.py` through the existing lazy `__getattr__` (same treatment as `PLACEHOLDER_REGEX`), so no caller imports the private leaf

## 3. Wire the render→join sites (`clarinet/files/facade.py`)

- [x] 3.1 `resolve()` — `path_safe=True` on the render, `join_within` on the working-dir join
- [x] 3.2 `checksums()` singular branch — same pair; this is a second `working_dir / filename` join and a live bypass if missed
- [x] 3.3 `render()` and the static `render_for()` — `path_safe=True` only; they return a `str` with no base to join against
- [x] 3.4 Confirm `exists()` inherits the guard via `resolve()` and needs no separate change; add a test pinning that
- [x] 3.5 Leave the static `render_template()` unguarded (`path_safe=False`) — Slicer script args may be absolute paths. Add a comment at the call site in `clarinet/services/slicer/context.py` recording that this is deliberate, so a later reader does not "fix" it

## 4. Persisted-filename replay

- [x] 4.1 `clarinet/services/pipeline/context.py` — apply `join_within` to the `f.dir(level) / link.filename` join. Nothing is rendered here, so no value guard applies

## 5. Config-load validator (`clarinet/models/file_schema.py`)

- [x] 5.1 Attach `@field_validator("pattern")` calling `validate_file_pattern` to `FileDefinitionRead` **only**. Do not add one to `FileDefinition`: it is `table=True`, so SQLModel skips validation there (its existing `validate_name_is_identifier` never fires either). Add a comment saying so
- [x] 5.2 Verify both config entry points are covered — `clarinet/config/primitives.py` (`FileDef`) and `clarinet/utils/file_registry_resolver.py` (`FileRegistryEntry`) both construct `FileDefinitionRead`

## 6. Error surfacing and logging

- [x] 6.1 `clarinet/services/record_service.py::_missing_output_links` — catch `UnsafePathError` and re-raise `UNPROCESSABLE_ENTITY` from `clarinet.exceptions.http`, so a user's own bad data is a 422 rather than a 500
- [x] 6.2 Emit one WARNING before raising, carrying record id, file-definition name, offending placeholder key and violation reason — **never the value** (`record.data` may carry PHI; see `.claude/rules/logging-pii.md`). The value goes in the exception message only
- [x] 6.3 Leave every other site unhandled: a `ClarinetError` reaching a router is a 500, a pipeline task failure follows the normal retry/DLQ contract

## 7. Tests

- [x] 7.1 New `tests/test_path_safety.py` — pure unit tests for the three primitives, stdlib-only, mirroring `tests/test_path_template_renderer.py` and `tests/test_path_uniqueness.py`
- [x] 7.2 Drive the `{data.*}` rejection cases from a single module-level `BANNED_DATA_PATTERNS` list, so un-banning under #552 moves strings between two lists
- [x] 7.3 Value-guard coverage: absolute absorption (`/etc/passwd`), relative traversal (`../../etc/passwd`), the split form where the value is `..` and the pattern supplies the literal `/`, NUL byte, backslash on POSIX, and a coerced collection joining to `a/b`
- [x] 7.4 Containment coverage: empty render under LENIENT making the joined path equal the base; `checksums()` and `exists()` refusing an escaping name
- [x] 7.5 Config-load rejection through both paths — extend `tests/test_config_primitives.py` (`FileDef`) and `tests/test_file_registry_resolver.py` (`FileRegistryEntry`) — plus the API 422 path
- [x] 7.6 Literal-text coverage: `/abs/mask.nrrd`, `../x.nrrd`, trailing separator, `.hidden.nrrd`
- [x] 7.7 Proof the ban alone is insufficient: `patient_id = ".."` is permitted by `PATIENT_ID_REGEX`, and `Files.resolve` must still reject it
- [x] 7.8 Persisted-filename replay: a stored `RecordFileLink.filename` of `../../etc/passwd` is refused at read time
- [x] 7.9 Regressions that must keep working: `study_{study_uid}/mask.nrrd` resolving into a subdirectory (AMENDED mid-execution: the bare `{study_uid}/mask.nrrd` form is now REJECTED at config load by the vanishing-placeholder rule -- a patient-level record renders it to `/mask.nrrd`; pinned as rejected in `tests/test_path_safety.py`), `seg_{id}.seg.nrrd` and `mask.seg.nrrd` rendering, and `Files.render_template` still accepting an absolute path for Slicer script args
- [x] 7.10 Update the two tests broken by the ban — `tests/test_file_validation.py` (builds a `FileDefinitionRead`) and `tests/test_path_uniqueness.py` (builds a `RecordTypeCreate`) — each with a `# see #552` comment at the changed line. The nine other `{data.*}` hits are raw `render_template` calls and need no change
- [x] 7.11 Check `tests/test_path_template_renderer.py` and `tests/test_storage_paths.py` for message-match regressions — both assert `_safe_render`'s error strings

## 8. Documentation

- [x] 8.1 CHANGELOG — breaking-change entry for the `{data.*}` ban with migration instructions (`{id}` / `{parent_id}` / `{user_id}`, or one `FileDefinition` per variant), referencing #552; plus a line noting that pre-existing traversal filenames are now refused at read time
- [x] 8.2 `clarinet/models/file_schema.py` `pattern` docstring — mark `{data.FIELD}` temporarily rejected, see #552
- [x] 8.3 `.claude/rules/file-registry.md` and its byte-identical mirror `clarinet/docs/file-registry.md` — same wording, keep the two in sync
- [x] 8.4 `clarinet/docs/agent/claude/definitions.md` and `workflows.md` — same wording
- [x] 8.5 `examples/project_template/.claude/rules/definitions.md` (two sites) and `workflows.md` — the second `definitions.md` site is the `allow_path_collision` row citing `{data.FIELD}` as its motivating example, which needs a **different example**, not a temporary-rejection note
- [x] 8.6 `docs/kb/files-and-anonymization.md` — new section covering both the runtime guard and the ban; this page has no existing `{data.FIELD}` mention

## 9. Verification

- [x] 9.1 `./scripts/run_tests.sh -k "path_safety or files_facade or file_registry or path_uniqueness or file_validation" -q` before `make check` — a type/lint pass does not catch behavioural regressions in tests that mocked the old contract
- [x] 9.2 `make check` clean
- [ ] 9.3 (STILL OPEN — release gate, see verify.md) Resolve the open THEORY before release: run `SELECT name, pattern FROM filedefinition WHERE pattern LIKE '%{data.%'` against each deployed instance. Any hit needs the CHANGELOG migration applied by hand and its on-disk files renamed. Record the outcome in `verify.md`
- [x] 9.4 Confirm #552 is referenced from every doc site written in group 8 and from `_reject_data_placeholders`, so the reversal has a single discoverable entry point

## 12. Remaining containment gaps (added mid-execution, found during task 5)

Execute after group 6 and before group 8 (documentation), so the docs cover it.

- [x] 12.1 `clarinet/services/file_validation.py:110` — apply `join_within` to the `(target_dir / resolved)` existence probe. `render_for` already carries the value guard after 3.3, but the join is unchecked; `design.md`'s sink table names this file while the plan's File Structure table omits it. Residual vector as first written: an escaping pattern *literal* from a `filedefinition` row that D7 never re-validates. SUPERSEDED: `RecordType.file_registry` now re-validates every stored row on read and skips a failing one, so no in-tree producer hands `FileValidator` an unvalidated definition — the join is a backstop. AMENDED in review round 6: collections (`multiple=True`) are no longer rendered here at all; a required one is reported missing without a disk probe (glob matching is #562), because rendering a config-load-legal `{study_uid}/slice_{n}.dcm` for a patient-level record produced `/slice_.dcm` and a 500 at this very join
- [x] 12.2 `clarinet/files/_resolver.py:87` — `base = record.clarinet_storage_path or settings.storage_path` makes the containment base caller-chosen: the field is on `RecordBase`, inherited by `RecordCreate`, set through `POST /api/records`, and never reset by `RecordService.create_record`. Reject a value that is not absolute or not normalized, enforced at the resolver so every consumer and every existing row is covered. AMENDED after 12.4: the `not under settings.storage_path` half was DROPPED -- 12.4 found legitimate disjoint roots pinned by three test modules, so the field is an admin-only storage-root override, not a subdirectory selector. Accepted residual, documented in CHANGELOG
- [x] 12.3 Restrict who may set the field at the API boundary. AMENDED: shipped as the route dependency `check_storage_path_admin_only` returning **403** on `POST /api/records`, NOT as a `RecordCreate` field validator returning 422 -- the rule is an authorization question (who may choose a root), not a well-formedness question, and well-formedness is already enforced at the resolver by 12.2
- [x] 12.4 Preserve the legitimate per-record override: 40 references across 8 test modules including `tests/integration/test_record_working_folder.py`. Investigate before implementing; if any legitimate use points outside `settings.storage_path`, stop and report rather than breaking it

## 13. `UnsafePathError` logging exposure (added mid-execution, human ruling #5)

Found by review during group 8. `UnsafePathError` omits the substituted value
from `str(exc)` by design, but it subclasses `ConfigurationError` and so reached
a handler using `logger.opt(exception=exc)`; loguru's `diagnose=True` renders
**frame locals**, putting `exc.value` — and a `base` path derived from a raw
patient id — onto the console and plaintext-file sinks. Reproduced, not theorised.

Rejected alternatives, recorded so they are not re-proposed: accept-and-document
(leaves the group-1 PHI ruling's intent defeated); `diagnose=False` repo-wide
(closes every frame-locals leak but is a shared-infrastructure change outside
this change's mandate).

- [x] 13.1 Dedicated `UnsafePathError` handler in `clarinet/api/exception_handlers.py`, logging without
      `logger.opt(exception=...)` so no traceback — and therefore no frame locals — is rendered. Relies on
      Starlette resolving handlers by walking `type(exc).__mro__`, so the specific registration beats the
      inherited `ConfigurationError` one
- [x] 13.2 Preserve the generic `ConfigurationError` handler's status code and response body shape exactly
      — this changes logging, not API behaviour
- [x] 13.3 Leave the 422 submit path untouched: `record_service.py` catches `UnsafePathError` before any
      handler sees it. (Superseded in review round 2: that 422 no longer echoes the value at all — the
      "submitter produced it" premise is false once `{data.*}` is banned. See verify.md)
- [x] 13.4 Tests: RED proving the value leaks pre-fix via the diagnose-enabled sink specifically (a
      `format_exception`-based sink would prove nothing — those never rendered locals), GREEN proving the
      message survives and the value does not
- [x] 13.5 Record what this does NOT close, as a handler comment and in `verify.md`: `base` remains in the
      message (accepted residual); and pipeline/worker paths never pass through FastAPI exception handlers
      at all, so a `join_within` failure inside a TaskIQ task is still rendered by that stack
