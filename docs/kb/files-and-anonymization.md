---
type: Concept
title: Files and the anonymized-path contract
description: Why Files is the only way to turn a record into a path on disk, and why resolvers raise AnonPathError instead of falling back to raw DICOM UIDs.
tags: [files, storage, anonymization, paths, contract]
timestamp: 2026-07-21T19:46:32Z
---

Turning "this record's segmentation mask" into an absolute path is a
surprisingly load-bearing operation: the writer, half a dozen readers, the CLI
and every pipeline task must agree on the answer. Clarinet solves that with one
rendering engine and one public facade.

## `Files` is the entry point for path resolution

`clarinet/files/facade.py`, imported as `from clarinet.files import Files`.
Models carry **no** path logic — `RecordRead`, `StudyRead`, `SeriesRead` and
`PatientRead` have no `working_folder` field and no `_format_path` helpers.

`clarinet.files`'s public surface is six names, all served as lazy
`__getattr__` re-exports (`clarinet/files/__init__.py`) so the stdlib-only
`_template` leaf stays importable from `clarinet.settings` validators without
pulling in the rest of the package graph: `Files`, `AnonPathError`,
`PLACEHOLDER_REGEX`, and three path-safety primitives —
`validate_file_pattern`, `assert_path_safe_value`, `join_within` — covered
below under "Path-safety guards". Those three are meant to be imported
directly, by callers that build their own render-then-join step (e.g.
`services/file_validation.py`) or that validate a pattern outside the
config-load path. Everything else under `clarinet/files/_*` is a private
leaf; never import those directly.

```python
f = Files(record)              # strict: raises AnonPathError when not anonymized
f.dir()                        # working directory at the record's level
f.resolve("mask")              # absolute path for a FileDefinition name

f = Files(record, fallback=True)     # lenient: falls back to raw UIDs
f = Files.for_reader(record)         # same leniency, one call

Files.working_dirs(patient=..., study=..., series=...)   # stateless, all levels
Files.render_for(record, pattern)                        # pattern only
await Files(record).checksums()                          # registered files
await Files.checksum(path)
```

`_storage.render_all_levels` is the single template engine behind all of these,
so a custom `settings.disk_path_template` produces identical paths for the
writer (`AnonymizationService`), every reader (`DicomWebCache`,
`prefetch_dicom_web`), `clarinet anon migrate-paths`, and `ctx.files` inside
pipeline tasks. There is no writer/reader divergence to reason about.

## Safe by default: no silent raw-UID fallback

When `settings.disk_path_template` references an anonymized identifier,
`Files(record)` refuses to satisfy it from the raw DICOM UID: a missing
`anon_uid` / `anon_id` raises `AnonPathError` (re-exported from
`clarinet.exceptions`). Strictness is therefore a property of the **template**,
not of the mode — a template that never references an anonymized identifier
never triggers anon resolution and so can never raise.

The reason is **asymmetric anonymization**: a study can flip from non-anon to
anon mid-pipeline, so a `Record` created before the run still carries
`record.study_anon_uid = None` while `study.anon_uid` is already populated.
Falling back to the raw UID in that window made downstream tasks load the wrong
dataset, or address files the writer no longer produces under that identifier.

Choosing the mode is a **call-site decision, not a property of the entity**. If
you add a resolver call, pick the side first.

| Mode | Where |
|---|---|
| Strict, and lets it propagate | the writer `AnonymizationService._save_series_to_disk`; `ctx.files` in pipeline tasks |
| Strict, but catches `AnonPathError` to degrade | `DicomWebCache` (`services/dicomweb/cache.py`) and `prefetch_dicom_web` (`services/pipeline/tasks/cache_dicomweb.py`) — the cache simply misses; `clarinet anon migrate-paths` (`cli/anon.py`) logs, counts the failure and moves to the next record |
| `Files(record, fallback=True)` | `build_slicer_context` (`services/slicer/context.py`) — Slicer is the UI layer and must open in-flight records |
| `Files.for_reader(record)` | `validate_record_files` (`services/file_validation.py`); `RecordService.check_files` and its checksum collection (`services/record_service.py`) |

