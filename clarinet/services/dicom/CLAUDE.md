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
  anonymizer.py     # DicomAnonymizer — salted-hash UIDs over dicomanonymizer defaults
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
| `c-get` (default), `c-get-study` | dimsechord's C-GET | nothing beyond an outbound association |
| `c-move`, `c-move-study` | C-MOVE-to-self, delegated to dimsechord's `retrieve_via_move` | a running Storage SCP; the peer must route `dicom_aet` back to `dicom_ip:dicom_port` |

**Which to choose.** An association carries at most 128 presentation contexts,
and the two paths spend that budget differently. The C-GET SCU has to *propose*
storage contexts, so it negotiates dimsechord's 26 curated classes — 10 image
classes, each across the compressed transfer syntaxes, plus 16 non-image ones
uncompressed — broad on syntax, narrow on SOP class. The Storage SCP only
*accepts*, matching whatever the peer proposes, so it covers pynetdicom's 120
`StoragePresentationContexts` with every transfer syntax and never spends the
budget.

So c-get needs nothing from the network, and c-move covers far more classes.
**On c-get, check your modalities first**: a SOP class outside
`DEFAULT_IMAGE_STORAGE_CLASSES` / `DEFAULT_OTHER_STORAGE_CLASSES` gets no
accepted context, so its instances fail their sub-operations and the retrieve
returns short — `num_failed > 0` under `warning_0xb000`, not an exception from
the client (see Short retrieves below). The curated set covers CT,
MR, Enhanced CT/MR, PET, CR, DX-for-presentation, SC, US and US multi-frame,
plus RT, SEG, KO, PR, encapsulated documents and the Basic Text / Enhanced /
Comprehensive / Comprehensive 3D SR classes. Notably **absent**: X-Ray Radiation
Dose SR (routine on CT), X-Ray Angiographic, Nuclear Medicine, Digital
Mammography, Enhanced XA/XRF, Breast Tomosynthesis and the VL/endoscopic family,
among others. A site with any of those wants c-move until dimsechord takes a
storage-class argument. c-move is not complete either: its 120 classes are
pynetdicom's `StoragePresentationContexts`, so a class outside them is refused
in **both** modes — private ones such as Siemens CSA Non-Image (PhoenixZIPReport
on MR), and newer standard ones such as Radiopharmaceutical Radiation Dose SR
(PET/CT, NM), Enhanced X-Ray Radiation Dose SR and Parametric Map.

### Listener ownership

A listening port belongs to one process, and the PACS routes C-MOVE by
destination AET to a host and port it was configured with. Both consequences
are the operator's to resolve, and the two processes resolve them differently:

| Process | Owns a listener when |
|---|---|
| API lifespan | `storage_scp_wanted()` — `dicom_scp_enabled`, else a c-move mode |
| `clarinet worker` | asked explicitly — `--dicom AET:PORT`, or `dicom_scp_enabled=true` |

The worker must not infer it from the mode: on a c-move deployment the API
already holds `dicom_aet` on `dicom_port`, so a worker that inferred ownership
would race it for the same port and lose.

- **One process retrieving** — nothing to configure *if it is the API*. It binds
  `dicom_aet` on `dicom_port`; register that pair on the PACS. A worker
  retrieving on its own still needs `--dicom`, or it starts with no listener and
  raises at its first retrieve.
- **Several retrieving on one host** — each needs its own registered
  `(AET, port)`. `clarinet worker --dicom AET:PORT` sets both for a worker, and
  switches the transport to C-MOVE *keeping the Q/R level* (`c-get-study` →
  `c-move-study`) plus `dicom_scp_enabled=true`, so the flag still works where
  one shared `EnvironmentFile` says otherwise.
- **A process that must not retrieve** — `dicom_scp_enabled=false`. It binds
  nothing; a C-MOVE retrieve from it then raises a `RuntimeError` naming the
  AET and port that would have to be registered.

Both `dicom_scp_enabled` values are **per-process**. Setting `true` in a shared
`EnvironmentFile` — which both deploy templates read — makes the API and every
worker claim the same port, and whichever starts second crash-loops. Give each
process its own AET and port instead; for a worker that is what `--dicom` is.

A bind collision raises at startup with the port, the AET and those three ways
out. `start_storage_scp` deliberately does **not** fall back to a free port: the
PACS was never told to route there, so the listener would receive nothing and
every retrieve would time out instead of failing.

`_retrieve_via_move` translates the call and hands it to dimsechord: the Q/R
level comes from whether a `series_uid` was passed, the storage mode from
whether an `output_dir` was, the destination from `settings.dicom_aet`, and
`dicom_cmove_timeout` becomes the arrival budget while the `timeout` argument
bounds the association. Everything after that — the collect session, driving
the C-MOVE, the arrival target, the wait, the disk write — is
`DicomOperations.retrieve_via_move`, which Clarinet used to own and which was
ported into dimsechord unchanged. **Do not reimplement it here.** The arrival
target in particular has to be read from the *first* pending response: a final
Success response may omit the sub-operation counters (PS3.4 C.4.2.1.6), so a
total summed at the end sees a stale `NumberOfRemainingSuboperations`.

Two known rough edges in that upstream code, both pre-dating the move to the
package: the target is the peer's *announced* total, so a retrieve with failed
sub-operations waits out `dicom_cmove_timeout` and reports `status="timeout"`
with the instances that did arrive; and a peer that omits the counters entirely
on its first pending response leaves the target unset, with the same effect on
an otherwise successful retrieve. Both want fixing in dimsechord, not here.
Clarinet's consumers read either as a short retrieve (below), which makes the
second fatal rather than slow: a first pending response without counters sums
to `total_expected == 0`, skipping both `set_expected` and the final-count
fallback, so every retrieve from such a peer ends `timeout`, and
`ensure_series_cached` and `convert_series_to_nifti` fail on it. Paths with a
C-FIND count to hand compensate — `ensure_study_cached` and
`prefetch_dicom_web` — as does `AnonymizationService`, which never reads
`status`; the fix belongs in dimsechord.

