# DICOM Service

Async DICOM client for Query/Retrieve operations against external PACS servers (e.g. Orthanc).

## Architecture

```
dicom/
  models.py         # Pydantic models: DicomNode, queries, results, storage config
  operations.py     # Synchronous pynetdicom wrapper (C-FIND, C-GET, C-MOVE)
  handlers.py       # C-STORE event handlers (disk / memory / forward modes)
  client.py         # Async facade — delegates to operations via asyncio.to_thread()
  anonymizer.py     # Anonymizer, PACS stubs (planned; not yet exported)
  series_filter.py  # Configurable series filter (modality blocklist, instance count, unknown policy)
  orchestrator.py   # AnonymizationOrchestrator — Record-aware skip-guard + Patient + submit
  pipeline.py       # Built-in @pipeline_task anonymize_study_pipeline + run_anonymization helper
  tasks.py          # create_anonymization_service factory (raw, no Record bookkeeping)
  __init__.py       # Public API re-exports
```

- `DicomClient` is the main entry point — all methods are async
- `DicomOperations` is synchronous; never call it directly from async code
- `StorageHandler` handles incoming C-STORE events in three modes: `DISK`, `MEMORY`, `FORWARD`

## Settings (`clarinet/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `dicom_aet` | `CLARINET` | Local AE title |
| `dicom_port` | `11112` | Local DICOM port |
| `dicom_ip` | `None` | Local DICOM IP |
| `dicom_max_pdu` | `16384` | Maximum PDU size |
| `dicom_max_concurrent_associations` | `8` | Global semaphore limit for concurrent DICOM associations |
| `pacs_aet` | `ORTHANC` | Remote PACS AE title |
| `pacs_host` | `localhost` | Remote PACS host |
| `pacs_port` | `4242` | Remote PACS port |

Env vars use `CLARINET_` prefix (e.g. `CLARINET_PACS_HOST`).

## Test PACS (Orthanc on klara)

- Host: `192.168.122.151`
- DICOM port: `4242`, AET: `ORTHANC`
- REST API: `http://192.168.122.151:8042` (no auth)
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

`store_instances_batch` sends multiple datasets over a single DICOM association (vs `store_instance` which opens one association per dataset).

- **`operations.py`**: `store_instances_batch(config, datasets)` → `BatchStoreResult` (sync, one `ae.associate()`, loops `send_c_store`)
- **`client.py`**: `store_instances_batch(datasets, peer)` → async wrapper via `asyncio.to_thread()`
- **`models.py`**: `BatchStoreResult(total_sent, total_failed, failed_sop_uids)`
- Used by `AnonymizationService._send_series_to_pacs()` for per-series batch distribution

## Association Semaphore

`DicomOperations._association()` enforces a global `threading.Semaphore` to limit concurrent DICOM associations across all operations (DICOMweb, anonymization, import). Initialized in app lifespan via `DicomOperations.set_association_semaphore(settings.dicom_max_concurrent_associations)`. Uses `threading.Semaphore` (not `asyncio.Semaphore`) because `_association()` is synchronous, called via `asyncio.to_thread()`.

## Anonymization API surface

Three entry points, all sharing the same `AnonymizationService` for raw DICOM work:

- **`AnonymizationService`** (DI alias `AnonymizationServiceDep`) — raw anonymize_study, no Record. Used by HTTP sync without a tracking Record (raw mode, backwards-compat).
- **`AnonymizationOrchestrator`** (`orchestrator.py`) — wraps the service with skip-guard, idempotent Patient anonymization, and Record submission. On success: PATCH (`update_record_data`) when the Record is already finished, POST (`submit_record_data`) otherwise. On **any** unhandled exception (domain, network, runtime) raised anywhere in the flow — including pre-flight `get_study` and Patient anonymization — the orchestrator marks the Record `failed` (with `error` field), then re-raises so retry/DLQ middleware see it. For finished records the failed transition uses PATCH + `update_record_status` to avoid the 409 from POST. Use via `create_anonymization_orchestrator(client=...)` async context manager.
- **`anonymize_study_pipeline`** (`pipeline.py`) — built-in `@pipeline_task` that runs the orchestrator with the worker's `ctx.client`. Downstream wraps this with `run_anonymization(msg, ctx, extra_record_data={...})` to add project-specific Record fields.