`Files.for_reader()` is itself implemented as "try strict, catch, rebuild with
`fallback=True`" (`files/facade.py`), so it is itself the fourth catch site.

Note that **nothing in `clarinet/api/` catches `AnonPathError`** — the axis is
library-internal / service / CLI, not "backend vs UX routers". The leniency
decision is made in the service layer before a router ever sees a path.

### Consequence for workers

`RetryMiddleware` only skips retries for 4xx `ClarinetAPIError`, so an
`AnonPathError` raised in a worker is retried `pipeline_retry_count` times with
exponential backoff before landing in the DLQ. That is usually the right shape:
the race window closes as soon as the anonymization run finishes, so a retry
succeeds. See [Pipeline](./pipeline.md).

## Anonymization surface

Three entry points share one `AnonymizationService` for the raw DICOM work:

| Entry point | Record bookkeeping |
|---|---|
| `AnonymizationService` (`AnonymizationServiceDep`) | none — raw mode, backwards compatible |
| `AnonymizationOrchestrator` | skip-guard, idempotent patient anonymization, submits results to the tracking Record; on **any** unhandled exception it marks the Record `failed` and re-raises so retry/DLQ middleware still see it |
| `anonymize_study_pipeline` | the built-in `@pipeline_task` that runs the orchestrator with the worker's client; downstream wraps it via `run_anonymization(msg, ctx, extra_record_data=...)` |

Skip-guard policy: skip when `study.anon_uid` is set **and** the previous Record
data has no `error` **and** (it was already sent to PACS or this run is not
sending). A re-run is always allowed after an error or when the run upgrades to
send-to-PACS.

**Series-subset runs bypass the guard entirely** — a study-granular `anon_uid`
cannot prove the requested series were processed. A subset run still persists
`anon_uid` (masking, viewer and path resolution depend on it) but records
`series_uids` in the Record data, which keeps the guard treating it as not-done
so a later whole-study run is not wrongly skipped. `series_uids` is therefore a
reserved Record-data key, stripped from `extra_record_data` on whole-study runs.
An empty, unknown or filter-excluded subset raises `AnonymizationFailedError`
naming each offending UID — a subset request is never silently narrowed.

Multi-PACS fan-out goes through `settings.anon_extra_pacs_nodes` (or
`extra_pacs=[DicomNode(...)]`), wired on every construction path. `pacs` keeps
its dual role as C-GET source and first destination; extras are store-only.
Per-node failure counts land in `AnonymizationResult.send_failed_by_node`, and
with `anon_fail_on_send_error=True` any send failure raises
`AnonymizationSendError` **before** `study.anon_uid` persists, so a retry redoes
the run cleanly.

## File definitions

What a name like `"mask"` means is declared by the project's file registry —
`FileDefinition` rows linked to record types and records. That side of the
system is covered in [Domain model](./domain-model.md) and, in detail, in
`.claude/rules/file-registry.md`.

## Path-safety guards

A `FileDefinition.pattern` renders against DICOM identifiers and other
per-record fields, then the rendered name is joined onto the record's
working directory. The render context still merges in `record.data`, but a
pattern may no longer reference it — see "Config-load pattern validation"
below. Both steps are guarded, and — like the anonymized-path strictness
above — neither guard alone would be enough: an admin-authored `/` in a
subdirectory pattern (`{study_uid}/mask.nrrd`) must stay legal, so the join
can't reject every `/`; a value guard alone can't see what a literal pattern
segment contributes.

### Config-load pattern validation

