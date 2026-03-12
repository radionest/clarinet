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
6. **PACS settings** (`pacs_host`, `pacs_port`, etc.)

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

### Module-level functions (no `SlicerHelper` init needed)

- `SlicerHelperError(Exception)` — lightweight exception for helper errors
- `export_segmentation(name, output_path)` → exports segmentation node to file; raises `SlicerHelperError` if node not found or file not created
- `clear_scene()` → calls `slicer.mrmlScene.Clear(0)`

### SlicerHelper class

- `SlicerHelper(working_folder)` — clears scene, sets root dir
- `load_volume(path, window=)` → image node
- `set_source_volume(node)` — explicitly set the source volume node for segmentation editing
- `create_segmentation(name)` → `SegmentationBuilder` (fluent `.add_segment()`, `.select_segment(name)`)
- `load_segmentation(path, name=None)` → loads existing segmentation from file, sets reference geometry
- `setup_editor(seg, effect=, brush_size=, threshold=, source_volume=)` — configures SegmentEditor; `source_volume` overrides `_image_node`
- `set_layout("axial"|"sagittal"|"coronal"|"four_up")`
- `annotate(text)`, `configure_slab(thickness=)`, `setup_edit_mask(path)`
- `add_view_shortcuts()` — a/s/c keys for view switching
- `add_shortcuts(shortcuts: list[tuple[str, str]])` — custom keyboard shortcuts (key→layout or key→exec code)
- `load_study_from_pacs(study_instance_uid)` → list of loaded MRML node IDs; **auto-sets first scalar volume as `_image_node`**
- `load_series_from_pacs(study_instance_uid, series_instance_uid)` → list of loaded MRML node IDs; **loads only the specified series; auto-sets first scalar volume as `_image_node`**
- `get_segment_names(segmentation)` → `list[str]` — ordered segment names from a segmentation node
- `get_segment_centroid(segmentation, segment_name)` → `tuple[float,float,float] | None` — RAS centroid via SegmentStatistics; None if empty
- `copy_segments(source_seg, target_seg, segment_names=None, empty=False)` — copy segments between segmentations; `empty=True` copies only metadata (name + color)
- `auto_number_segment(segmentation, prefix="ROI", start_from=None)` → `int` — adds `{prefix}_{N+1}` segment, returns assigned number
- `subtract_segmentations(seg_a, seg_b, output_name=None, max_overlap=0, max_overlap_ratio=None)` — ROI-level subtraction: removes seg_a segments overlapping with seg_b. In-place or new node if `output_name` set
- `set_dual_layout(volume_a, volume_b, seg_a=None, seg_b=None, linked=True)` — side-by-side view with Red/Yellow composites and per-view segmentation visibility
- `setup_segment_focus_observer(editable_seg, reference_seg)` — auto-jump to reference centroid when selecting an empty segment in the editor

### Source volume auto-detection

`load_study_from_pacs()` and `load_series_from_pacs()` iterate loaded node IDs and set the first `vtkMRMLScalarVolumeNode` as `_image_node`. This ensures `setup_editor()` can call `setSourceVolumeNode()` without manual `set_source_volume()`.

## PacsHelper (`helper.py`)

DIMSE (C-FIND + C-GET/C-MOVE) integration via `ctkDICOMQuery` / `ctkDICOMRetrieve`.

- `PacsHelper(host, port, called_aet, calling_aet, prefer_cget, move_aet)` — connection params
- `retrieve_study(study_instance_uid)` → **local-first**: checks `slicer.dicomDatabase` for existing series, falls back to C-FIND + C-GET/C-MOVE from PACS
- `retrieve_series(study_instance_uid, series_instance_uid)` → **local-first**: checks `slicer.dicomDatabase.filesForSeries()`, falls back to C-GET/C-MOVE (no C-FIND)
- Called internally by `SlicerHelper.load_study_from_pacs()` and `load_series_from_pacs()` — not used directly by scripts

### Local-first lookup strategy

Both `retrieve_study()` and `retrieve_series()` check Slicer's local DICOM database (`slicer.dicomDatabase`) before contacting the PACS server. If data is found locally, it is loaded directly via `DICOMUtils.loadSeriesByUID()`, avoiding network round-trips. This makes reopening previously loaded studies/series near-instant.

PACS settings: same `pacs_*` vars as DICOM service — see `clarinet/services/dicom/CLAUDE.md`.

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
- Helper has `_Dummy` stubs so `helper.py` is importable without Slicer
