# DICOM Service

Deep reference: [Imaging stack](../../../docs/kb/imaging-stack.md), [Files and the anonymized-path contract](../../../docs/kb/files-and-anonymization.md).

Async DICOM client for Query/Retrieve operations against external PACS servers (e.g. Orthanc).

The DIMSE core — SCU, Storage SCP, presentation contexts, C-FIND result
mapping — is the [`dimsechord`](https://pypi.org/project/dimsechord/) package.
This directory holds only what is Clarinet's: the retrieve-mode dispatch, the
SCP lifecycle, anonymization, and the series filter.

## Architecture

```
dicom/
  models.py         # Clarinet models (anonymization, PACS import) + dimsechord re-exports
  client.py         # DicomClient — dimsechord's SCU plus dicom_retrieve_mode dispatch
  scp.py            # Storage SCP singleton (dimsechord.StorageSCP) lifecycle
  anonymizer.py     # Anonymizer, PACS stubs (planned; not yet exported)
  series_filter.py  # Configurable series filter (modality blocklist, instance count, unknown policy)
  orchestrator.py   # AnonymizationOrchestrator — Record-aware skip-guard + Patient + submit
  pipeline.py       # Built-in @pipeline_task anonymize_study_pipeline + run_anonymization helper
  tasks.py          # create_anonymization_service factory (raw, no Record bookkeeping)
  __init__.py       # Public API re-exports
```

- `DicomClient` is the main entry point — all methods are async
- Generic Q/R models (`DicomNode`, queries, `*Result`, `RetrieveResult`,
  `BatchStoreResult`) are dimsechord dataclasses, re-exported from `models.py`
  so `from clarinet.services.dicom.models import ...` keeps working. They are
  **not** Pydantic models — no `.model_dump()`, and construction is keyword-only

## Retrieve modes

`dicom_retrieve_mode` — not the call site — picks the transport, so the same
`get_study` / `get_series` / `get_*_to_memory` calls work against a PACS that
offers C-GET and one that offers only C-MOVE:

| Mode | Transport | Needs |
|---|---|---|
| `c-move` (default), `c-move-study` | C-MOVE-to-self via `client._retrieve_via_move` | a running Storage SCP; the peer must route `dicom_aet` back to `dicom_ip:dicom_port` |
| `c-get`, `c-get-study` | dimsechord's C-GET | nothing beyond an outbound association |

**Why c-move is the default.** An association carries at most 128 presentation
contexts, and the two paths spend that budget differently. The C-GET SCU must
*propose* storage contexts, so it negotiates dimsechord's curated image classes
across their compressed transfer syntaxes — broad on syntax, narrow on SOP
class. The Storage SCP only *accepts*, matching against whatever the peer
proposes, so it supports every storage class and every transfer syntax with no
budget at all. Where the PACS can reach us, c-move is the path that never
silently drops an unusual modality. Where it cannot — no inbound route, no AET
registration — set `c-get` and check that your modalities are in dimsechord's
`DEFAULT_IMAGE_STORAGE_CLASSES` / `DEFAULT_OTHER_STORAGE_CLASSES`.

### Listener ownership

A listening port belongs to one process, and the PACS routes C-MOVE by
destination AET to a host and port it was configured with. Both consequences
are the operator's to resolve, so `storage_scp_wanted()` (in `scp.py`) is the
single place that decides, and both the API lifespan and `clarinet worker`
call it:

- **One process retrieving** — nothing to configure. It binds `dicom_aet` on
  `dicom_port`; register that pair on the PACS.
- **Several retrieving on one host** — each needs its own registered
  `(AET, port)`. `clarinet worker --dicom AET:PORT` sets both (and forces
  c-move) for a worker.
- **A process that must not retrieve** — `dicom_scp_enabled=false`. It binds
  nothing; a C-MOVE retrieve from it then raises a `RuntimeError` naming the
  AET and port that would have to be registered.

A bind collision raises at startup with the port, the AET and those three ways
out. `start_storage_scp` deliberately does **not** fall back to a free port: the
PACS was never told to route there, so the listener would receive nothing and
every retrieve would time out instead of failing.

Under c-move the client registers a **collect** session on the SCP singleton,
runs `move_study`/`move_series` with `settings.dicom_aet` as the destination,
then waits for the instances to physically arrive — the peer's sub-operation
tally is not proof of arrival, so `num_completed` and `instances` come from the
session and a shortfall sets `status="timeout"`. `dicom_cmove_timeout` bounds
the move and the arrival wait together. The `-study` suffix does not change the
transport; it is read by the DICOMweb cache and the Slicer helper to batch at
study level.

Progress under c-move is sampled from the receiving session (dimsechord's
`move_*` exposes no per-sub-operation hook), so `on_progress` reports arrivals
with `total=None` until the move returns.

## Settings (`clarinet/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `dicom_aet` | `CLARINET` | Local AE title |
| `dicom_port` | `11112` | Local DICOM port |
| `dicom_ip` | `None` | Local DICOM IP |
| `dicom_max_pdu` | `16384` | Maximum PDU size |
| `dicom_max_concurrent_associations` | `8` | Global semaphore limit for concurrent DICOM associations |
| `dicom_retrieve_mode` | `c-move` | `c-get` / `c-get-study` / `c-move` / `c-move-study` — see Retrieve modes below |
| `dicom_cmove_timeout` | `300.0` | Seconds bounding the C-MOVE *and* the wait for its instances to arrive |
| `dicom_scp_enabled` | `None` | `None` = own a listener when the mode is c-move; `false` = never (this process must not retrieve via C-MOVE); `true` = always |
| `pacs_aet` | `ORTHANC` | Remote PACS AE title |
| `pacs_host` | `localhost` | Remote PACS host |
| `pacs_port` | `4242` | Remote PACS port |
| `anon_extra_pacs_nodes` | `[]` | Extra C-STORE destinations for anonymized instances (TOML `[[anon_extra_pacs_nodes]]` tables with aet/host/port; env as JSON string) |
| `anon_fail_on_send_error` | `False` | Raise `AnonymizationSendError` on any C-STORE failure, before the study anon_uid persists |

Env vars use `CLARINET_` prefix (e.g. `CLARINET_PACS_HOST`).

## Test PACS (Orthanc)

- Host: `localhost` by default; override via `CLARINET_TEST_PACS_HOST` (see `tests/config.py` and `.env.test.example`)
- DICOM port: `4242`, AET: `ORTHANC`
- REST API: `http://<host>:8042` (no auth)
- All operations allowed: C-ECHO, C-FIND, C-GET, C-MOVE, C-STORE

## Usage

```python
from clarinet.services.dicom import (
    DicomClient, DicomNode, StudyQuery, SeriesQuery,
    PacsImportRequest, PacsStudyWithSeries, RetrieveResult,
)
from clarinet.settings import settings

client = DicomClient(calling_aet=settings.dicom_aet, max_pdu=settings.dicom_max_pdu)
pacs = DicomNode(aet=settings.pacs_aet, host=settings.pacs_host, port=settings.pacs_port)

studies = await client.find_studies(query=StudyQuery(patient_id="12345"), peer=pacs)
result = await client.get_study(study_uid=studies[0].study_instance_uid, peer=pacs, output_dir=Path("./out"))
```

## Series Filter

`SeriesFilter` excludes non-image series (SR, KO, PR, etc.) at import and/or anonymization time.
- Pure logic, no I/O — operates on `SeriesFilterCriteria` DTO
- `SeriesFilterCriteria.from_series_result()` for import time (PACS C-FIND data)
- `SeriesFilterCriteria.from_series()` for anonymization time (DB model)
- Settings: `series_filter_excluded_modalities`, `series_filter_min_instance_count`, `series_filter_unknown_modality_policy`, `series_filter_on_import`

## Batch C-STORE

`store_instances_batch` sends multiple datasets over a single DICOM association (vs `store_instance` which opens one association per dataset).

- **dimsechord**: `DicomClient.store_instances_batch(datasets, peer)` → `BatchStoreResult` (one `ae.associate()`, loops `send_c_store`, off-loop via `asyncio.to_thread()`)
- **`BatchStoreResult(total_sent, total_failed, failed_sop_uids)`**, re-exported from `models.py`
- Used by `AnonymizationService._send_series_to_pacs()` for per-series batch distribution — sequentially to every node in `self.destinations` (`pacs` + `extra_pacs`); failures are counted per node (`aet@host:port` keys) and one node's failure never aborts the rest

## Association Semaphore

dimsechord's SCU enforces a process-global `threading.Semaphore` limiting concurrent DICOM associations across all operations (DICOMweb, anonymization, import). Initialized in the app lifespan via `DicomClient.set_max_concurrent_associations(settings.dicom_max_concurrent_associations)`. It is a `threading.Semaphore` (not `asyncio.Semaphore`) because it is acquired inside the `asyncio.to_thread()` worker — size it with the loop's other `to_thread` work in mind.

## Errors

dimsechord raises typed errors. Only `AssociationError` is reachable from the
code Clarinet runs, and it maps to 409 in `api/exception_handlers.py` —
preserving the contract the inline layer had, where every association failure
surfaced as CONFLICT. The rest of the hierarchy is unreachable today and is
deliberately not mapped: `FindFailedError` comes from `find_iter` /
`QueryEngine` (the typed `find_studies` / `find_series` log a warning and
return partial results instead of raising), and `PoolExhaustedError` /
`RetrieveBusyError`, `ArrivalTimeoutError`, `MoveToSelfError` belong to
`PullEngine` / `AssociationPool`. Map them when Clarinet adopts those.

## Anonymization API surface

Three entry points, all sharing the same `AnonymizationService` for raw DICOM work:

- **`AnonymizationService`** (DI alias `AnonymizationServiceDep`) — raw anonymize_study, no Record. Used by HTTP sync without a tracking Record (raw mode, backwards-compat).
- **`AnonymizationOrchestrator`** (`orchestrator.py`) — wraps the service with skip-guard, idempotent Patient anonymization, and Record submission. On success: PATCH (`update_record_data`) when the Record is already finished, POST (`submit_record_data`) otherwise. On **any** unhandled exception (domain, network, runtime) raised anywhere in the flow — including pre-flight `get_study` and Patient anonymization — the orchestrator marks the Record `failed` (with `error` field), then re-raises so retry/DLQ middleware see it. For finished records the failed transition uses PATCH + `update_record_status` to avoid the 409 from POST. Use via `create_anonymization_orchestrator(client=...)` async context manager.
- **`anonymize_study_pipeline`** (`pipeline.py`) — built-in `@pipeline_task` that runs the orchestrator with the worker's `ctx.client`. Downstream wraps this with `run_anonymization(msg, ctx, extra_record_data={...})` to add project-specific Record fields.

Series subset: `anonymize_study(..., series_uids=[...])` restricts the run to an
explicit selection; empty / unknown / filter-excluded selections raise
`AnonymizationFailedError` naming each offending UID (+ filter reason) — a subset
request is never silently narrowed. `AnonymizationOrchestrator.run` and
`run_anonymization` pass `series_uids` through kwarg-only (deliberately not read
from `msg.payload`).

Multi-PACS fan-out: `AnonymizationService(..., extra_pacs=[DicomNode(...)])`, or
`settings.anon_extra_pacs_nodes` wired on every construction path via
`extra_pacs_from_settings()` (orchestrator/worker factory AND the HTTP DI
factory). `pacs` keeps its dual role (C-GET source + first destination); extras
are store-only. Per-node failure counts land in
`AnonymizationResult.send_failed_by_node` (and the Record data);
`instances_send_failed` stays the sum. With `anon_fail_on_send_error=True`, any
send failure raises `AnonymizationSendError(failed_by_node)` (subclass of
`AnonymizationFailedError`) BEFORE `study.anon_uid` persists, so a retry redoes
the run cleanly.

Skip-guard policy: `study.anon_uid is set` AND `prev Record data has no error` AND `(sent_to_pacs already true OR not sending this run)` → skip. Re-run is always permitted after a previous error or when this run upgrades to send-to-PACS. Subset runs (`series_uids is not None`) bypass the guard entirely — the study-granular `anon_uid` cannot prove the requested series were processed. A subset run still persists the study-granular `anon_uid` (masking/viewer/file-path resolution depend on it) but records `series_uids` in its Record data — the guard treats such a record as not-done, so a later whole-study run on the same record re-runs instead of being wrongly skipped. `series_uids` is a reserved Record-data key — the orchestrator strips it from `extra_record_data` on whole-study runs (with a warning).

The HTTP endpoint `POST /api/dicom/studies/{uid}/anonymize` resolves a tracking Record by `settings.anon_record_type_name` (default `"anonymize-study"`); when present, sync mode runs the orchestrator and background mode dispatches `anonymize_study_pipeline` (or in-process orchestrator when `pipeline_enabled=False`); without a Record, sync runs raw and background returns 404.

`_run_orchestrator_in_process` accepts `record_id: int` (not `int | None`). Callers must `assert record.id is not None` after `_find_anonymize_record` to satisfy mypy — see `clarinet/models/CLAUDE.md` → "Primary keys after insert/get".

## Anonymization contract: backend vs UX paths

Storage-path rendering lives in `clarinet/files/` — the same template
engine (`_storage.render_all_levels` + `_storage.derive_anon_patient_id`)
feeds the writer, every reader, the CLI migration tool, and the pipeline
via `Files(record)` (the public entry point). One rendering point means a
custom `disk_path_template` produces the same path everywhere — there is no
writer / reader divergence to worry about. Routers and services call the
path resolver through `Files` (`from clarinet.files import Files`), which
is the only public entry point — models carry no path logic.

Studies may be anonymized mid-pipeline (PR #250 — asymmetric anonymization),
so a `Record` created before the anonymization run carries
`record.study_anon_uid = None` even though `study.anon_uid` has since been
populated. Silently falling back to the raw UID in this window made backend
tasks load the wrong dataset or address files that the writer no longer
produces under that identifier.

Resolvers therefore default to **safe-by-default** mode — when the
anonymized identifier is missing they raise `AnonPathError`
(`clarinet.exceptions.AnonPathError`) instead of returning the raw UID.
UX call sites opt in to the legacy fallback via `Files(record, fallback=True)`
or `Files.for_reader(record)`.

Backend (no fallback — default):
- `AnonymizationService._save_series_to_disk` (the writer)
- `DicomWebCache._resolve_dcm_anon_dir` (the reader; catches
  `AnonPathError` and returns `None` so the cache simply misses)
- `prefetch_dicom_web._has_dcm_anon` (anonymized cache lookup; same
  catch pattern)
- `clarinet anon migrate-paths` (per-record failures are logged and the
  CLI moves on)
- `ctx.files` in pipeline tasks (`Files(record)` from `build_task_context`)
- `Files(record)` constructor (raises on missing anon —
  routers catch and serve `null` for UX endpoints)

UX (`Files(record, fallback=True)` / `Files.for_reader(record)`):
- `build_slicer_context` (Slicer is the UI layer — opens in-flight
  records on the raw UID when anonymization has not propagated yet)
- `build_template_vars` in `slicer/context.py` (renders the same
  `{study_anon_uid}` placeholders for user-authored args)
- `Files.for_reader(record)` for backend services that must tolerate the
  pre-anon flow: `validate_record_files`,
  `RecordService._collect_output_file_paths`,
  `RecordService.check_files`, cascade delete
- `viewer.py` inline fallbacks for external viewer URIs

`RecordRead` / `SeriesRead` / `StudyRead` / `PatientRead` carry no
path-resolution logic — `working_folder` / `slicer_*_args_formatted`
fields and the `_format_path` / `_get_working_folder` /
`_format_slicer_kwargs` helpers were removed. Routers compose paths
explicitly via `Files`; the frontend no longer decodes a `working_folder` key.

If you add a new resolver call, pick the side first — the boolean lives
in the call site, not in the entity.

## Key conventions

- All I/O goes through `asyncio.to_thread()` because pynetdicom is synchronous
- Exceptions: `CONFLICT` for association failures, `NOT_FOUND` where applicable
- Logger: `from clarinet.utils.logger import logger`
