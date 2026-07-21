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

## `Files` is the only entry point

`clarinet/files/facade.py`, imported as `from clarinet.files import Files`.
Models carry **no** path logic — `RecordRead`, `StudyRead`, `SeriesRead` and
`PatientRead` have no `working_folder` field and no `_format_path` helpers. The
implementation lives behind private `clarinet/files/_*` leaves; never import
those directly.

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
succeeds. See [Pipeline](/pipeline.md).

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
system is covered in [Domain model](/domain-model.md) and, in detail, in
`.claude/rules/file-registry.md`.
