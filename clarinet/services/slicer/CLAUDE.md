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

### Module-level functions (no `SlicerHelper` init needed)

- `SlicerHelperError(Exception)` — lightweight exception for helper errors
- `export_segmentation(name, output_path)` → exports segmentation node to file; raises `SlicerHelperError` if node not found or file not created
- `clear_scene()` → calls `slicer.mrmlScene.Clear(0)`

### SlicerHelper class

- `SlicerHelper(working_folder)` — clears scene, sets root dir; tracks observers/shortcuts for cleanup
- `cleanup()` — removes all observers and shortcuts registered by this helper instance
- `load_volume(path, window=)` → image node
- `set_source_volume(node)` — explicitly set the source volume node for segmentation editing
- `create_segmentation(name)` → `SegmentationBuilder` (fluent `.add_segment()`, `.select_segment(name)`)
- `load_segmentation(path, name=None)` → loads existing segmentation from file, sets reference geometry
- `set_segmentation_visibility(segmentation, visible)` — show/hide a segmentation in all views via `SetVisibility()`
- `configure_segment_display(segmentation, segment_name, *, color=, fill_opacity=, outline_opacity=, outline_thickness=)` — per-segment 2D display: color, fill/outline opacity, line thickness. `outline_thickness` is global per segmentation display node
- `setup_editor(seg, effect=, brush_size=, threshold=, source_volume=)` — configures SegmentEditor; `source_volume` overrides `_image_node`
- `set_layout("axial"|"sagittal"|"coronal"|"four_up")`
- `annotate(text)`, `configure_slab(thickness=)`, `setup_edit_mask(path)`
- `add_view_shortcuts()` — a/s/c keys for view switching
- `add_shortcuts(shortcuts: list[tuple[str, str]])` — custom keyboard shortcuts (key→layout or key→exec code); tracked for `cleanup()`
- `load_study_from_pacs(study_instance_uid, *, server_name=, raise_on_empty=True)` → list of loaded MRML node IDs; **auto-sets first scalar volume as `_image_node`**; raises `SlicerHelperError` if no nodes loaded and `raise_on_empty=True` (default). Use `raise_on_empty=False` for optional/fallback loads
- `load_series_from_pacs(study_instance_uid, series_instance_uid, *, server_name=, raise_on_empty=True)` → list of loaded MRML node IDs; **loads only the specified series; auto-sets first scalar volume as `_image_node`**; raises `SlicerHelperError` if no nodes loaded and `raise_on_empty=True` (default). Use `raise_on_empty=False` for optional/fallback loads
- `get_segment_names(segmentation)` → `list[str]` — ordered segment names from a segmentation node
- `get_segment_centroid(segmentation, segment_name)` → `tuple[float,float,float] | None` — extracts per-segment labelmap via `node.GetBinaryLabelmapRepresentation()` and computes tight non-zero voxel center with numpy; handles shared labelmaps and missing extent metadata; observer-safe (no event processing); None if empty
- `copy_segments(source_seg, target_seg, segment_names=None, empty=False)` — copy segments between segmentations; `empty=True` copies only metadata (name + color)
- `sync_segments(source_seg, target_seg, empty=False)` → `list[str]` — copy segments from source missing in target (by name); returns list of added names
- `rename_segments(segmentation, prefix="NEW", color=None, start_from=1)` → `int` — rename all segments to `{prefix}_{N}` with optional color; returns count
- `auto_number_segment(segmentation, prefix="ROI", start_from=None)` → `int` — adds `{prefix}_{N+1}` segment, returns assigned number
- `subtract_segmentations(seg_a, seg_b, output_name=None, max_overlap=0, max_overlap_ratio=None)` — ROI-level subtraction: removes seg_a segments overlapping with seg_b. In-place or new node if `output_name` set
- `binarize_and_split_islands(segmentation, output_name="_BinarizedIslands", min_island_size=1)` — merges all segments into a single binary mask (any label > 0), then splits connected components into individual segments via Islands effect. Returns new segmentation node. Used to convert multi-category segmentations (e.g. mts/unclear/benign) into per-island segments for ROI-level comparison
- `merge_as_pool(source_seg, target_seg, pool_name="_pool", color=(0.5, 0.5, 0.5))` — merges all source segments into a single binary segment in target segmentation. Used for cross-segmentation Islands workflow: the pool segment appears in the target's merged labelmap, enabling ADD_SELECTED_ISLAND to pick islands from it
- `set_dual_layout(volume_a, volume_b, seg_a=None, seg_b=None, linked=True, orientation_a=None, orientation_b=None)` — side-by-side view with Red/Yellow composites, per-view segmentation visibility, and auto-detected orientation per volume (reads IJK-to-RAS direction matrix); pass `orientation_a`/`orientation_b` ("Axial", "Sagittal", "Coronal") to override auto-detection
- `align_by_center(moving_volume, reference_volume, moving_segmentation=None, transform_name="AlignTransform")` → `vtkMRMLLinearTransformNode` — pure translation aligning image centers; applies transform to moving volume (and optional segmentation)
- `refine_alignment_by_centroids(moving_seg, reference_seg, transform_node, min_landmarks=1)` → `int` — computes rigid-body transform (vtkLandmarkTransform / Horn method) from matching segment centroids in LOCAL RAS; replaces matrix on existing transform node; returns number of landmark pairs used (0 = no change). Edge cases: 1 pt = translation, 2 pts = translation + partial rotation, 3+ = full rigid
- `_local_to_world_centroid(segmentation, segment_name)` → `tuple[float,float,float] | None` — converts `get_segment_centroid()` local RAS to world RAS by applying parent MRML transform (if any); returns local directly when no parent transform exists
- `setup_segment_focus_observer(editable_seg, reference_seg, reference_views=, editable_views=, only_empty=, on_refine=)` — auto-navigate to segment centroid on selection; `reference_views` (default `["Red", "Yellow"]`) jump to reference centroid, `editable_views` (default `[]`) jump to editable centroid (falls back to reference if empty); `only_empty=True` (default) skips non-empty segments; `on_refine` optional callback invoked before centroid computation on each segment switch (use to update alignment transforms); observer tracked for `cleanup()`; caches reference centroids (immutable during session); uses `_local_to_world_centroid` for transform-aware coordinates

