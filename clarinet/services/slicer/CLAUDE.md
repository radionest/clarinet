# Slicer Service Guide

## Overview

HTTP-based integration with **3D Slicer** desktop application. Sends Python scripts to Slicer's built-in web server (`POST /slicer/exec`) and returns JSON responses. Router uses `build_slicer_context_async()` for DB-backed context hydration.

## Architecture (5 files)

| File | Role |
|------|------|
| `client.py` | `SlicerClient` — async httpx wrapper for Slicer HTTP API |
| `service.py` | `SlicerService` — orchestrator: prepends helper DSL + context vars to scripts |
| `helper.py` | `SlicerHelper` DSL — runs **inside Slicer**, not in Clarinet. Read as text and sent as script payload |
| `context.py` | `build_slicer_context()` (sync) + `build_slicer_context_async()` (async, with hydration) |
| `context_hydration.py` | Decorator-based registry for async context enrichment hydrators |

**Flow:** Router → `build_slicer_context_async(record_read, session)` → sync `build_slicer_context()` + async hydrators → `SlicerService.execute()` → builds script (helper + context + user code) → `SlicerClient.execute()` → HTTP POST to Slicer

## Context Builder (`context.py`)

`build_slicer_context(record: RecordRead) -> dict[str, Any]` assembles the context dict in layers:

1. **Standard vars** (auto, by DICOM level):
   - `working_folder` — always
   - `study_uid` — for STUDY and SERIES level
   - `series_uid` — for SERIES level only
2. **File paths from file_registry** (auto): each `FileDefinition.name` → resolved absolute path via `FileResolver`
3. **`output_file`** (auto): first OUTPUT file from file_registry — convenience alias for scripts
4. **Custom `slicer_script_args`** (template-resolved with all vars above)
5. **Custom `slicer_result_validator_args`** (same)

`build_slicer_context_async(record, session)` wraps the sync function and runs any `slicer_context_hydrators` registered on the record type.

Uses `FileResolver` from `clarinet/services/pipeline/context.py` (100% sync, no DB dependencies).

### Script variable naming convention

Scripts use **FileDefinition names** as variable names (e.g. `segmentation_single`, `master_model`, `master_projection`).
The generic `output_file` alias points to the first OUTPUT file — useful for scripts shared across record types
(e.g. `segment.py` used by both `segment_CT_single` and `segment_CT_with_archive`).

### Helper: `build_template_vars(record)`

Provides the same set of placeholders as `RecordRead._format_path_strict()`:
`patient_id`, `patient_anon_name`, `study_uid`, `study_anon_uid`, `series_uid`, `series_anon_uid`, `user_id`, `clarinet_storage_path`.

## Context Hydration (`context_hydration.py`)

Decorator-based registry for async context enrichment. Mirrors `clarinet/services/schema_hydration.py`.

### Components

- `SlicerHydrationContext(frozen dataclass)` — holds `StudyRepository` and `RecordRepository`; created via `.from_session(session)`
- `@slicer_context_hydrator("name")` — registers an async function that returns `dict[str, Any]` to merge into context
- `hydrate_slicer_context(context, record, session, names)` — runs named hydrators sequentially, merges results
- `load_custom_slicer_hydrators(folder)` — loads `context_hydrators.py` from tasks folder at startup

### RecordType field

`RecordType.slicer_context_hydrators: list[str] | None` (JSON column) — list of hydrator names to run.
Set in `RecordDef` config: `slicer_context_hydrators=["patient_first_study"]`.

### Writing a hydrator

```python
from clarinet.services.slicer.context_hydration import (
    SlicerHydrationContext, slicer_context_hydrator,
)

@slicer_context_hydrator("patient_first_study")
async def hydrate_patient_first_study(record, context, ctx):
    studies = await ctx.study_repo.find_by_patient(record.patient_id)
    if not studies:
        return {}
    first = sorted(studies, key=lambda s: s.date or "")[0]
    return {"best_study_uid": first.anon_uid or first.study_uid}
```

## Key Details

- **Slicer URL** is per-request: `http://{client_ip}:{settings.slicer_port}` (each user has local Slicer)
- **Client is short-lived**: new `SlicerClient` per request via `async with` (no connection pooling)
- **Helper caching**: `SlicerService.__init__` reads `helper.py` once from disk
- **DI**: `SlicerServiceDep` in `dependencies.py`, factory `get_slicer_service()` — no DB session needed

## Settings (`clarinet/settings.py`)

```python
slicer_port: int = 2016          # Default Slicer web server port
slicer_timeout: float = 10.0     # HTTP timeout (seconds)
slicer_script_paths: list[str]   # Additional script directories (unused currently)
```

## Exceptions (`clarinet/exceptions/domain.py`)

