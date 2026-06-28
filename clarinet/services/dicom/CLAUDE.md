# DICOM Service

Async DICOM client for Query/Retrieve operations against external PACS servers (e.g. Orthanc).
The DICOM transport core (`DicomClient`, `StorageSCP`, query/result models, converters,
multipart) lives in the **`dimsechord`** package; this service re-exports it and adds the
Clarinet-specific anonymization / Record / series-filter layers on top.

## Architecture

```
dicom/
  models.py         # Clarinet domain models (AnonymizationResult, Pacs* requests …);
                    #   re-exports DicomNode/queries/results/BatchStoreResult from dimsechord
  scp.py            # Process-wide StorageSCP singleton (thin wrap of dimsechord.StorageSCP)
  anonymizer.py     # DicomAnonymizer + per-study patient-id helpers
  series_filter.py  # Configurable series filter (modality blocklist, instance count, unknown policy)
  orchestrator.py   # AnonymizationOrchestrator — Record-aware skip-guard + Patient + submit
  pipeline.py       # Built-in @pipeline_task anonymize_study_pipeline + run_anonymization helper
  tasks.py          # create_anonymization_service factory (raw, no Record bookkeeping)
  __init__.py       # Public API re-exports (dimsechord core + domain models)
```

- `DicomClient` (from `dimsechord`, re-exported via `from clarinet.services.dicom import DicomClient`)
  is the main entry point — all methods are async; the synchronous pynetdicom plumbing and its
  `asyncio.to_thread()` offloading are internal to dimsechord.
- `StorageSCP` (the C-MOVE move-to-self target) is dimsechord's; `scp.py` only owns the
  process-wide singleton (`get_storage_scp()` / `shutdown_storage_scp()`), started in the app
  lifespan when `dicom_retrieve_mode` is a `c-move*` mode.
- The package `__init__` preserves a process-wide pynetdicom identifier-logging toggle
  (`LOG_RESPONSE_IDENTIFIERS` / `LOG_REQUEST_IDENTIFIERS` from `settings.dicom_log_identifiers`).

## Settings (`clarinet/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `dicom_aet` | `CLARINET` | Local AE title |
| `dicom_port` | `11112` | Local DICOM port |
| `dicom_ip` | `None` | Local DICOM IP |
| `dicom_max_pdu` | `16384` | Maximum PDU size |
| `dicom_max_concurrent_associations` | `8` | Process-global cap on concurrent DICOM associations (dimsechord) |
| `pacs_aet` | `ORTHANC` | Remote PACS AE title |
| `pacs_host` | `localhost` | Remote PACS host |
| `pacs_port` | `4242` | Remote PACS port |

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
    StorageMode,
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

`DicomClient.store_instances_batch(datasets, peer)` (dimsechord, async) sends multiple datasets
over a single DICOM association (vs `store_instance`, which opens one association per dataset).
Returns `BatchStoreResult(total_sent, total_failed, failed_sop_uids)` (re-exported from dimsechord).
Used by `AnonymizationService._send_series_to_pacs()` for per-series batch distribution.

## Association cap

dimsechord's `DicomClient` enforces a process-global cap on concurrent DICOM associations
(shared across DICOMweb, anonymization, import). The app lifespan sets it once via
`DicomClient.set_max_concurrent_associations(settings.dicom_max_concurrent_associations)`. The
C-MOVE Storage-SCP / `PullEngine` path bounds itself with the same value through dimsechord's
`AssociationPool(per_aet_cap=settings.dicom_max_concurrent_associations)`.

## Anonymization API surface

Three entry points, all sharing the same `AnonymizationService` for raw DICOM work:

- **`AnonymizationService`** (DI alias `AnonymizationServiceDep`) — raw anonymize_study, no Record. Used by HTTP sync without a tracking Record (raw mode, backwards-compat).
- **`AnonymizationOrchestrator`** (`orchestrator.py`) — wraps the service with skip-guard, idempotent Patient anonymization, and Record submission. On success: PATCH (`update_record_data`) when the Record is already finished, POST (`submit_record_data`) otherwise. On **any** unhandled exception (domain, network, runtime) raised anywhere in the flow — including pre-flight `get_study` and Patient anonymization — the orchestrator marks the Record `failed` (with `error` field), then re-raises so retry/DLQ middleware see it. For finished records the failed transition uses PATCH + `update_record_status` to avoid the 409 from POST. Use via `create_anonymization_orchestrator(client=...)` async context manager.
- **`anonymize_study_pipeline`** (`pipeline.py`) — built-in `@pipeline_task` that runs the orchestrator with the worker's `ctx.client`. Downstream wraps this with `run_anonymization(msg, ctx, extra_record_data={...})` to add project-specific Record fields.

Skip-guard policy: `study.anon_uid is set` AND `prev Record data has no error` AND `(sent_to_pacs already true OR not sending this run)` → skip. Re-run is always permitted after a previous error or when this run upgrades to send-to-PACS.

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
- `CacheFiller._resolve_dcm_anon_dir` (the dcm_anon reader; catches
  `AnonPathError` and returns `None` so the lookup simply misses)
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

- DICOM transport I/O (synchronous pynetdicom + its `asyncio.to_thread()` offloading) is owned by dimsechord
- Exceptions: `CONFLICT` for association failures, `NOT_FOUND` where applicable
- Logger: `from clarinet.utils.logger import logger`