`validate_file_pattern` (`clarinet/files/_template.py`, attached as a
`@field_validator("pattern")` on `FileDefinitionRead` — **not** on
`FileDefinition`, which is `table=True` and skips Pydantic validation)
rejects an unsafe pattern before any record ever renders it: empty or
whitespace-only, a backslash, NUL, a `..` component, or a dot-leading
basename in the pattern's *literal* text (placeholders masked out first, so
`{study_uid}/mask.nrrd` stays legal) — plus two checks against the *full*
pattern regardless of placeholders: an absolute prefix and a trailing
separator. This rule is permanent, and a rejection here — unlike the
stored-row case below — aborts startup: the process does not come up with
the offending definition simply skipped.

**Temporarily banned, tracked by
[#552](https://github.com/radionest/clarinet/issues/552):** a pattern
containing `{data.FIELD}` or bare `{data}` is rejected at the same
config-load point — `record.data` is user-submitted and, at best,
JSON-Schema validated, never with filesystem safety in mind. The ban is
reversible in one commit once the non-traversal corner cases it defers
(length limits, Windows-reserved basenames, unicode normalization,
`record.data`'s mutability) are settled; `{id}`, `{parent_id}` and
`{user_id}` are safe replacements today.

Because a `FileDefinition` row is never re-validated once stored (`table=True`
skips Pydantic), a row written before this validator existed can still carry
an unsafe pattern. `RecordType.file_registry` catches that at read time: a
stored definition that fails `FileDefinitionRead` construction is skipped
from the registry — logged as a WARNING naming the record type and
definition — rather than raising and nulling the entire registry for every
other file on that record type. The trade-off: if the skipped definition was
`required=True` with `role=INPUT`, its "required file missing" check
silently stops firing, because `services/file_validation.py` only inspects
definitions present in `file_registry` — a record can no longer be marked
`blocked` for that file until an operator fixes the row's pattern.

### The substituted value

`render_template(..., path_safe=True)` runs `assert_path_safe_value` on
every *coerced* placeholder value (so a list that flattens to `"a/b"` is
still caught). It rejects a value containing `/`, `\` or NUL, and separately
rejects a value that is exactly `.` or `..` — it does not reject a leading
dot in general (`mask.seg.nrrd` stays fine; that check lives at the join,
below, and is basename-level). `Files.resolve`, `Files.render`,
`Files.render_for` and `Files.checksums` all render with `path_safe=True`.
`Files.render_template` — the static renderer feeding Slicer script
arguments (`services/slicer/context.py`) — deliberately does **not**: those
arguments may legitimately be absolute paths, and the result never feeds a
working-directory join.

### The joined path

`join_within(base, rendered)` (`clarinet/files/_template.py`, re-exported
from `clarinet.files`) checks the assembled path: not outside `base`, not
equal to `base` itself (LENIENT rendering can flatten a whole-pattern
placeholder to `""`), no `..` path component, and a basename that doesn't
start with a dot. It is **purely lexical** — `os.path.normpath` plus
`Path.is_relative_to`, zero filesystem access — which is what lets
`Files.resolve` stay synchronous inside `build_slicer_context`'s
per-file-definition loop. `Files.resolve` and `Files.checksums` call it
internally right after rendering; `FileValidator.validate`
(`services/file_validation.py`) and the persisted-filename replay in
`services/pipeline/context.py` call it directly on a name they already have
in hand (a rendered pattern, or a stored `RecordFileLink.filename`).

Being lexical, `join_within`'s containment proof is only as trustworthy as
its own `base` argument — a relative or `..`-carrying base satisfies
`is_relative_to(base)` for every filename joined onto it, making the proof
vacuous. See "The storage-path override" below for the one caller-influenced
base in the system, and how it stays trustworthy without that check.

`join_within` is **not** symlink-aware — no `Path.resolve()`, by design, to
keep `Files.resolve` synchronous. It is not the last line of defense
everywhere; see the next section.

### The symlink-aware layer at the delete and serve sinks

`_filter_in_sandbox` (`services/record_service.py`) is a second, independent
guard — unchanged by this change — at the three sinks where a symlink could
matter: `resolve_output_file`, `clear_output_files` and
`delete_record_cascade` (the latter two share `_collect_output_file_paths`;
all three funnel through `_resolve_paths_for_file_def`). Unlike
`join_within`, it calls `Path.resolve()` — a real filesystem syscall that
chases symlinks — and drops any candidate whose resolved location escapes
the resolved sandbox. `join_within` and `_filter_in_sandbox` are
complementary, not redundant: the former is cheap enough to run inside
`Files.resolve`'s hot, synchronous, per-file-definition loop; the latter is
only affordable at the handful of sinks that already pay for a filesystem
round trip.

### Where `UnsafePathError` surfaces

- **Record submit** (`RecordService._validate_output_paths`, run before the
  submission persists, and `_sync_output_files`'s post-scan reconciliation
  backstop): translated to `422` — a user's own submitted data produced the
  unsafe value, so on the principle that a violation surfaces according to
  who caused it, it is that user's problem to fix. The 422 detail
  deliberately **does** include the offending value (`exc.value!r`) — the
  submitter already has it, they produced it — but it never reaches a log.
- **Everywhere else a router lets it propagate** (the Slicer context
  builder, `resolve_output_file`, …): `500`, via `UnsafePathError`'s own
  dedicated handler (`handle_unsafe_path_error`, `exception_handlers.py`)
  — registered separately from the generic `ConfigurationError` handler so
  it can log without a traceback (see "Never log the value" below). An
  administrator's bad pattern, or a legacy poisoned row, is a server-side
  problem, not a per-request one.
- **Pipeline tasks**: `UnsafePathError` is a `ConfigurationError`, not a
  `ClarinetAPIError`, so `RetryMiddleware` does not special-case it the way
  it special-cases a 4xx API error — it is retried `pipeline_retry_count`
  times with backoff and then lands in the DLQ, the same shape as
  `AnonPathError` above.

**Never log the value.** `assert_path_safe_value`'s two raise sites name the
offending placeholder key in the message. Three of `join_within`'s five
raise sites name the working directory (`base`) instead (the other two — the
`..`-component and dot-leading-basename checks — name neither) — which,
under `Files.for_reader` / `fallback=True`, can itself be a path built from
the record's **raw**, not-yet-anonymized patient id. That message is
logged either way it propagates, but keeping the *substituted value* itself
out of every sink took a dedicated fix, not just
`str(exc)`'s own omission of it. `UnsafePathError` has its own handler
(`handle_unsafe_path_error`, `exception_handlers.py`), which Starlette
dispatches ahead of the generic `ConfigurationError` handler whenever the
exception is this specific subclass — handler lookup walks the exception's
own MRO, so the specific registration wins regardless of registration
order. It logs `str(exc)` plus the request method/path via a plain
`logger.error(...)` — deliberately **not** `.opt(exception=exc)`, so no
traceback is ever rendered. That distinction is the fix: the generic
handler's `logger.opt(exception=exc).error(...)` *does* render a full
traceback, and this project's **stderr console sink runs with
`diagnose=True` unconditionally**, which prints **frame locals** into that
traceback — including `assert_path_safe_value`'s own `value` parameter —
regardless of what the message itself says. (The file sink is
conditional: `diagnose=not serialize` — off, and safe, in JSON mode, where
an exception instead renders through a plain `traceback.format_exception`
call with no frame locals; the Loki sink always formats that same way and
is never exposed.) Before this handler existed, every `UnsafePathError`
that reached a router uncaught leaked the raw value to stderr on every
deployment; `str(exc)` omitting the value was never sufficient on its own.

This closes it for an `UnsafePathError` that reaches FastAPI's exception
handlers **uncaught** — not simply "any request on a FastAPI route": a
broad `except Exception:` sitting between the raise and the router can
still swallow it and log a traceback of its own before either handler
ever runs. Two in-framework sites do exactly that, both on genuine
request paths: `services/slicer/context_hydration.py:120-121` — directly
on `POST /slicer/records/{id}/open`, the very endpoint the "Slicer context
builder" mention above names as an `UnsafePathError` source — and
`services/schema_hydration.py:227-228`. Each catches broadly and logs with
`logger.exception(...)`, which renders a traceback the same way the old
generic handler did. Reaching either with an `UnsafePathError` needs
project-authored callback code — a registered hydrator — that itself
touches `Files`/`join_within`: no such code exists in this framework repo
today, the same evidentiary status as the `.call()` residual below, not a
confirmed leak either.

Where nothing intercepts it first, the substituted value itself never
lands in a message or a log: it travels only on `exc.value` (and
`exc.metadata()`), which the record-submit 422 body above deliberately
surfaces to the submitter but which no log statement reads. Two more
paths stay open:

- **Pipeline and worker code — confirmed, not hypothetical.** The
  built-in `convert_series_to_nifti` task calls
  `ctx.files.resolve(VOLUME_NIFTI)`
  (`services/pipeline/tasks/convert_series.py:60`), reaching `join_within`
  inside the TaskIQ worker process with no project code involved, entirely
  outside any ASGI request — so neither exception handler above ever
  runs. `services/pipeline/task.py:118-120` logs, then re-raises; TaskIQ's own
  executor — an installed dependency, not clarinet code — catches
  `BaseException` and logs it with `exc_info=True` through the **stdlib**
  `logging` module (`taskiq/receiver/receiver.py:26,282-286`). Clarinet's
  `InterceptHandler` (`utils/logger.py:189-214`) redirects all stdlib
  logging into loguru — installed both at import (`utils/logger.py:392`)
  and again by `reconfigure_for_worker` (`:383`, called at worker
  startup) — forwarding `exc_info` into `.opt(exception=...)` exactly like
  the generic handler used to. The worker's stderr sink runs with the
  same unconditional `diagnose=True`. Frame locals — hence `exc.value` —
  are rendered there every time.
- **Unverified — not a confirmed leak.** Anything that escapes Starlette's
  `ExceptionMiddleware` entirely (fire-and-forget `RecordFlowEngine.fire` —
  see `services/recordflow/engine.py:120-128` — ASGI middleware,
  background tasks) is picked up the same way, through `InterceptHandler`.
  Whether `UnsafePathError` actually reaches this path is not established
  — it would need a project-authored `.call()` callback that touches
  `Files` inside an entity flow fired via `engine.fire()`.

`record.data` may carry PHI that log scrubbing does not redact
(`.claude/rules/logging-pii.md`); the working-directory path under
unanonymized fallback (`base`, embedded directly in `str(exc)`) remains a
real, open exposure everywhere this section's guard does not reach.

### The storage-path override

`Record.clarinet_storage_path` lets an admin redirect one record's entire
working directory to an arbitrary absolute path — a real per-record
override, exercised across the integration and unit test suites, not dead
code. `_resolve_storage_base` (`clarinet/files/_resolver.py`) has exactly one
caller — `build_working_dirs`, reached whenever a `Files` instance is
constructed for a `RecordRead` — and requires the value to be absolute and
already normalized, raising `UnsafePathError` otherwise; this is what keeps
`join_within`'s containment proof non-vacuous. The Slicer context builder
reads `record.clarinet_storage_path` **raw**, twice
(`services/slicer/context.py:60,282`) — it is protected only by ordering,
not by a guard of its own: both raw reads happen after `build_slicer_context`
already constructs `Files(record, ..., fallback=True)` (`context.py:170`),
which raises first if the value is malformed. Reordering that construction
later would silently drop this protection. `_resolve_storage_base`
**deliberately does not** require containment under `settings.storage_path`:
the field is *by design* a storage root disjoint from `settings.storage_path`,
not a subdirectory
selector, so a well-formed absolute path set by an admin — or already
present in the database — is still honoured verbatim.
`check_storage_path_admin_only` (`api/routers/record.py`) narrows *who* can
reach that capability: a non-admin supplying a non-`None` value is rejected
(403) at record creation. The combination is a deliberate, accepted
residual, not an oversight — see the CHANGELOG entry for this release.