Skip-guard policy: `study.anon_uid is set` AND `prev Record data has no error` AND `(sent_to_pacs already true OR not sending this run)` → skip. Re-run is always permitted after a previous error or when this run upgrades to send-to-PACS.

The HTTP endpoint `POST /api/dicom/studies/{uid}/anonymize` resolves a tracking Record by `settings.anon_record_type_name` (default `"anonymize-study"`); when present, sync mode runs the orchestrator and background mode dispatches `anonymize_study_pipeline` (or in-process orchestrator when `pipeline_enabled=False`); without a Record, sync runs raw and background returns 404.

`_run_orchestrator_in_process` accepts `record_id: int` (not `int | None`). Callers must `assert record.id is not None` after `_find_anonymize_record` to satisfy mypy — see `clarinet/models/CLAUDE.md` → "Primary keys after insert/get".

## Anonymization contract: backend vs UX paths

Storage-path rendering itself lives in
`clarinet.services.common.storage_paths` — the same template engine
(`build_context` + `render_working_folder` + `render_all_levels` +
`derive_anon_patient_id`) feeds the writer, every reader, the CLI
migration tool, the helper method `RecordRead._get_working_folder`, and
the pipeline `FileResolver` (which is a thin wrapper over
`render_all_levels`). One rendering point means a custom
`disk_path_template` produces the same path everywhere — there is no
writer / reader divergence to worry about. Routers/services should call
the path resolver through `FileRepository`
(`clarinet/repositories/file_repository.py`).

Studies may be anonymized mid-pipeline (PR #250 — asymmetric anonymization),
so a `Record` created before the anonymization run carries
`record.study_anon_uid = None` even though `study.anon_uid` has since been
populated. Silently falling back to the raw UID in this window made backend
tasks load the wrong dataset or address files that the writer no longer
produces under that identifier.

Resolvers therefore default to **safe-by-default** mode — when the
anonymized identifier is missing they raise `AnonPathError`
(`clarinet.exceptions.AnonPathError`) instead of returning the raw UID.
UX call sites opt in to the legacy fallback via
`fallback_to_unanonymized=True`.

Backend (no fallback — default):
- `AnonymizationService._save_series_to_disk` (the writer)
- `DicomWebCache._resolve_dcm_anon_dir` (the reader; catches
  `AnonPathError` and returns `None` so the cache simply misses)
- `prefetch_dicom_web._has_dcm_anon` (anonymized cache lookup; same
  catch pattern)
- `clarinet anon migrate-paths` (per-record failures are logged and the
  CLI moves on)
- `FileResolver.build_working_dirs*` / pipeline `build_task_context`
- `_get_working_folder` on `RecordRead` (default safe mode)
- `RecordRead._format_path_strict` / `SeriesRead._format_path_strict` —
  default safe mode

UX (`fallback_to_unanonymized=True`):
- `RecordRead._format_path` → `_format_slicer_kwargs` → user-defined
  Slicer script args (`slicer_script_args`)
- `build_slicer_context` (Slicer is the UI layer — opens in-flight
  records on the raw UID when anonymization has not propagated yet)
- `build_template_vars` in `slicer/context.py` (renders the same
  `{study_anon_uid}` placeholders for user-authored args)
- `validate_record_files` / `RecordService._collect_output_file_paths`
  / `RecordService.check_files` (admin/UI endpoints — fail soft rather
  than 500)
- `viewer.py` inline fallbacks for external viewer URIs

After Phase 4 of the FileRepository refactor `RecordRead.working_folder`
/ `SeriesRead.working_folder` and the three `slicer_*_args_formatted`
fields are plain `Optional` fields with default `None` — they are no
longer auto-computed by the Pydantic model. Routers do not currently
inject them; callers must construct paths via `FileRepository` (strict)
or via the `_format_*` / `_get_working_folder` helpers (UX, with
fallback) explicitly.

If you add a new resolver call, pick the side first — the boolean lives
in the call site, not in the entity.

## Key conventions

- All I/O goes through `asyncio.to_thread()` because pynetdicom is synchronous
- Exceptions: `CONFLICT` for association failures, `NOT_FOUND` where applicable
- Logger: `from clarinet.utils.logger import logger`