### VTK / Slicer pitfalls (learned the hard way)

1. **Shared labelmaps (Slicer 5.0+):** `segment.GetRepresentation("Binary labelmap")` returns the *shared* labelmap — same `vtkOrientedImageData` for all segments. Its extent covers the entire volume, so bounding-box center is identical for every segment (= volume center). Use `node.GetBinaryLabelmapRepresentation(seg_id, output)` instead — this is the MRML-node-level API that extracts a **per-segment copy**.

2. **Observer re-entry:** `JumpSlice` and other Slicer operations can trigger `ModifiedEvent` on `vtkMRMLSegmentEditorNode`, re-invoking the callback while it's still running. Always use a `_in_callback` guard flag.

3. **No processEvents() in callbacks:** `SegmentStatistics` and some Slicer utilities call `slicer.app.processEvents()` internally. Inside a VTK observer callback this causes re-entrant event processing → deadlocks. Use only pure VTK + numpy operations.

4. **np.nonzero() on large volumes:** `np.nonzero(arr > 0)` allocates 3 arrays of coordinates for ALL non-zero voxels — millions of int64 entries for a 512x512x300 segment. Use `np.any(mask, axis=(...))` projections instead (three 1D arrays of ~512 entries).

5. **ExportAllSegmentsToLabelmapNode extent (Slicer 5.10):** In Slicer 5.10 the function accepts at most 3 args: `(segmentationNode, labelmapNode, extentComputationMode)`. The 4-arg form with `referenceVolumeNode` does **not** exist. Use `extentComputationMode=0` for reference-geometry-based extent (not `2` as in newer Slicer docs). Without the 3rd arg, each export crops to the segmentation's own bounding box — two segmentations in the same space produce different-shaped arrays, breaking voxel-level comparison. Pattern:
   ```python
   seg.SetReferenceImageGeometryParameterFromVolumeNode(volume)
   seg_logic.ExportAllSegmentsToLabelmapNode(seg, labelmap, 0)
   ```

### Source volume auto-detection

`load_study_from_pacs()` and `load_series_from_pacs()` iterate loaded node IDs and set the first `vtkMRMLScalarVolumeNode` as `_image_node`. This ensures `setup_editor()` can call `setSourceVolumeNode()` without manual `set_source_volume()`.

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
- Helper has `_Dummy` stubs so `helper.py` is importable without Slicer