The `-study` suffix does not reach this path at all — it is read by the Slicer
helper (`helper.py`), which batches its own ctkDICOM retrieves at study level.

### Short retrieves

`num_completed` cannot tell a whole retrieve from a short one;
`retrieve_is_complete(result)` can — `status == "success"` and `num_failed == 0`.
A timed-out C-MOVE (`timeout`), a C-GET whose SOP class got no context
(`warning_0xb000`) and an association dropped before its final response
(`pending`) all fail it. Consumers check it after their zero-instance guard:

| Consumer | On a short retrieve |
|---|---|
| `DicomWebCache.ensure_series_cached`, `convert_series_to_nifti` | raise — nothing is cached or converted (`ensure_series_cached` raises `SeriesRefusedError` for a refused series) |
| `DicomWebCache.ensure_study_cached` | keep the series whose arrivals reach the C-FIND `NumberOfSeriesRelatedInstances` (`series_instance_counts`), then retrieve every requested series still missing — short, or never sent — on its own through `ensure_series_cached`, which raises if that is short too; a refused series is logged and left out |
| `prefetch_dicom_web` | publish only the series that arrived whole — after the study-level retrieve, by C-FIND count; after per-series ones, by the series' own status or, failing that, its C-FIND count — then raise naming the rest, so the retry fetches only those; a refused series is logged and left out |
| `AnonymizationService._retrieve_series` | its own check — received vs `Series.instance_count`, retried; does not read `status` |

A series the peer gave no (or a zero) count for is never vouched for by count.

**Refused** (`retrieve_was_refused`): nothing arrived and `num_failed > 0` — the
peer tried every instance and none was stored, usually a SOP class outside the
negotiated contexts. Retrying cannot help that, so the study is served and
prefetched without the series. The counts cannot tell it from a total failure
of another kind — a store handler that cannot write (full disk), an
unreachable c-move destination — so prefetch treats those as refused too.
A study-level retrieve cannot tell refused from short, so prefetch's first run
still fails and a per-series retry settles it — which needs C-FIND counts
(without them the retry is study-level again and ends in the DLQ), and a study
whose *only* series is refused still hits the zero-instance guard. A series
that arrives *partially* every time spends the retries and lands in the DLQ.

## Settings (`clarinet/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `dicom_aet` | `CLARINET` | Local AE title |
| `dicom_port` | `11112` | Local DICOM port |
| `dicom_ip` | `None` | Local DICOM IP |
| `dicom_max_pdu` | `16384` | Maximum PDU size |
| `dicom_max_concurrent_associations` | `8` | Global semaphore limit for concurrent DICOM associations |
| `dicom_retrieve_mode` | `c-get` | `c-get` / `c-get-study` / `c-move` / `c-move-study` — see Retrieve modes below |
| `dicom_cmove_timeout` | `300.0` | Seconds bounding the C-MOVE *and* the wait for its instances to arrive |
| `dicom_scp_enabled` | `None` | `None` = the API owns a listener when the mode is c-move (a worker needs `--dicom` or `true`); `false` = never; `true` = always |
| `pacs_aet` | `ORTHANC` | Remote PACS AE title |
| `pacs_host` | `localhost` | Remote PACS host |
| `pacs_port` | `4242` | Remote PACS port |
| `anon_extra_pacs_nodes` | `[]` | Extra C-STORE destinations for anonymized instances (TOML `[[anon_extra_pacs_nodes]]` tables with aet/host/port; env as JSON string) |
| `anon_fail_on_send_error` | `False` | Raise `AnonymizationSendError` on any C-STORE failure, before the study anon_uid persists |

Env vars use `CLARINET_` prefix (e.g. `CLARINET_PACS_HOST`).

## Test PACS (Orthanc)

- Host: `localhost` by default; override via `CLARINET_TEST_PACS_HOST` (see `tests/config.py` and `.env.test.example`)
- DICOM port: `4242`, AET: `ORTHANC`
- REST API: `http://<host>:8042`. The deploy VM's Orthanc has HTTP auth on (stock `orthanc:orthanc`, see `deploy/CLAUDE.md`); tests send `CLARINET_TEST_PACS_REST_USER`/`_PASS`
- Stock Orthanc allows C-ECHO and C-STORE from anyone but answers C-FIND/C-GET/C-MOVE from an unregistered AET with **zero matches, not an error** (`DicomAlwaysAllowFind/Get/Move = false`). Tests register their calling AET and seed their own dataset through `require_test_pacs()` (`tests/utils/dicom.py`)

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

dimsechord's SCU enforces a process-global `threading.Semaphore` limiting concurrent DICOM associations across all operations (DICOMweb, anonymization, import). Initialized via `DicomClient.set_max_concurrent_associations(settings.dicom_max_concurrent_associations)` in **both** the app lifespan and `run_worker` — a class attribute binds only the process that sets it, so each process that opens associations must install its own (#551). The limit is therefore per process, not fleet-wide: the API plus N workers can hold (N+1) × the setting at once. It is a `threading.Semaphore` (not `asyncio.Semaphore`) because it is acquired inside the `asyncio.to_thread()` worker — size it with the loop's other `to_thread` work in mind.

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