- `SlicerError` — base (non-200 response)
- `SlicerConnectionError(SlicerError)` — connect/timeout failure
- `SlicerSegmentationError(SlicerError)` — segmentation-specific
- `ScriptError(SlicerError)` — script execution error
- `NoScriptError(ScriptError)` — record type has no `slicer_script` or `slicer_result_validator`
- `ScriptArgumentError(ScriptError)` — invalid script arguments

## Helper DSL (`helper.py`)

Runs inside Slicer Python environment. Has `_Dummy` fallback for testing outside Slicer.

Full method API + VTK pitfalls: `.claude/rules/slicer-helper-api.md` (auto-loaded when editing `helper.py`).

Key methods: `SlicerHelper(working_folder)`, `load_volume()`, `create_segmentation()`, `load_segmentation()`, `setup_editor()`, `load_study_from_pacs()`, `load_series_from_pacs()`, `set_dual_layout()`, `align_by_center()`, `setup_segment_focus_observer()`.

`load_study_from_pacs()` and `load_series_from_pacs()` auto-set first `vtkMRMLScalarVolumeNode` as `_image_node`.

## PacsHelper (`helper.py`)

DIMSE (C-FIND + C-GET/C-MOVE) integration via `ctkDICOMQuery` / `ctkDICOMRetrieve`.

- `PacsHelper(host, port, called_aet, calling_aet, prefer_cget, move_aet)` — explicit connection params (for testing)
- `PacsHelper.from_slicer(server_name=None)` — reads PACS config from `QSettings` (`DICOM/ServerNodes/*`) as a **workaround** for `ctkDICOMVisualBrowser` not reflecting user-configured servers; picks first query/retrieve-enabled server or falls back to first server. Each user configures PACS once in `Edit > Application Settings > DICOM`. Logs via `_pacs_log` (`logging.getLogger("clarinet.slicer.pacs")`)
- `retrieve_study(study_instance_uid)` → **local-first**: checks `slicer.dicomDatabase` for existing series, falls back to C-FIND + C-GET from PACS, then **C-MOVE if C-GET fails** (Orthanc without CGet plugin)
- `retrieve_series(study_instance_uid, series_instance_uid)` → **local-first**: checks `slicer.dicomDatabase.filesForSeries()`, falls back to C-GET, then **C-MOVE if C-GET fails**
- Called internally by `SlicerHelper.load_study_from_pacs()` and `load_series_from_pacs()` — not used directly by scripts

### Local-first lookup strategy

Both `retrieve_study()` and `retrieve_series()` check Slicer's local DICOM database (`slicer.dicomDatabase`) before contacting the PACS server. If data is found locally, it is loaded directly via `DICOMUtils.loadSeriesByUID()`, avoiding network round-trips. This makes reopening previously loaded studies/series near-instant.

### PACS configuration

PACS connection params for Slicer are **not** in `settings.py`. Each user configures their PACS server in Slicer's GUI (`Edit > Application Settings > DICOM`), including their own calling AE title. `PacsHelper.from_slicer()` reads this configuration at runtime.

Backend DICOM service (`clarinet/services/dicom/`) still uses `settings.pacs_host`, `settings.pacs_port`, `settings.pacs_aet` for server-side operations.

**Usage via POST /exec:**
```json
{
  "script": "s = SlicerHelper('/tmp')\nloaded = s.load_study_from_pacs('1.2.840...')"
}
```

No PACS context variables needed — `PacsHelper.from_slicer()` reads config directly from Slicer.

## Router Endpoints (`clarinet/api/routers/slicer.py`)

- `POST /exec` — execute script with helper DSL prepended
- `POST /exec/raw` — execute raw script (no helper)
- `POST /clear` — clear the Slicer scene (sends `slicer.mrmlScene.Clear(0)` via `execute_raw`)
- `GET /ping` — check Slicer reachability
- `POST /records/{record_id}/open` — load record workspace in Slicer (uses `build_slicer_context_async()` + `record_type.slicer_script`, 60s timeout). Raises `NoScriptError` if no script configured.
- `POST /records/{record_id}/validate` — run `record_type.slicer_result_validator` (same context/timeout pattern). Raises `NoScriptError` if no validator configured.

## Testing

- Unit tests: `tests/test_slicer_context.py` — `build_slicer_context()` and `build_slicer_context_async()` with mocked settings
- Unit tests: `tests/test_slicer_context_hydration.py` — registry, decorator, loader, error handling
- Integration tests: `tests/integration/test_slicer_*.py`, `tests/integration/test_record_working_folder.py`
- E2E tests: `tests/e2e/test_slicer_pacs_workflow.py` — Slicer ↔ PACS (C-GET/C-MOVE) without mocks: PacsHelper retrieval, load_study/series_from_pacs, record-open API, backend C-MOVE → Slicer load
- Helper has `_Dummy` stubs so `helper.py` is importable without Slicer
- All slicer tests use `xdist_group("slicer")` for parallel safety — single Slicer instance shared across tests
