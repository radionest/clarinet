# Slicer Service Guide

## Overview

HTTP-based integration with **3D Slicer** desktop application. Sends Python scripts to Slicer's built-in web server (`POST /slicer/exec`) and returns JSON responses. No DB access — stateless service.

## Architecture (3 files)

| File | Role |
|------|------|
| `client.py` | `SlicerClient` — async httpx wrapper for Slicer HTTP API |
| `service.py` | `SlicerService` — orchestrator: prepends helper DSL + context vars to scripts |
| `helper.py` | `SlicerHelper` DSL — runs **inside Slicer**, not in Clarinet. Read as text and sent as script payload |

**Flow:** Router → `SlicerService.execute()` → builds script (helper + context + user code) → `SlicerClient.execute()` → HTTP POST to Slicer

## Key Details

- **Slicer URL** is per-request: `http://{client_ip}:{settings.slicer_port}` (each user has local Slicer)
- **Client is short-lived**: new `SlicerClient` per request via `async with` (no connection pooling)
- **Helper caching**: `SlicerService.__init__` reads `helper.py` once from disk
- **DI**: `SlicerServiceDep` in `dependencies.py`, factory `get_slicer_service()` — no DB session needed

## Settings (`src/settings.py`)

```python
slicer_port: int = 2016          # Default Slicer web server port
slicer_timeout: float = 10.0     # HTTP timeout (seconds)
slicer_script_paths: list[str]   # Additional script directories (unused currently)
```

## Exceptions (`src/exceptions/domain.py`)

- `SlicerError` — base (non-200 response)
- `SlicerConnectionError(SlicerError)` — connect/timeout failure
- `SlicerSegmentationError(SlicerError)` — segmentation-specific
- `ScriptError(SlicerError)` — script execution error
- `NoScriptError(ScriptError)` — record type has no `slicer_script` or `slicer_result_validator`
- `ScriptArgumentError(ScriptError)` — invalid script arguments

## Helper DSL (`helper.py`)

Runs inside Slicer Python environment. Has `_Dummy` fallback for testing outside Slicer.

### Module-level functions (no `SlicerHelper` init needed)

- `SlicerHelperError(Exception)` — lightweight exception for helper errors
- `export_segmentation(name, output_path)` → exports segmentation node to file; raises `SlicerHelperError` if node not found or file not created
- `clear_scene()` → calls `slicer.mrmlScene.Clear(0)`

### SlicerHelper class

- `SlicerHelper(working_folder)` — clears scene, sets root dir
- `load_volume(path, window=)` → image node
- `create_segmentation(name)` → `SegmentationBuilder` (fluent `.add_segment()`, `.select_segment(name)`)
- `load_segmentation(path, name=None)` → loads existing segmentation from file, sets reference geometry
- `setup_editor(seg, effect=, brush_size=, threshold=)` — configures SegmentEditor
- `set_layout("axial"|"sagittal"|"coronal"|"four_up")`
- `annotate(text)`, `configure_slab(thickness=)`, `setup_edit_mask(path)`
- `add_view_shortcuts()` — a/s/c keys for view switching
- `add_shortcuts(shortcuts: list[tuple[str, str]])` — custom keyboard shortcuts (key→layout or key→exec code)
- `load_study_from_pacs(study_instance_uid)` → list of loaded MRML node IDs

## PacsHelper (`helper.py`)

DIMSE (C-FIND + C-GET/C-MOVE) integration via `ctkDICOMQuery` / `ctkDICOMRetrieve`.

- `PacsHelper(host, port, called_aet, calling_aet, prefer_cget, move_aet)` — connection params
- `retrieve_study(study_instance_uid)` → queries PACS, retrieves series, loads into scene
- Called internally by `SlicerHelper.load_study_from_pacs()` — not used directly by scripts

**PACS settings** (`src/settings.py`):
```python
pacs_host: str = "localhost"      # Remote PACS hostname/IP
pacs_port: int = 4242            # PACS DIMSE port
pacs_aet: str = "ORTHANC"       # Called AE Title
pacs_calling_aet: str = "SLICER" # Calling AE Title
pacs_prefer_cget: bool = True    # C-GET (True) vs C-MOVE (False)
pacs_move_aet: str = "SLICER"   # Move destination AET (C-MOVE only)
```

**Usage via POST /exec:**
```json
{
  "script": "s = SlicerHelper('/tmp')\nloaded = s.load_study_from_pacs('1.2.840...')",
  "context": {
    "pacs_host": "192.168.1.10", "pacs_port": 4242,
    "pacs_aet": "PACS", "pacs_calling_aet": "SLICER",
    "pacs_prefer_cget": true, "pacs_move_aet": "SLICER"
  }
}
```

Context variables are injected by `SlicerService._build_context_block()` — no new endpoint needed.

## Router Endpoints (`src/api/routers/slicer.py`)

- `POST /exec` — execute script with helper DSL prepended
- `POST /exec/raw` — execute raw script (no helper)
- `POST /clear` — clear the Slicer scene (sends `slicer.mrmlScene.Clear(0)` via `execute_raw`)
- `GET /ping` — check Slicer reachability
- `POST /records/{record_id}/open` — load record workspace in Slicer (uses `record_type.slicer_script` + `slicer_all_args_formatted`, injects PACS context, 60s timeout). Raises `NoScriptError` if no script configured.
- `POST /records/{record_id}/validate` — run `record_type.slicer_result_validator` (same context/timeout pattern). Raises `NoScriptError` if no validator configured.

## Testing

Tests in `tests/integration/test_slicer_*.py`. Helper has `_Dummy` stubs so `helper.py` is importable without Slicer.
