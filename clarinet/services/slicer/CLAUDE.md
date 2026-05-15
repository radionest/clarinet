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

Context builder and hydration details: `.claude/rules/slicer-context.md` (auto-loaded for context files).

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

- `PacsHelper(host, port, called_aet, calling_aet, retrieve_mode, move_aet)` — explicit connection params (for testing)
- `PacsHelper.verify() -> bool` — C-ECHO connectivity test; logs diagnostics on failure
- **Retrieve modes** (`dicom_retrieve_mode` setting / `CLARINET_DICOM_RETRIEVE_MODE`):
  - `c-get` (default) / `c-move` — per-series retrieve
  - `c-get-study` / `c-move-study` — study-level retrieve (for PACS rejecting series-level associations)
- `PacsHelper.from_slicer(server_name=None)` — reads PACS config from `QSettings` (`DICOM/ServerNodes/*`) as a **workaround** for `ctkDICOMVisualBrowser` not reflecting user-configured servers; picks first query/retrieve-enabled server or falls back to first server. Each user configures PACS once in `Edit > Application Settings > DICOM`. Logs via `_pacs_log` (`logging.getLogger("clarinet.slicer.pacs")`)
- `retrieve_study(study_instance_uid)` → **local-first**: checks `slicer.dicomDatabase` for existing series, falls back to C-FIND + retrieve from PACS using `retrieve_mode` strategy
- `retrieve_series(study_instance_uid, series_instance_uid)` → **local-first**: checks `slicer.dicomDatabase.filesForSeries()`, falls back to retrieve from PACS using `retrieve_mode` strategy
- Called internally by `SlicerHelper.load_study_from_pacs()` and `load_series_from_pacs()` — not used directly by scripts

### Local-first lookup strategy

Both `retrieve_study()` and `retrieve_series()` check Slicer's local DICOM database (`slicer.dicomDatabase`) before contacting the PACS server. If data is found locally, it is loaded directly via `DICOMUtils.loadSeriesByUID()`, avoiding network round-trips. This makes reopening previously loaded studies/series near-instant.

### PACS configuration

Hybrid approach: PACS server params (`pacs_host`, `pacs_port`, `pacs_aet`) are injected from Clarinet `settings.py` into context variables by `build_slicer_context()`. The `_get_pacs_helper()` function reads these globals at runtime for server connection, but always reads `calling_aet` and `move_aet` from Slicer's QSettings via `PacsHelper.from_slicer()` — each user's Slicer has its own AE title for C-MOVE destination.

Fallback: if context variables are absent (standalone/manual usage), `PacsHelper.from_slicer()` provides all params from Slicer's QSettings.

**Usage via POST /exec:**
```json
{
  "script": "s = SlicerHelper('/tmp')\nloaded = s.load_study_from_pacs('1.2.840...')"
}
```

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

### E2E test patterns

- Scripts return results via `__execResult = {...}`, NOT `print(json.dumps(...))`
- Use `_pacs_helper_script_block()` for explicit PacsHelper params
- Use `_monkey_patch_from_slicer_block()` for overriding `from_slicer()`
- Use `_context_injection_block()` for Clarinet PACS context variables

## `__execResult` Result-Merging Contract

`slicer_result_validator` scripts can return extra fields via `__execResult = {...}`.
Those keys are merged into `record.data` before the record is saved. This replaces
HTTP callbacks from the validator (which are impossible in practice — see "Why
HTTP-callbacks from validators are unsupported" below).

### Flow

1. Doctor edits in Slicer → `POST /api/records/{id}/submit` (or `POST /api/records/{id}/data`).
2. `_process_submission` validates `data` against the record-type schema (pass 1).
3. `slicer_result_validator` runs in Slicer. The script may:
   - Raise → propagates as `SlicerError` → 422.
   - Assign `__execResult = {field: value, ...}` to write fields into `record.data`.
4. Framework merges `__execResult` over `validated_data` — **validator wins** on
   key collisions.
5. Framework re-runs `rt_service.validate_record_data()` on the merged dict
   (pass 2). Validator-injected values pass through the same JSON Schema and
   custom Python validators as user-submitted fields.
6. Record is persisted.

### Canonical use case — `x-widget: "hidden"`

The formosh JSON Schema extension `"x-widget": "hidden"` excludes a field from
the rendered form. The validator computes the value and provides it via
`__execResult` — the framework merges it into `record.data` and re-validates.

```json
// plan/schemas/cropping-box.schema.json
{
  "type": "object",
  "properties": {
    "x_min": { "type": "number", "x-widget": "hidden" },
    "x_max": { "type": "number", "x-widget": "hidden" },
    "y_min": { "type": "number", "x-widget": "hidden" },
    "y_max": { "type": "number", "x-widget": "hidden" },
    "z_min": { "type": "number", "x-widget": "hidden" },
    "z_max": { "type": "number", "x-widget": "hidden" }
  }
}
```

**Do not mark validator-filled fields as `required`** — the first validation
pass runs against the (empty) submitted form, before Slicer runs. Required
hidden fields would fail with `422: '...' is a required property` and the
validator would never be invoked. If you want a presence guarantee, use a
custom Python validator with `run_on_partial=False`, which runs on the
merged dict during the second validation pass.

```python
# plan/validators/cropping_box_validator.py
roi = slicer.util.getNodesByClass("vtkMRMLMarkupsROINode")[0]
center, size = [0.0]*3, [0.0]*3
roi.GetCenterWorld(center)
roi.GetSizeWorld(size)
__execResult = {
    "x_min": center[0] - size[0] / 2.0, "x_max": center[0] + size[0] / 2.0,
    "y_min": center[1] - size[1] / 2.0, "y_max": center[1] + size[1] / 2.0,
    "z_min": center[2] - size[2] / 2.0, "z_max": center[2] + size[2] / 2.0,
}
```

No HTTP callbacks, no `clarinet_api_url` / `clarinet_auth_cookie` injection.

### Conflict policy

On overlapping keys between `__execResult` and user-submitted `data` —
**validator wins**. This is intentional: hidden fields are authoritative and
not editable by the doctor. If a non-hidden key happens to collide, the
validator override is still applied; if that's not desired, the validator
should not return that key.

### Error semantics

- Validator raises `Exception` → Slicer returns 500 → `SlicerError` → 422.
- `__execResult` carries a value that fails schema/custom validation →
  `RecordDataValidationError` → 422 with structured `errors` (field paths
  include validator-injected keys, just like form fields).
- `__execResult` is empty / unset → merge is skipped; behaviour unchanged.

### Why HTTP-callbacks from validators are unsupported

The validator runs between two save phases: the record is `inwork`, and the
`pending → finished` (or `inwork → finished`) transition has not happened yet.
At that exact moment:

- `POST/PUT/PATCH /data/prefill` rejects the record (requires `pending` /
  `blocked`).
- `PATCH /data` rejects (requires `finished`).
- `POST /data` would succeed but recursively triggers another submission.

`__execResult` merging is the only consistent path for a validator to write
into `record.data`.
