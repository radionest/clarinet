---
paths:
  - "clarinet/services/slicer/helper.py"
---

# SlicerHelper Full API Reference

## Module-level functions

- `SlicerHelperError(Exception)` — lightweight exception for helper errors
- `export_segmentation(name, output_path)` → exports segmentation node to file
- `clear_scene()` → calls `slicer.mrmlScene.Clear(0)`
- `OverwriteMode` — str enum for Segment Editor "Modify other segments" masking mode: `OVERWRITE_ALL` (default in `setup_editor`), `OVERWRITE_VISIBLE`, `ALLOW_OVERLAP`. Resolved to `vtkMRMLSegmentEditorNode` constants lazily inside Slicer so the module stays importable under the `_Dummy` fallback

## SlicerHelper class

- `SlicerHelper(working_folder)` — clears scene, sets root dir; tracks observers/shortcuts for cleanup
- `cleanup()` — removes all observers and shortcuts
- `load_volume(path, window=)` → image node
- `set_source_volume(node)` — set source volume for segmentation editing
- `create_segmentation(name)` → `SegmentationBuilder` (fluent `.add_segment()`, `.select_segment(name)`)
- `load_segmentation(path, name=None)` → loads segmentation from file, sets reference geometry
- `set_segmentation_visibility(segmentation, visible)` — show/hide via `SetVisibility()`
- `configure_segment_display(segmentation, segment_name, *, color=, fill_opacity=, outline_opacity=, outline_thickness=)` — per-segment 2D display
- `setup_editor(seg, effect=, brush_size=, threshold=, sphere_brush=, source_volume=, overwrite_mode=OverwriteMode.OVERWRITE_ALL)` — configures SegmentEditor. The overwrite mode is reapplied on every call because Slicer may reset editor-node state when a new segmentation is attached
- `set_layout("axial"|"sagittal"|"coronal"|"four_up")`
- `annotate(text)`, `configure_slab(thickness=)`, `setup_edit_mask(path)`
- `add_view_shortcuts()` — a/s/c keys for view switching
- `add_shortcuts(shortcuts: list[tuple[str, str]])` — custom keyboard shortcuts
- `load_study_from_pacs(study_instance_uid, *, server_name=, raise_on_empty=True, window=)` → list of MRML node IDs; auto-sets first scalar volume as `_image_node`
- `load_series_from_pacs(study_instance_uid, series_instance_uid, *, server_name=, raise_on_empty=True, window=)` → list of MRML node IDs; loads only specified series
- `get_segment_names(segmentation)` → `list[str]`
- `get_segment_centroid(segmentation, segment_name)` → `tuple[float,float,float] | None` — per-segment labelmap center via numpy
- `get_largest_island_centroid(segmentation, segment_name)` → `tuple[float,float,float] | None` — largest connected component centroid via `vtkImageConnectivityFilter`; use for multi-island segments like `_pool`
- `copy_segments(source_seg, target_seg, segment_names=None, empty=False)`
- `sync_segments(source_seg, target_seg, empty=False)` → `list[str]` — copy missing segments by name
- `rename_segments(segmentation, prefix="NEW", color=None, start_from=1)` → `int`
- `auto_number_segment(segmentation, prefix="ROI", start_from=None)` → `int`
- `subtract_segmentations(seg_a, seg_b, output_name=None, max_overlap=0, max_overlap_ratio=None)` — ROI-level subtraction
- `binarize_and_split_islands(segmentation, output_name="_BinarizedIslands", min_island_size=1)` — merge + connected components split
- `merge_as_pool(source_seg, target_seg, pool_name="_pool", color=(0.5, 0.5, 0.5))` — merge into single binary segment
- `set_dual_layout(volume_a, volume_b, seg_a=None, seg_b=None, linked=True, orientation_a=None, orientation_b=None)` — side-by-side view with auto-detected orientation
- `align_by_center(moving_volume, reference_volume, moving_segmentation=None, transform_name="AlignTransform")` → translation transform
- `refine_alignment_by_centroids(moving_seg, reference_seg, transform_node, min_landmarks=1)` → `int` — rigid-body from matched centroids
- `setup_segment_focus_observer(editable_seg, reference_seg, reference_views=, editable_views=, only_empty=, on_refine=, island_segments=)` — auto-navigate to centroid on selection; segments in `island_segments` use largest-component centroid (no cache)

## PacsHelper methods

- `PacsHelper.verify() -> bool` — test PACS connectivity via C-ECHO (`ctkDICOMEcho`). Returns True on success, False on failure. Logs diagnostics (ACL, AE title, IP). Graceful fallback if `ctkDICOMEcho` unavailable.

## VTK / Slicer pitfalls

1. **Shared labelmaps (Slicer 5.0+):** `segment.GetRepresentation("Binary labelmap")` returns the *shared* labelmap — same for all segments. Use `node.GetBinaryLabelmapRepresentation(seg_id, output)` instead.

2. **Observer re-entry:** `JumpSlice` can trigger `ModifiedEvent` re-invoking callbacks. Always use `_in_callback` guard.

3. **No processEvents() in callbacks:** Causes re-entrant event processing → deadlocks. Use only pure VTK + numpy.

4. **np.nonzero() on large volumes:** Allocates millions of int64 entries. Use `np.any(mask, axis=(...))` projections instead.

5. **ExportAllSegmentsToLabelmapNode extent (Slicer 5.10):** Max 3 args: `(segNode, labelmapNode, extentComputationMode)`. Use `extentComputationMode=0` for reference-geometry-based extent:
   ```python
   seg.SetReferenceImageGeometryParameterFromVolumeNode(volume)
   seg_logic.ExportAllSegmentsToLabelmapNode(seg, labelmap, 0)
   ```

## Slicer exec protocol

Scripts sent via `POST /slicer/exec` return JSON through `__execResult`, NOT stdout.

- `__execResult = {"key": "value"}` → HTTP 200, response body = `{"key": "value"}`
- `print(...)` → visible in Slicer console only, NOT in HTTP response
- Script error → HTTP 500, `{"success": false, "message": "..."}`
- No `__execResult` set → HTTP 200, response body = `{}`

In e2e tests, always use `__execResult = {...}` and assert on response keys.
