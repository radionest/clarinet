"""
Slicer-side DSL helper for concise workspace setup.

This file is read as text by SlicerService and prepended to every script sent
to 3D Slicer. It also remains importable for testing (with DummySlicer fallback).

Usage inside Slicer (what the user writes)::

    s = SlicerHelper(working_folder)
    vol = s.load_volume(input_volume, window=(-1100, 35))

    seg = s.create_segmentation(segment_name)
    seg.add_segment("LungNodes", color=(0, 0, 1))

    s.setup_editor(seg, effect="Paint", brush_size=30, threshold=(-400, 500))
    s.set_layout("axial")
    s.annotate(patient_name)
    s.configure_slab(thickness=10)
    s.add_view_shortcuts()
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    slicer: Any
    qt: Any
    vtk: Any
    ctk: Any
else:
    try:
        import ctk
        import qt
        import slicer
        import vtk
    except ImportError:

        class _Dummy:
            """Dummy module for running outside 3D Slicer environment."""

            mrmlScene: Any = None
            app: Any = None
            util: Any = None
            vtkMRMLLayoutNode: Any = None
            modules: Any = None  # replaced below

            def __getattr__(self, name: str) -> Any:
                return None

        slicer = _Dummy()
        slicer.modules = _Dummy()
        qt = _Dummy()
        vtk = _Dummy()
        ctk = _Dummy()

EditorEffectName = Literal["Paint", "Erase", "Threshold", "Draw", "Islands"]


class SlicerHelperError(Exception):
    """Error raised by helper functions when Slicer operations fail."""


class OverwriteMode(str, Enum):
    """Segment Editor "Modify other segments" masking mode.

    Mirrors the dropdown in the Segment Editor "Masking" section. The enum
    uses plain string values instead of ``vtkMRMLSegmentEditorNode`` constants
    because ``helper.py`` must stay importable outside Slicer (see the
    ``_Dummy`` fallback at module top): ``slicer.vtkMRMLSegmentEditorNode`` is
    ``None`` there, and referencing ``.OverwriteAllSegments`` at class-body
    evaluation time would crash the import. Integer constants are looked up
    lazily in ``_resolve_overwrite_mode`` at call time, inside Slicer.
    """

    OVERWRITE_ALL = "overwrite_all"
    OVERWRITE_VISIBLE = "overwrite_visible"
    ALLOW_OVERLAP = "allow_overlap"


def _resolve_overwrite_mode(mode: OverwriteMode) -> int:
    """Resolve an ``OverwriteMode`` to its ``vtkMRMLSegmentEditorNode`` constant.

    Called only inside Slicer — the ``slicer`` module is real here, so the
    constants (which may change integer values between Slicer releases) are
    read from the authoritative source instead of being hard-coded.
    """
    if mode == OverwriteMode.OVERWRITE_ALL:
        return int(slicer.vtkMRMLSegmentEditorNode.OverwriteAllSegments)
    elif mode == OverwriteMode.OVERWRITE_VISIBLE:
        return int(slicer.vtkMRMLSegmentEditorNode.OverwriteVisibleSegments)
    elif mode == OverwriteMode.ALLOW_OVERLAP:
        return int(slicer.vtkMRMLSegmentEditorNode.OverwriteNone)
    else:
        raise SlicerHelperError(f"Unsupported overwrite mode: {mode!r}")


def _matrices_match(a: Any, b: Any, tol: float = 0.1) -> bool:
    """Coarse same-grid check: max abs elementwise diff of two vtk 4x4 matrices.

    The same absolute ``tol`` is applied to translation (mm) and to direction
    cosines (dimensionless, |·| <= 1) — tight enough to catch a mask drawn on a
    different study or a flipped axis (grids differ by whole voxels / a sign),
    loose enough to absorb float round-trips through geometry serialization.
    """
    for r in range(3):
        for c in range(4):
            if abs(a.GetElement(r, c) - b.GetElement(r, c)) > tol:
                return False
    return True


def _same_volume_file(a: str, b: str) -> bool:
    """Inode-aware path comparison (Slicer may normalize/realpath stored names)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def find_loaded_volume(path: str | None = None) -> Any:
    """Resolve the reference scalar volume node in the current Slicer scene.

    Args:
        path: If given, return the loaded volume whose storage file is the same
            file (inode-aware), or ``None`` if no loaded volume matches — the
            caller named a specific file, so substituting a different volume as
            the reference grid would defeat the guard. When ``path`` is None,
            falls back to the sole scalar volume, or ``None`` if several are
            loaded (ambiguous reference).

    Returns:
        The matching ``vtkMRMLScalarVolumeNode``, or ``None`` if unresolved.
    """
    volumes = list(slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode"))
    if path:
        for candidate in volumes:
            storage = candidate.GetStorageNode()
            file_name = storage.GetFileName() if storage else None
            if file_name and _same_volume_file(file_name, path):
                return candidate
        return None  # path requested but unmatched — don't substitute a foreign volume
    if len(volumes) == 1:
        return volumes[0]
    return None


def _assert_segmentation_matches_volume(
    segmentation: Any,
    volume_node: Any,
    *,
    tol: float = 0.1,
) -> None:
    """Raise if a segmentation's reference geometry does not match a volume's grid.

    Private: no longer part of the public Slicer-script API (see
    ``export_segmentation``'s ``conform_to`` for the file-export guard). Used
    internally as a fail-fast check at two call sites -- ``load_segmentation``
    (best-effort load-time check) and ``_export_segments_labelmap`` (the
    correspondence-engine set-ops' own pre-regrid check, gated by their
    ``resample=`` parameter). Compares node-to-node VTK matrices directly;
    ``export_segmentation``'s guard instead classifies against a reference
    file's on-disk grid via the bundled ``grid_relation``.

    Compares the segmentation's reference image geometry (dimensions +
    voxel-to-world matrix) against the volume within ``tol``. A no-op when the
    segmentation has no recorded reference geometry or ``volume_node`` is None.

    Args:
        segmentation: ``vtkMRMLSegmentationNode`` or ``SegmentationBuilder``.
        volume_node: Reference ``vtkMRMLScalarVolumeNode`` (e.g. from
            ``find_loaded_volume``). ``None`` skips the check.
        tol: Absolute tolerance for both translation (mm) and direction cosines.

    Raises:
        SlicerHelperError: On dimension or voxel-to-world mismatch.
    """
    if volume_node is None:
        return
    seg_node = getattr(segmentation, "node", segmentation)
    seg = seg_node.GetSegmentation()

    geom_param = slicer.vtkSegmentationConverter.GetReferenceImageGeometryParameterName()
    geom_str = seg.GetConversionParameter(geom_param)
    if not geom_str:
        return  # no reference geometry recorded — nothing to compare

    ref_geom = slicer.vtkOrientedImageData()
    slicer.vtkSegmentationConverter.DeserializeImageGeometry(geom_str, ref_geom, False)

    vol_image = volume_node.GetImageData()
    if vol_image is None:
        raise SlicerHelperError(
            "Reference volume has no image data — cannot verify segmentation geometry "
            "(load the volume before validating a segmentation against it)."
        )

    seg_dims = tuple(ref_geom.GetDimensions())
    vol_dims = tuple(vol_image.GetDimensions())

    # In Slicer "world" space is RAS, so the segmentation's ImageToWorld matrix
    # and the volume's IJKToRAS matrix live in the same space and are directly
    # comparable (no LPS/RAS conversion needed here).
    seg_to_world = vtk.vtkMatrix4x4()
    ref_geom.GetImageToWorldMatrix(seg_to_world)
    vol_to_ras = vtk.vtkMatrix4x4()
    volume_node.GetIJKToRASMatrix(vol_to_ras)

    if seg_dims != vol_dims or not _matrices_match(seg_to_world, vol_to_ras, tol):
        raise SlicerHelperError(
            "Segmentation geometry does not match the volume grid "
            f"(seg dims={seg_dims} vs volume dims={vol_dims}). The mask was drawn or "
            "imported on a foreign grid (different study, or a volume regenerated "
            "with a flipped axis) and would export inconsistent with the volume. "
            "Re-segment on the loaded volume, or conform the saved file to the "
            "volume grid (clarinet.services.image.conform_seg_to_grid)."
        )


# TYPE_CHECKING-only: not a real import at runtime -- clarinet.services.image.grid
# is standalone (numpy + stdlib only) and rides in the same flattened bundle as
# the correspondence engine (see correspondence_bundle.py's _MODULES), exec'd
# into this module's globals only when the caller passes execute(...,
# include_correspondence=True). _read_grid_on_disk() and export_segmentation()
# below read the symbols via globals() (see their guards); this import exists
# solely so mypy can resolve the names and type-check the calls.
if TYPE_CHECKING:
    from clarinet.services.image.grid import Grid, RelationKind, grid_relation


def _flip_lps_ras(affine: Any) -> Any:
    """Self-inverse LPS<->RAS conversion of a 4x4 voxel-to-world affine (negate x, y rows)."""
    flipped = affine.copy()
    flipped[:2, :] *= -1.0
    return flipped


def _read_grid_on_disk(path: str) -> Grid:
    """Read a file's on-disk voxel grid via SimpleITK metadata-only IO.

    The Slicer-side counterpart to ``clarinet.services.image.grid_io.read_grid``
    (unavailable here -- no ``clarinet`` import inside Slicer). Reads metadata
    only (``ReadImageInformation()``), never voxel data. SimpleITK is
    LPS-native like the bundled ``Grid``, so no LPS/RAS conversion is needed --
    and, unlike ``slicer.util.loadVolume`` (which silently flips the slice axis
    of any left-handed/``det<0`` grid on load), it reads the file's grid
    faithfully. A 4-D ``.seg.nrrd`` (Slicer's layered-segmentation format)
    reads as a 3-D vector image, so its spatial grid comes straight off
    ``GetSize()``/``GetSpacing()``/etc. -- no 4-D special-casing.

    Raises:
        SlicerHelperError: The correspondence bundle (carrying ``Grid``) is not
            in this session's namespace -- call ``execute(...,
            include_correspondence=True)``; or *path* cannot be read (missing
            file, corrupt/unsupported header, ...).
    """
    if "Grid" not in globals():
        raise SlicerHelperError(
            "_read_grid_on_disk requires the correspondence bundle; "
            "call execute(..., include_correspondence=True)."
        )
    import numpy as np
    import SimpleITK as sitk

    reader = sitk.ImageFileReader()
    reader.SetFileName(path)
    try:
        reader.ReadImageInformation()
    except Exception as e:
        raise SlicerHelperError(f"Cannot read grid from {path!r}: {e}") from e

    size = reader.GetSize()
    if len(size) != 3:
        raise SlicerHelperError(
            f"Expected a 3-D spatial grid at {path!r}, got {len(size)}-D (size={size})"
        )
    direction = np.array(reader.GetDirection()).reshape(3, 3)
    spacing = reader.GetSpacing()
    origin = reader.GetOrigin()
    return Grid.from_components(
        shape=(int(size[0]), int(size[1]), int(size[2])),
        spacing=(float(spacing[0]), float(spacing[1]), float(spacing[2])),
        origin=(float(origin[0]), float(origin[1]), float(origin[2])),
        direction=direction,
    )


def _node_binary_labelmap_grid(seg_node: Any) -> Grid:
    """Read a segmentation node's current binary-labelmap grid as a bundled ``Grid``.

    Same geometry source as ``_assert_segmentation_matches_volume``: the
    segmentation's recorded reference-image-geometry conversion parameter.
    Slicer "world" space is RAS; the bundled ``Grid`` is LPS by construction,
    so the matrix is flipped via ``_flip_lps_ras`` before building the
    ``Grid``.

    Raises:
        SlicerHelperError: The segmentation has no recorded reference geometry
            yet (nothing was ever loaded or created against a source volume).
    """
    seg = seg_node.GetSegmentation()
    geom_param = slicer.vtkSegmentationConverter.GetReferenceImageGeometryParameterName()
    geom_str = seg.GetConversionParameter(geom_param)
    if not geom_str:
        raise SlicerHelperError(
            "export_segmentation(conform_to=...): segmentation has no recorded "
            "reference geometry to classify -- load or create it against a "
            "source volume first."
        )
    ref_geom = slicer.vtkOrientedImageData()
    slicer.vtkSegmentationConverter.DeserializeImageGeometry(geom_str, ref_geom, False)

    ras_matrix = vtk.vtkMatrix4x4()
    ref_geom.GetImageToWorldMatrix(ras_matrix)

    import numpy as np

    affine_ras = np.array([[ras_matrix.GetElement(r, c) for c in range(4)] for r in range(4)])
    dims = ref_geom.GetDimensions()
    return Grid(shape=(int(dims[0]), int(dims[1]), int(dims[2])), affine=_flip_lps_ras(affine_ras))


def _reindex_segmentation_to_grid(source_node: Any, ref_grid: Grid) -> tuple[Any, Any]:
    """Re-grid *source_node*'s segments onto *ref_grid* by exact index rearrangement.

    Builds a hidden scalar-volume node carrying *ref_grid*'s IJK-to-RAS matrix
    (never a loaded node's -- a loaded volume's matrix may already be
    ITK-canonicalized, which is the bug this module exists to route around)
    and a temporary segmentation node on that grid. Each source segment's
    binary labelmap is read back through ``arrayFromSegmentBinaryLabelmap(...,
    referenceVolumeNode=...)``, which resamples with nearest-neighbor -- exact
    (no interpolation blur) for a REARRANGED (signed-permutation) grid
    relationship. Segments are copied one at a time so overlap between them
    survives; Slicer's own writer decides shared-vs-separate layers at export
    time, so layer structure needs no manual bookkeeping here. Any failure
    mid-loop removes both temp nodes before re-raising.

    *source_node* is only ever read -- never mutated.

    Returns:
        ``(hidden_ref_node, tmp_segmentation_node)`` -- both scene-owned; the
        caller removes them (``slicer.mrmlScene.RemoveNode``) once done.
    """
    import numpy as np

    nx, ny, nz = ref_grid.shape
    placeholder = np.zeros((nz, ny, nx), dtype=np.uint8)
    ras_affine = _flip_lps_ras(ref_grid.affine)
    hidden_ref = slicer.util.addVolumeFromArray(
        placeholder, ijkToRAS=ras_affine, name="_conform_ref"
    )
    hidden_ref.SetHideFromEditors(True)

    tmp_seg = None
    try:
        tmp_seg = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", "_conform_seg")
        tmp_seg.CreateDefaultDisplayNodes()
        tmp_seg.SetHideFromEditors(True)
        tmp_seg.SetReferenceImageGeometryParameterFromVolumeNode(hidden_ref)

        src_seg = source_node.GetSegmentation()
        tmp_vtk_seg = tmp_seg.GetSegmentation()
        for i in range(src_seg.GetNumberOfSegments()):
            seg_id = src_seg.GetNthSegmentID(i)
            segment = src_seg.GetSegment(seg_id)
            arr = slicer.util.arrayFromSegmentBinaryLabelmap(source_node, seg_id, hidden_ref)
            tmp_vtk_seg.AddEmptySegment(seg_id, segment.GetName(), segment.GetColor())
            slicer.util.updateSegmentBinaryLabelmapFromArray(arr, tmp_seg, seg_id, hidden_ref)
    except Exception:
        if tmp_seg is not None:
            slicer.mrmlScene.RemoveNode(tmp_seg)
        slicer.mrmlScene.RemoveNode(hidden_ref)
        raise

    return hidden_ref, tmp_seg


def export_segmentation(name: str, output_path: str, *, conform_to: str | None = None) -> str:
    """Find segmentation node by name, export to file, and verify.

    Args:
        name: Display name of the segmentation node in the scene.
        output_path: Absolute path where the segmentation file will be saved.
        conform_to: Optional path to a reference file (e.g. the series volume)
            whose ON-DISK grid the export must land on. When set, the node's
            current grid is read (never a loaded node's -- see
            ``_read_grid_on_disk``) and classified against the reference's
            on-disk grid via the bundled ``grid_relation``: SAME exports
            directly; REARRANGED re-grids onto the reference by exact index
            rearrangement (no interpolation) before exporting; FOREIGN raises
            without writing. The written file is then re-read and
            re-classified against the reference grid -- a post-write mismatch
            deletes the file and raises (fail-closed: no bad artifact is left
            behind). ``None`` skips all of this and exports the node as-is
            (today's behavior).

    Returns:
        The output_path on success.

    Raises:
        SlicerHelperError: The node is not found; the file was not created;
            ``conform_to`` is set but the script was sent without the
            correspondence bundle (same opt-in contract as
            ``detect_overlaps``/``subtract_segmentations``); ``conform_to``
            names a missing/unreadable file; the node's grid classifies as
            FOREIGN against the reference; or the written file fails the
            post-write SAME re-check.
    """
    seg_node = slicer.util.getNode(name)
    if seg_node is None:
        raise SlicerHelperError(f"Segmentation node '{name}' not found in scene")

    if conform_to is None:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        slicer.util.exportNode(seg_node, output_path)
        if not os.path.isfile(output_path):
            raise SlicerHelperError(f"Export failed: file not created at {output_path}")
        return output_path

    if "grid_relation" not in globals():
        raise SlicerHelperError(
            "export_segmentation(conform_to=...) requires the correspondence bundle; "
            "call execute(..., include_correspondence=True)."
        )

    ref_grid = _read_grid_on_disk(conform_to)
    node_grid = _node_binary_labelmap_grid(seg_node)
    relation = grid_relation(ref_grid, node_grid)
    if relation.kind is RelationKind.FOREIGN:
        raise SlicerHelperError(
            f"export_segmentation: '{name}' is on a grid foreign to reference "
            f"{conform_to!r} -- node {node_grid.summary()} vs reference "
            f"{ref_grid.summary()}. Conform the source before exporting "
            "(clarinet.services.image.conform_seg_to_grid)."
        )

    export_node = seg_node
    hidden_ref = tmp_seg = None
    try:
        if relation.kind is RelationKind.REARRANGED:
            hidden_ref, tmp_seg = _reindex_segmentation_to_grid(seg_node, ref_grid)
            export_node = tmp_seg

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        slicer.util.exportNode(export_node, output_path)
        if not os.path.isfile(output_path):
            raise SlicerHelperError(f"Export failed: file not created at {output_path}")

        written_grid = _read_grid_on_disk(output_path)
        if grid_relation(ref_grid, written_grid).kind is not RelationKind.SAME:
            os.remove(output_path)
            raise SlicerHelperError(
                f"export_segmentation: post-write grid check failed for {output_path!r} "
                f"against reference {conform_to!r} -- the written file was deleted."
            )
    finally:
        if tmp_seg is not None:
            slicer.mrmlScene.RemoveNode(tmp_seg)
        if hidden_ref is not None:
            slicer.mrmlScene.RemoveNode(hidden_ref)

    return output_path


def _labelmap_array_or_raise(labelmap_node: Any, source_node: Any, *, what: str) -> Any | None:
    """Read an exported labelmap as a numpy array; classify an empty export.

    ``ExportAllSegmentsToLabelmapNode`` yields a labelmap with no scalars whenever
    the export lands no voxels on the reference volume grid. Two very different
    causes need opposite handling:

    - **Foreign grid** — the source carries painted voxels in its own grid that
      vanish when re-gridded to the reference extent (a mask saved on a
      flipped/foreign grid: the projection Z-flip bug class).
      ``slicer.util.arrayFromVolume`` would then dereference ``None`` and crash
      with an opaque ``'NoneType' object has no attribute 'GetDataType'``. Raise a
      diagnosable ``SlicerHelperError`` pointing at the on-disk repair instead —
      the set-op companion to the pre-regrid ``_assert_segmentation_matches_volume``.
    - **Genuinely empty** — the source has no voxels anywhere. Pre-guard set-ops
      treated this as a no-op; preserve that. Warn and return ``None`` so the
      caller can short-circuit to its own empty-result path.

    Args:
        labelmap_node: The exported ``vtkMRMLLabelMapVolumeNode``.
        source_node: The segmentation node that was exported. Inspected (native
            per-segment labelmaps, independent of the reference geometry) to tell
            the two empty causes apart.
        what: Human description of the export, for diagnostics.

    Returns:
        The labelmap as a numpy array, or ``None`` when the source is genuinely
        empty (the caller treats this as an empty result / no-op).

    Raises:
        SlicerHelperError: Empty export from a source that *does* carry voxels —
            a flipped/foreign grid that does not overlap the reference extent.
    """
    image = labelmap_node.GetImageData()
    point_data = image.GetPointData() if image is not None else None
    scalars = point_data.GetScalars() if point_data is not None else None
    if scalars is not None:
        return slicer.util.arrayFromVolume(labelmap_node)

    if _segmentation_has_voxels(source_node):
        raise SlicerHelperError(
            f"Exporting {what} produced an empty labelmap although the source carries "
            "voxels — the mask sits on a flipped/foreign grid and does not overlap the "
            "reference volume extent. Conform the file to the volume grid "
            "(clarinet.services.image.conform_seg_to_grid) and retry."
        )

    print(
        f"[SlicerHelper] WARNING: {what} is empty (no voxels on any grid) — treating it "
        "as an empty result (no-op), not a grid mismatch."
    )
    return None


def _find_segment_id(vtk_seg: Any, name: str) -> str | None:
    """Find segment ID by display name.

    Args:
        vtk_seg: A vtkSegmentation object.
        name: Segment display name to search for.

    Returns:
        Segment ID string, or None if not found.
    """
    for i in range(vtk_seg.GetNumberOfSegments()):
        sid = vtk_seg.GetNthSegmentID(i)
        if vtk_seg.GetSegment(sid).GetName() == name:
            return str(sid)
    return None


def clear_scene() -> None:
    """Clear the current Slicer MRML scene."""
    slicer.mrmlScene.Clear(0)


def store_record_id(rid: int) -> None:
    """Store the opened record ID in Slicer module scope for later validation."""
    slicer.modules._clarinet_record_id = rid


def validate_record_id(rid: int) -> None:
    """Check that the current Slicer session was opened with this record.

    Raises:
        SlicerHelperError: If no record was opened or if the IDs don't match.
    """
    stored = getattr(slicer.modules, "_clarinet_record_id", None)
    if stored is None:
        raise SlicerHelperError(
            f"No record was opened in Slicer before validation (expected record_id={rid})"
        )
    if stored != rid:
        raise SlicerHelperError(
            f"Record mismatch: Slicer has record_id={stored}, "
            f"but validation requested for record_id={rid}"
        )


def get_segment_names(segmentation_node: Any) -> list[str]:
    """Get ordered list of segment names from a segmentation node.

    Args:
        segmentation_node: Raw vtkMRMLSegmentationNode.

    Returns:
        List of segment names in index order.
    """
    vtk_seg = segmentation_node.GetSegmentation()
    names: list[str] = []
    for i in range(vtk_seg.GetNumberOfSegments()):
        seg_id = vtk_seg.GetNthSegmentID(i)
        names.append(vtk_seg.GetSegment(seg_id).GetName())
    return names


def _extract_segment_labelmap(
    segmentation_node: Any, segment_id: str
) -> tuple[Any, tuple[int, int, int, int, int, int]] | None:
    """Extract a per-segment binary labelmap and its extent.

    Uses the MRML node-level API ``GetBinaryLabelmapRepresentation`` — NOT
    ``segment.GetRepresentation("Binary labelmap")``, which returns the
    *shared* labelmap whose extent spans every segment combined (Slicer 5.0+
    pitfall; see ``slicer-helper-api.md`` pitfall 1).

    Returns ``None`` when the segment has no allocated extent
    (``extent[0] > extent[1]``), so callers fold the emptiness guard into a
    single ``None`` check.

    Returns:
        ``(labelmap, extent)`` — a ``vtkOrientedImageData`` and its
        ``(xmin, xmax, ymin, ymax, zmin, zmax)`` extent — or ``None``.
    """
    import vtkSegmentationCorePython as vtkSegCore

    labelmap = vtkSegCore.vtkOrientedImageData()
    segmentation_node.GetBinaryLabelmapRepresentation(segment_id, labelmap)

    extent = labelmap.GetExtent()
    if extent[0] > extent[1]:
        return None
    return labelmap, extent


def _labelmap_to_mask(image_data: Any) -> Any | None:
    """Reshape a VTK labelmap's scalars into a boolean numpy mask.

    Follows VTK's Fortran-order reshape convention ``(k, j, i)`` =
    ``(dims[2], dims[1], dims[0])`` (i varies fastest). Returns ``None`` only
    when the labelmap carries no scalars; an all-zero array is returned as-is
    so callers decide emptiness via their own centroid / component logic.
    """
    from vtk.util.numpy_support import vtk_to_numpy

    scalars = image_data.GetPointData().GetScalars()
    if scalars is None:
        return None
    dims = image_data.GetDimensions()
    return vtk_to_numpy(scalars).reshape(dims[2], dims[1], dims[0]) > 0


def _get_segment_mask(segmentation_node: Any, segment_id: str) -> Any | None:
    """Extract a per-segment binary mask as a reshaped numpy bool array.

    Returns ``None`` for any "empty" case — missing labelmap extent, missing
    scalars, or all-zero voxels — so callers can collapse emptiness checks
    and component-counting into a single ``None`` guard.

    Args:
        segmentation_node: Raw vtkMRMLSegmentationNode.
        segment_id: Segment ID within the node's segmentation.

    Returns:
        3D boolean mask with shape ``(dims[2], dims[1], dims[0])`` matching
        the VTK Fortran-order reshape convention, or ``None`` if the segment
        has no non-zero voxels.
    """
    extracted = _extract_segment_labelmap(segmentation_node, segment_id)
    if extracted is None:
        return None
    labelmap, _extent = extracted

    mask = _labelmap_to_mask(labelmap)
    if mask is None or not mask.any():
        return None
    return mask


def is_segment_empty(segmentation_node: Any, segment_id: str) -> bool:
    """Check if a segment has no non-zero voxels.

    Args:
        segmentation_node: Raw vtkMRMLSegmentationNode.
        segment_id: Segment ID within the node's segmentation.

    Returns:
        True if segment is empty (no voxels) or not found.
    """
    return _get_segment_mask(segmentation_node, segment_id) is None


def _segmentation_has_voxels(segmentation_node: Any) -> bool:
    """True if any segment carries non-zero voxels in its native labelmap.

    Distinguishes a genuinely empty segmentation (no voxels anywhere — set-ops
    tolerate it as a no-op) from a foreign-grid mask whose voxels exist in their
    own grid but vanish when re-gridded to the reference extent (the flipped-grid
    bug — set-ops must fail fast). Inspects each segment's native binary labelmap,
    which is independent of the reference geometry, so it stays non-empty for a
    foreign-grid mask even after ``SetReferenceImageGeometryParameterFromVolumeNode``.
    """
    vtk_seg = segmentation_node.GetSegmentation()
    for i in range(vtk_seg.GetNumberOfSegments()):
        seg_id = vtk_seg.GetNthSegmentID(i)
        if not is_segment_empty(segmentation_node, seg_id):
            return True
    return False


def count_segment_components(segmentation_node: Any, segment_name: str) -> int:
    """Count connected components in a named segment.

    Uses per-segment binary labelmap and scipy.ndimage.label with 6-connectivity.

    Args:
        segmentation_node: Raw vtkMRMLSegmentationNode.
        segment_name: Display name of the segment.

    Returns:
        Number of connected components. 0 if segment is empty or not found.
    """
    from scipy.ndimage import label

    vtk_seg = segmentation_node.GetSegmentation()
    seg_id = _find_segment_id(vtk_seg, segment_name)
    if seg_id is None:
        return 0

    mask = _get_segment_mask(segmentation_node, seg_id)
    if mask is None:
        return 0

    _, num_components = label(mask)
    return int(num_components)


LAYOUT_MAP: dict[str, str] = {
    "axial": "SlicerLayoutOneUpRedSliceView",
    "sagittal": "SlicerLayoutOneUpYellowSliceView",
    "coronal": "SlicerLayoutOneUpGreenSliceView",
    "four_up": "SlicerLayoutFourUpView",
}


class SegmentationBuilder:
    """Builder for segmentation with fluent API."""

    def __init__(self, node: Any, image_node: Any) -> None:
        self.node = node
        self._image_node = image_node
        self._segmentation = node.GetSegmentation()

    def add_segment(
        self, name: str, color: tuple[float, float, float] = (1.0, 0.0, 0.0)
    ) -> SegmentationBuilder:
        """Add a segment with name and RGB color. Returns self for chaining."""
        self._segmentation.AddEmptySegment(name, name, color)
        return self

    def select_segment(self, name: str) -> None:
        """Set active segment in editor by name."""
        slicer.util.selectModule("SegmentEditor")
        editor = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        editor.setCurrentSegmentID(name)


_pacs_log = logging.getLogger("clarinet.slicer.pacs")


def _get_pacs_helper(server_name: str | None = None) -> PacsHelper:
    """Create PacsHelper from context variables (injected by Clarinet) or Slicer config.

    Priority:
    1. Context variables (pacs_host, pacs_port, pacs_aet) — set by build_slicer_context()
    2. Slicer QSettings — fallback for standalone/manual usage

    Uses globals() because this module is executed as injected script text inside
    Slicer — context variables are set as module-level globals by SlicerService.
    """
    g = globals()
    if "pacs_host" in g and "pacs_port" in g and "pacs_aet" in g:
        host = g["pacs_host"]
        port = int(g["pacs_port"])
        called_aet = g["pacs_aet"]
        retrieve_mode = g.get("dicom_retrieve_mode", "c-get")
        # calling_aet and move_aet must come from Slicer's own config —
        # each user's Slicer has its own AE title for C-MOVE destination
        slicer_pacs = PacsHelper.from_slicer(server_name)
        _pacs_log.info(
            f"Using PACS from Clarinet settings: {host}:{port} "
            f"(AET={called_aet}, mode={retrieve_mode}, "
            f"move_dest={slicer_pacs.calling_aet})"
        )
        return PacsHelper(
            host=host,
            port=port,
            called_aet=called_aet,
            calling_aet=slicer_pacs.calling_aet,
            retrieve_mode=retrieve_mode,
            move_aet=slicer_pacs.move_aet,
        )
    return PacsHelper.from_slicer(server_name)


class PacsHelper:
    """DSL for PACS query/retrieve inside 3D Slicer via DIMSE.

    Wraps ``ctkDICOMQuery`` + ``ctkDICOMRetrieve`` into concise methods.
    Use ``PacsHelper.from_slicer()`` to read connection params from Slicer's
    DICOM module, or pass them explicitly for testing.
    """

    # Valid retrieve strategies:
    #   c-get        — C-GET per series, fallback to C-MOVE per series
    #   c-get-study  — C-GET whole study, fallback to C-MOVE whole study
    #   c-move       — C-MOVE per series (no C-GET attempt)
    #   c-move-study — C-MOVE whole study (no C-GET attempt)
    VALID_MODES = ("c-get", "c-get-study", "c-move", "c-move-study")

    def __init__(
        self,
        host: str,
        port: int,
        called_aet: str,
        calling_aet: str,
        retrieve_mode: str = "c-get",
        move_aet: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.called_aet = called_aet
        self.calling_aet = calling_aet
        if retrieve_mode not in self.VALID_MODES:
            raise ValueError(
                f"Unsupported retrieve_mode {retrieve_mode!r}. "
                f"Expected one of: {', '.join(self.VALID_MODES)}"
            )
        self.retrieve_mode = retrieve_mode
        self.move_aet = calling_aet if move_aet is None else move_aet

    def verify(self) -> bool:
        """Test PACS connectivity via C-ECHO (DICOM Verification SOP Class).

        Returns True if PACS responds to C-ECHO, False otherwise.
        Logs the result at INFO/ERROR level for diagnostics.
        """
        try:
            echo = ctk.ctkDICOMEcho()
        except AttributeError:
            _pacs_log.warning("C-ECHO not available (ctkDICOMEcho missing in this Slicer version)")
            return True  # assume OK if we can't test

        echo.callingAETitle = self.calling_aet
        echo.calledAETitle = self.called_aet
        echo.host = self.host
        echo.port = self.port

        try:
            ok = bool(echo.echo())
        except Exception as e:
            _pacs_log.error(
                f"C-ECHO exception: {e} "
                f"(calling={self.calling_aet}, called={self.called_aet}, "
                f"host={self.host}:{self.port})"
            )
            return False

        if ok:
            _pacs_log.info(
                f"C-ECHO OK: {self.calling_aet} → {self.host}:{self.port} "
                f"(called={self.called_aet})"
            )
        else:
            _pacs_log.error(
                f"C-ECHO failed: PACS not reachable or rejected association "
                f"(calling={self.calling_aet}, called={self.called_aet}, "
                f"host={self.host}:{self.port}). "
                f"Check: (1) AE title '{self.calling_aet}' is allowed by the PACS ACL "
                f"with the correct IP of this machine; "
                f"(2) PACS AE title '{self.called_aet}' and address "
                f"{self.host}:{self.port} are correct."
            )
        return ok

    @classmethod
    def from_slicer(cls, server_name: str | None = None) -> PacsHelper:
        """Create PacsHelper from Slicer's configured PACS servers.

        Reads connection parameters from Slicer's DICOM module, so each user's
        local Slicer configuration (including their own calling AE title) is used.

        Args:
            server_name: Optional connection name to select a specific server.
                If None, uses the first query/retrieve-enabled server.

        Raises:
            SlicerHelperError: If no PACS server is configured in Slicer.
        """
        # WORKAROUND: Read PACS servers from QSettings instead of
        # ctkDICOMVisualBrowser API.
        #
        # Slicer has two separate server lists that are NOT synchronized:
        #   1. ctkDICOMVisualBrowser — the new visual DICOM browser widget
        #   2. QSettings (DICOM/ServerNodes/*) — persistent storage used by
        #      the "DICOM Query/Retrieve" dialog and Application Settings
        #
        # Users typically configure PACS servers via Edit > Application Settings
        # > DICOM (the Query/Retrieve dialog), but those changes are only
        # persisted to QSettings. The ctkDICOMVisualBrowser loads its own
        # hard-coded defaults (ExampleHost, MedicalConnections) and does NOT
        # reflect servers added through the settings dialog.
        #
        # Additionally, the Calling AET is stored in TWO places that are NOT
        # synchronized:
        #   - Global: QSettings key "CallingAETitle" (no DICOM/ prefix) —
        #     this is what the user sets in Application Settings > DICOM
        #   - Per-server: DICOM/ServerNodes/{i}["Calling AETitle"] — defaults
        #     to "CTK" and is rarely changed by users
        # We read from the global key first, falling back to per-server.
        #
        # Reading from QSettings directly ensures we see the servers that the
        # user actually configured.
        settings = qt.QSettings()
        count = int(settings.value("DICOM/ServerNodeCount", "0"))

        servers: list[dict[str, Any]] = []
        for i in range(count):
            raw = settings.value(f"DICOM/ServerNodes/{i}")
            if raw is None:
                continue
            try:
                text = raw.data() if hasattr(raw, "data") else str(raw)
                data = json.loads(text)
            except (json.JSONDecodeError, AttributeError, TypeError):
                _pacs_log.warning(f"Skipping malformed DICOM/ServerNodes/{i}")
                continue
            servers.append(data)

        # Qt checkbox tri-state: 0 = Unchecked, 1 = PartiallyChecked, 2 = Checked
        _QT_CHECKED = 2

        server: dict[str, Any] | None = None
        if server_name:
            for s in servers:
                if s.get("Name") == server_name:
                    server = s
                    break
            if server is None:
                raise SlicerHelperError(
                    f"PACS server '{server_name}' not found in Slicer. "
                    f"Configure it in Edit > Application Settings > DICOM."
                )
        else:
            for s in servers:
                if int(s.get("QueryRetrieveCheckState", 0)) == _QT_CHECKED:
                    server = s
                    break
            if server is None and servers:
                server = servers[0]

        if server is None:
            raise SlicerHelperError(
                "No PACS server configured in Slicer. "
                "Add one in Edit > Application Settings > DICOM."
            )

        host = server["Address"]
        port = int(server["Port"])
        called_aet = server["Called AETitle"]

        global_aet = settings.value("CallingAETitle")
        if global_aet:
            calling_aet = str(global_aet)
        else:
            per_server_aet = server.get("Calling AETitle", "SLICER")
            calling_aet = str(per_server_aet)
            _pacs_log.warning(
                f"CallingAETitle not set in Slicer global settings, "
                f"using {'per-server' if per_server_aet != 'SLICER' else 'default'} "
                f"value: {calling_aet}. "
                f"Set it in Edit > Application Settings > DICOM > Calling AE Title."
            )

        retrieve_mode = "c-get" if server.get("Retrieve Protocol", "CGET") == "CGET" else "c-move"

        _pacs_log.info(
            f"Using PACS server: {server.get('Name', 'unknown')} "
            f"({host}:{port}, AET={called_aet}, calling={calling_aet})"
        )

        return cls(
            host=host,
            port=port,
            called_aet=called_aet,
            calling_aet=calling_aet,
            retrieve_mode=retrieve_mode,
            move_aet=calling_aet,
        )

    def _retrieve_study_level(self, retrieve: Any, study_instance_uid: str) -> None:
        """Retrieve using study-level DIMSE operations (getStudy/moveStudy)."""
        prefer_cget = self.retrieve_mode.startswith("c-get")
        if prefer_cget:
            _pacs_log.info(f"C-GET study {study_instance_uid} ...")
            ok = retrieve.getStudy(study_instance_uid)
            if not ok:
                _pacs_log.warning(f"C-GET study failed (ok={ok}), falling back to C-MOVE study")
                retrieve.moveDestinationAETitle = self.move_aet
                ok = retrieve.moveStudy(study_instance_uid)
                if not ok:
                    _pacs_log.error(
                        f"C-MOVE study failed (ok={ok}) for {study_instance_uid} "
                        f"(calling={self.calling_aet}, called={self.called_aet}, "
                        f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                    )
        else:
            _pacs_log.info(f"C-MOVE study {study_instance_uid} ...")
            retrieve.moveDestinationAETitle = self.move_aet
            ok = retrieve.moveStudy(study_instance_uid)
            if not ok:
                _pacs_log.error(
                    f"C-MOVE study failed (ok={ok}) for {study_instance_uid} "
                    f"(calling={self.calling_aet}, called={self.called_aet}, "
                    f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                )

    def _retrieve_series_level(
        self, retrieve: Any, study_instance_uid: str, series_instance_uid: str
    ) -> None:
        """Retrieve using series-level DIMSE operations (getSeries/moveSeries)."""
        prefer_cget = self.retrieve_mode.startswith("c-get")
        if prefer_cget:
            _pacs_log.info(f"C-GET series {series_instance_uid} ...")
            ok = retrieve.getSeries(study_instance_uid, series_instance_uid)
            if not ok or not (
                slicer.dicomDatabase and slicer.dicomDatabase.filesForSeries(series_instance_uid)
            ):
                _pacs_log.warning(
                    f"C-GET failed for series {series_instance_uid} (ok={ok}), "
                    f"falling back to C-MOVE"
                )
                retrieve.moveDestinationAETitle = self.move_aet
                ok = retrieve.moveSeries(study_instance_uid, series_instance_uid)
                if not ok:
                    _pacs_log.error(
                        f"C-MOVE series failed (ok={ok}) for {series_instance_uid} "
                        f"(calling={self.calling_aet}, called={self.called_aet}, "
                        f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                    )
        else:
            _pacs_log.info(f"C-MOVE series {series_instance_uid} ...")
            retrieve.moveDestinationAETitle = self.move_aet
            ok = retrieve.moveSeries(study_instance_uid, series_instance_uid)
            if not ok:
                _pacs_log.error(
                    f"C-MOVE series failed (ok={ok}) for {series_instance_uid} "
                    f"(calling={self.calling_aet}, called={self.called_aet}, "
                    f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                )

    def retrieve_study(self, study_instance_uid: str) -> list[str]:
        """Load a DICOM study into the MRML scene (local-first).

        Checks Slicer's local DICOM database first and loads from there if the
        study already exists. Falls back to C-FIND + retrieve from PACS using
        the configured strategy (c-get, c-get-study, c-move, c-move-study).

        Args:
            study_instance_uid: DICOM Study Instance UID to retrieve.

        Returns:
            List of loaded MRML node IDs.
        """
        # 1. Check local Slicer DICOM database
        db = slicer.dicomDatabase
        local_series: list[str] = db.seriesForStudy(study_instance_uid) if db else []
        if local_series:
            from DICOMLib import DICOMUtils

            _pacs_log.info(f"Study {study_instance_uid} found in local DICOM database")
            # list() required: db.seriesForStudy() returns QStringList (tuple in PythonQt),
            # but DICOMUtils.loadSeriesByUID() checks isinstance(x, list)
            loaded: list[str] = DICOMUtils.loadSeriesByUID(list(local_series))
            if loaded:
                return loaded

        # 2. Verify PACS connectivity
        if not self.verify():
            _pacs_log.error("Aborting retrieve — PACS connectivity check failed")
            return []

        # 3. C-FIND: query PACS for the study
        _pacs_log.info(f"C-FIND study {study_instance_uid} ...")
        query = ctk.ctkDICOMQuery()
        query.callingAETitle = self.calling_aet
        query.calledAETitle = self.called_aet
        query.host = self.host
        query.port = self.port
        query.setFilters({"StudyInstanceUID": study_instance_uid})

        temp_db = ctk.ctkDICOMDatabase()
        temp_db.openDatabase("")
        try:
            query.query(temp_db)
        except Exception as e:
            _pacs_log.error(
                f"C-FIND failed for study {study_instance_uid}: {e} "
                f"(calling={self.calling_aet}, called={self.called_aet}, "
                f"host={self.host}:{self.port})"
            )
            temp_db.closeDatabase()
            return []

        series_to_retrieve: list[str] = [
            series_uid
            for study_uid, series_uid in query.studyAndSeriesInstanceUIDQueried
            if study_uid == study_instance_uid
        ]
        _pacs_log.info(f"C-FIND returned {len(series_to_retrieve)} series")
        if not series_to_retrieve:
            _pacs_log.warning(
                f"C-FIND returned 0 series for study {study_instance_uid} — "
                f"study may not exist on PACS or association was rejected"
            )
            temp_db.closeDatabase()
            return []

        # 4. Retrieve into Slicer DICOM database using configured strategy
        retrieve = ctk.ctkDICOMRetrieve()
        retrieve.callingAETitle = self.calling_aet
        retrieve.calledAETitle = self.called_aet
        retrieve.host = self.host
        retrieve.port = self.port
        retrieve.setDatabase(slicer.dicomDatabase)

        _pacs_log.info(
            f"DIMSE retrieve: {self.calling_aet} → {self.host}:{self.port} "
            f"(called={self.called_aet}, mode={self.retrieve_mode}, move_dest={self.move_aet})"
        )

        retrieved_series_uids: list[str] = []
        study_level = self.retrieve_mode.endswith("-study")

        if study_level:
            # Study-level: single getStudy/moveStudy call retrieves all series at once
            self._retrieve_study_level(retrieve, study_instance_uid)
            for series_uid in series_to_retrieve:
                files = db.filesForSeries(series_uid) if db else ()
                if files:
                    _pacs_log.info(f"Retrieved {len(files)} files for series {series_uid}")
                    retrieved_series_uids.append(series_uid)
                else:
                    _pacs_log.error(
                        f"Retrieve failed: 0 files for series {series_uid} "
                        f"(calling={self.calling_aet}, called={self.called_aet}, "
                        f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                    )
        else:
            # Series-level: retrieve each series individually
            for series_uid in series_to_retrieve:
                self._retrieve_series_level(retrieve, study_instance_uid, series_uid)
                files = db.filesForSeries(series_uid) if db else ()
                if files:
                    _pacs_log.info(f"Retrieved {len(files)} files for series {series_uid}")
                    retrieved_series_uids.append(series_uid)
                else:
                    _pacs_log.error(
                        f"Retrieve failed: 0 files for series {series_uid} "
                        f"(calling={self.calling_aet}, called={self.called_aet}, "
                        f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                    )

        # 5. Load ONLY the retrieved series into the MRML scene
        from DICOMLib import DICOMUtils

        loaded_node_ids: list[str] = DICOMUtils.loadSeriesByUID(retrieved_series_uids)

        temp_db.closeDatabase()
        return loaded_node_ids or []

    def retrieve_series(self, study_instance_uid: str, series_instance_uid: str) -> list[str]:
        """Load a single DICOM series into the MRML scene (local-first).

        Checks Slicer's local DICOM database first and loads from there if the
        series already exists. Falls back to retrieve from PACS using the
        configured strategy. With ``-study`` modes, retrieves the entire study
        but only loads the requested series.

        Args:
            study_instance_uid: DICOM Study Instance UID.
            series_instance_uid: DICOM Series Instance UID to retrieve.

        Returns:
            List of loaded MRML node IDs.
        """
        # 1. Check local Slicer DICOM database
        db = slicer.dicomDatabase
        if db and db.filesForSeries(series_instance_uid):
            from DICOMLib import DICOMUtils

            _pacs_log.info(f"Series {series_instance_uid} found in local DICOM database")
            loaded: list[str] = DICOMUtils.loadSeriesByUID([series_instance_uid])
            if loaded:
                return loaded

        # 2. Verify PACS connectivity
        if not self.verify():
            _pacs_log.error("Aborting retrieve — PACS connectivity check failed")
            return []

        # 3. Retrieve from PACS using configured strategy
        retrieve = ctk.ctkDICOMRetrieve()
        retrieve.callingAETitle = self.calling_aet
        retrieve.calledAETitle = self.called_aet
        retrieve.host = self.host
        retrieve.port = self.port
        retrieve.setDatabase(slicer.dicomDatabase)

        _pacs_log.info(
            f"DIMSE retrieve: {self.calling_aet} → {self.host}:{self.port} "
            f"(called={self.called_aet}, mode={self.retrieve_mode}, move_dest={self.move_aet})"
        )

        if self.retrieve_mode.endswith("-study"):
            # Study-level retrieval — downloads entire study, loads requested series
            self._retrieve_study_level(retrieve, study_instance_uid)
        else:
            self._retrieve_series_level(retrieve, study_instance_uid, series_instance_uid)

        # Verify files arrived in the database
        files_after = db.filesForSeries(series_instance_uid) if db else ()
        if not files_after:
            _pacs_log.error(
                f"Retrieve failed: 0 files for series {series_instance_uid} "
                f"(calling={self.calling_aet}, called={self.called_aet}, "
                f"host={self.host}:{self.port}, move_dest={self.move_aet})"
            )
            return []

        _pacs_log.info(f"Retrieved {len(files_after)} files for series {series_instance_uid}")

        from DICOMLib import DICOMUtils

        loaded_node_ids: list[str] = DICOMUtils.loadSeriesByUID([series_instance_uid])
        return loaded_node_ids or []


# Only initialize on first load — subsequent exec() calls must NOT reset
# this to None, otherwise cleanup() in __init__ can't reach the old helper.
# Typed as the base (not SlicerHelper) because __init__ assigns ``self`` from
# inside ``_SlicerHelperBase``; only SlicerHelper is ever instantiated.
if "_current_helper" not in globals():
    _current_helper: _SlicerHelperBase | None = None


class _SlicerHelperBase:
    """Shared state + primitives used across all SlicerHelper mixins."""

    working_folder: str
    _scene: Any
    _layout_manager: Any
    _image_node: Any
    _editor_widget: Any
    _observer_tags: list[tuple[Any, int]]
    _shortcuts: list[Any]

    if TYPE_CHECKING:
        # Cross-mixin method contracts. The real implementations live on the
        # sibling mixins; these stubs are evaluated only by the type checker
        # (no runtime footprint), so mypy resolves sibling `self.`/alias calls
        # through the shared base. SlicerHelper's MRO binds the real methods.
        def load_segmentation(self, path: str, name: str | None = None) -> Any: ...
        def get_segment_names(self, segmentation: SegmentationBuilder | Any) -> list[str]: ...
        def get_segment_centroid(
            self, segmentation: SegmentationBuilder | Any, segment_name: str
        ) -> tuple[float, float, float] | None: ...
        def _local_to_world_centroid(
            self, segmentation: SegmentationBuilder | Any, segment_name: str
        ) -> tuple[float, float, float] | None: ...
        def _local_to_world_island_centroid(
            self, segmentation: SegmentationBuilder | Any, segment_name: str
        ) -> tuple[float, float, float] | None: ...

    def __init__(self, working_folder: str) -> None:
        """Reset views, clear scene, and set root directory.

        Order matters: slice view composite nodes are detached BEFORE
        ``mrmlScene.Clear(0)`` to avoid VTK "Input port 0 has 0 connections"
        warnings that would otherwise flood the Qt event loop and block
        subsequent HTTP requests. Detaching upstream removes the stale
        BackgroundVolumeID/ForegroundVolumeID/LabelVolumeID references
        before Clear deletes the volumes, so no warnings are ever queued
        and an explicit ``processEvents()`` drain is unnecessary — which
        also avoids Qt re-entrancy if this runs inside an HTTP event
        handler.

        Args:
            working_folder: Absolute path to the working directory.
        """
        import gc

        global _current_helper
        if _current_helper is not None:
            _current_helper.cleanup()
        _current_helper = self

        self.working_folder = working_folder
        self._scene = slicer.mrmlScene
        self._layout_manager = slicer.app.layoutManager()
        self._image_node: Any = None
        self._editor_widget: Any = None
        self._observer_tags: list[tuple[Any, int]] = []
        self._shortcuts: list[Any] = []

        # 1. Detach slice view composite nodes first (avoid dangling refs
        # to volumes that Clear(0) is about to delete).
        for name in ("Red", "Yellow", "Green"):
            widget = self._layout_manager.sliceWidget(name)
            if widget is None:
                continue
            composite = widget.mrmlSliceCompositeNode()
            composite.SetBackgroundVolumeID(None)
            composite.SetForegroundVolumeID(None)
            composite.SetLabelVolumeID(None)

        # 2. Clear scene and set working folder.
        self._scene.Clear(0)
        self._scene.SetRootDirectory(working_folder)

        # 3. Force GC + return freed pages to OS.
        gc.collect()
        try:
            import ctypes

            ctypes.cdll.LoadLibrary("libc.so.6").malloc_trim(0)
        except OSError:
            pass  # non-Linux

    @staticmethod
    def _unwrap_node(segmentation: SegmentationBuilder | Any) -> Any:
        """Extract the raw MRML node from a SegmentationBuilder or pass through.

        Uses ``getattr`` fallback because ``isinstance`` may fail when
        the helper runs inside Slicer's ``exec()`` context where class
        identity can differ from the module-level definition.
        """
        if isinstance(segmentation, SegmentationBuilder):
            return segmentation.node
        return getattr(segmentation, "node", segmentation)

    def cleanup(self) -> None:
        """Remove all observers, shortcuts, and MRML references.

        Breaks the ref cycle: ``_observer_tags`` → node → observer callback
        → closure (``helper_ref``) → ``self`` → ``_observer_tags``.
        """
        for node, tag in self._observer_tags:
            with contextlib.suppress(Exception):
                node.RemoveObserver(tag)
        self._observer_tags.clear()

        for shortcut in self._shortcuts:
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._shortcuts.clear()

        self._editor_widget = None
        self._image_node = None

    def _apply_window(self, node: Any, window: tuple[float, float]) -> None:
        display = node.GetScalarVolumeDisplayNode()
        if display is None:
            return
        display.AutoWindowLevelOff()
        display.SetWindowLevelMinMax(window[0], window[1])

    def _apply_reference_geometry(self, node: Any) -> None:
        """Set a segmentation's reference image geometry from the source volume.

        No-op until a source volume (``_image_node``) has been loaded. Centralizes
        the reference-geometry step that ``ExportAllSegmentsToLabelmapNode`` needs
        for a deterministic extent (see ``slicer-helper-api.md`` pitfall 5).
        """
        if self._image_node is not None:
            node.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)


class _VolumeLayoutMixin(_SlicerHelperBase):
    def load_volume(
        self,
        path: str,
        window: tuple[float, float] | None = None,
    ) -> Any:
        """Load volume file (NRRD/NIfTI/DICOM) and optionally set window/level.

        Args:
            path: File path relative to working_folder, or absolute.
            window: Optional (min, max) window level values.

        Returns:
            The loaded image node.
        """
        full_path = path if os.path.isabs(path) else os.path.join(self.working_folder, path)
        self._image_node = slicer.util.loadVolume(full_path)

        if self._image_node is None:
            raise SlicerHelperError(
                f"Failed to load volume: {full_path!r}. "
                f"Check that the file exists and the format is supported."
            )

        if window is not None:
            self._apply_window(self._image_node, window)

        return self._image_node

    def set_layout(self, layout: str) -> None:
        """Set view layout.

        Args:
            layout: One of 'axial', 'sagittal', 'coronal', 'four_up'.
        """
        attr_name = LAYOUT_MAP.get(layout)
        if attr_name is None:
            raise ValueError(f"Unknown layout '{layout}'. Use: {list(LAYOUT_MAP.keys())}")
        layout_id = getattr(slicer.vtkMRMLLayoutNode, attr_name)
        self._layout_manager.setLayout(layout_id)

    def annotate(
        self,
        text: str,
        position: str = "upper_right",
        color: tuple[float, float, float] = (1.0, 0.0, 0.0),
    ) -> None:
        """Add text annotation to the red slice view.

        Args:
            text: Annotation text to display.
            position: VTK corner position name (e.g. 'upper_right', 'upper_left').
            color: RGB color tuple (0-1 range).
        """
        position_map = {
            "upper_right": vtk.vtkCornerAnnotation.UpperRight,
            "upper_left": vtk.vtkCornerAnnotation.UpperLeft,
            "lower_right": vtk.vtkCornerAnnotation.LowerRight,
            "lower_left": vtk.vtkCornerAnnotation.LowerLeft,
        }
        vtk_pos = position_map.get(position, vtk.vtkCornerAnnotation.UpperRight)

        view = self._layout_manager.sliceWidget("Red").sliceView()
        view.cornerAnnotation().SetText(vtk_pos, text)
        view.cornerAnnotation().GetTextProperty().SetColor(*color)
        view.forceRender()

    def configure_slab(self, thickness: float = 10.0, reconstruction_type: int = 1) -> None:
        """Enable slab reconstruction on axial (Red) view.

        Args:
            thickness: Slab thickness in mm.
            reconstruction_type: 0=Max, 1=Mean, 2=Sum.
        """
        slice_node = self._scene.GetNodeByID("vtkMRMLSliceNodeRed")
        slice_node.SlabReconstructionEnabledOn()
        slice_node.SetSlabReconstructionThickness(thickness)
        slice_node.SetSlabReconstructionType(reconstruction_type)

    def setup_edit_mask(self, mask_path: str, segment_id: str = "Segment_1") -> None:
        """Load edit mask segmentation and restrict editing to mask region.

        Args:
            mask_path: Path to the mask segmentation file.
            segment_id: Segment ID within the mask to use as editable area.
        """
        mask_node = self.load_segmentation(mask_path, name="EditMask")

        if self._editor_widget is not None:
            self._editor_widget.setMaskSegmentationNode(mask_node)
            self._editor_widget.setMaskSegmentID(segment_id)
            self._editor_widget.setMaskMode(
                slicer.vtkMRMLSegmentEditorNode.PaintAllowedInsideSingleSegment
            )

    def add_view_shortcuts(self) -> None:
        """Add standard keyboard shortcuts: a/s/c for axial/sagittal/coronal."""
        self.add_shortcuts(
            [
                ("a", "axial"),
                ("s", "sagittal"),
                ("c", "coronal"),
            ]
        )

    def add_shortcuts(self, shortcuts: list[tuple[str, str]]) -> None:
        """Add custom keyboard shortcuts.

        Args:
            shortcuts: List of (key, layout_or_code) tuples. If the value is a
                       known layout name, sets that layout. Otherwise the value
                       is treated as Python code to exec.
        """
        main_window = slicer.util.mainWindow()
        for key, action in shortcuts:
            shortcut = qt.QShortcut(main_window)
            shortcut.setKey(qt.QKeySequence(key))
            if action in LAYOUT_MAP:
                layout_attr = LAYOUT_MAP[action]
                layout_id = getattr(slicer.vtkMRMLLayoutNode, layout_attr)
                shortcut.connect(
                    "activated()",
                    lambda lid=layout_id: self._layout_manager.setLayout(lid),
                )
            else:
                shortcut.connect("activated()", lambda code=action: exec(code))
            self._shortcuts.append(shortcut)

    def _detect_acquisition_orientation(self, volume_node: Any) -> str:
        """Determine natural acquisition plane from volume's direction matrix.

        Args:
            volume_node: Loaded vtkMRMLScalarVolumeNode.

        Returns:
            "Axial", "Sagittal", or "Coronal".
        """
        import numpy as np

        try:
            mat = vtk.vtkMatrix4x4()
            volume_node.GetIJKToRASDirectionMatrix(mat)

            # Third column = slice normal direction
            slice_normal = np.array(
                [
                    mat.GetElement(0, 2),
                    mat.GetElement(1, 2),
                    mat.GetElement(2, 2),
                ]
            )
            dominant = int(np.argmax(np.abs(slice_normal)))
            # 0=L/R → Sagittal, 1=A/P → Coronal, 2=S/I → Axial
            return {0: "Sagittal", 1: "Coronal", 2: "Axial"}[dominant]
        except Exception:
            return "Axial"

    def set_dual_layout(
        self,
        volume_a: Any,
        volume_b: Any,
        seg_a: SegmentationBuilder | Any | None = None,
        seg_b: SegmentationBuilder | Any | None = None,
        linked: bool = True,
        orientation_a: str | None = None,
        orientation_b: str | None = None,
    ) -> None:
        """Set side-by-side layout with two volumes and optional segmentations.

        Args:
            volume_a: Volume node for the left (Red) view.
            volume_b: Volume node for the right (Yellow) view.
            seg_a: Optional segmentation visible only in the left view.
            seg_b: Optional segmentation visible only in the right view.
            linked: If True, link slice navigation between views.
            orientation_a: Orientation for left view ("Axial", "Sagittal",
                "Coronal"). Auto-detected from volume_a if None.
            orientation_b: Orientation for right view ("Axial", "Sagittal",
                "Coronal"). Auto-detected from volume_b if None.
        """
        layout_node = self._layout_manager.layoutLogic().GetLayoutNode()
        layout_node.SetViewArrangement(slicer.vtkMRMLLayoutNode.SlicerLayoutSideBySideView)

        # Configure Red (left) composite
        red_widget = self._layout_manager.sliceWidget("Red")
        red_composite = red_widget.mrmlSliceCompositeNode()
        red_composite.SetBackgroundVolumeID(volume_a.GetID())

        # Configure Yellow (right) composite
        yellow_widget = self._layout_manager.sliceWidget("Yellow")
        yellow_composite = yellow_widget.mrmlSliceCompositeNode()
        yellow_composite.SetBackgroundVolumeID(volume_b.GetID())

        # Restrict segmentation visibility to specific views
        if seg_a is not None:
            node_a = self._unwrap_node(seg_a)
            display_a = node_a.GetDisplayNode()
            if display_a is not None:
                display_a.SetViewNodeIDs(["vtkMRMLSliceNodeRed"])

        if seg_b is not None:
            node_b = self._unwrap_node(seg_b)
            display_b = node_b.GetDisplayNode()
            if display_b is not None:
                display_b.SetViewNodeIDs(["vtkMRMLSliceNodeYellow"])

        # Link slice navigation (SetLinkedControl lives on SliceCompositeNode)
        if linked:
            red_composite = self._scene.GetNodeByID("vtkMRMLSliceCompositeNodeRed")
            yellow_composite = self._scene.GetNodeByID("vtkMRMLSliceCompositeNodeYellow")
            if red_composite is not None:
                red_composite.SetLinkedControl(True)
            if yellow_composite is not None:
                yellow_composite.SetLinkedControl(True)

        self._layout_manager.resetSliceViews()

        # Set per-view orientation (auto-detect from volume if not specified)
        orient_a = orientation_a or self._detect_acquisition_orientation(volume_a)
        orient_b = orientation_b or self._detect_acquisition_orientation(volume_b)

        red_node = self._scene.GetNodeByID("vtkMRMLSliceNodeRed")
        yellow_node = self._scene.GetNodeByID("vtkMRMLSliceNodeYellow")

        if red_node is not None:
            red_node.SetOrientation(orient_a)
        if yellow_node is not None:
            yellow_node.SetOrientation(orient_b)


class _SegmentAnalysisMixin(_SlicerHelperBase):
    def get_segment_names(self, segmentation: SegmentationBuilder | Any) -> list[str]:
        """Get ordered list of segment names from a segmentation node.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.

        Returns:
            List of segment names in index order.
        """
        return get_segment_names(self._unwrap_node(segmentation))

    @staticmethod
    def _bbox_centroid_ras(
        mask: Any,
        extent: tuple[int, int, int, int, int, int],
        image_to_world_source: Any,
    ) -> tuple[float, float, float] | None:
        """RAS bounding-box centroid of a boolean voxel *mask*.

        Uses ``np.any`` axis projections instead of ``np.nonzero`` to avoid
        allocating huge coordinate arrays (~10-50x faster, negligible memory).

        *mask* must follow VTK's Fortran-order reshape
        ``(k, j, i)`` = ``(dims[2], dims[1], dims[0])`` (i varies fastest), so
        ``np.any(mask, axis=(0, 1))`` collapses k and j to a 1-D array along
        the i-axis, and so on for j and k.

        Args:
            mask: 3D boolean array in ``(k, j, i)`` order.
            extent: ``(xmin, xmax, ymin, ymax, zmin, zmax)`` of the labelmap.
            image_to_world_source: Image data to read the IJK→RAS matrix from.

        Returns:
            ``(R, A, S)`` centroid or ``None`` if *mask* is all-False.
        """
        import numpy as np

        i_idx = np.where(np.any(mask, axis=(0, 1)))[0]
        if len(i_idx) == 0:
            return None
        j_idx = np.where(np.any(mask, axis=(0, 2)))[0]
        k_idx = np.where(np.any(mask, axis=(1, 2)))[0]

        ci = (float(i_idx[0]) + float(i_idx[-1])) / 2.0 + extent[0]
        cj = (float(j_idx[0]) + float(j_idx[-1])) / 2.0 + extent[2]
        ck = (float(k_idx[0]) + float(k_idx[-1])) / 2.0 + extent[4]

        mat = vtk.vtkMatrix4x4()
        image_to_world_source.GetImageToWorldMatrix(mat)
        ras = mat.MultiplyPoint([ci, cj, ck, 1.0])
        return (ras[0], ras[1], ras[2])

    @staticmethod
    def _centroid_from_labelmap(
        image_data: Any,
        extent: tuple[int, int, int, int, int, int],
        image_to_world_source: Any,
    ) -> tuple[float, float, float] | None:
        """Compute bounding-box centroid from a VTK labelmap in RAS coords.

        Thin wrapper over :meth:`_bbox_centroid_ras`: turns the labelmap
        scalars into a boolean mask, then delegates. ``image_to_world_source``
        may differ from *image_data* when a filter strips orientation metadata
        from its output.

        Args:
            image_data: ``vtkImageData`` whose scalars contain the mask.
            extent: ``(xmin, xmax, ymin, ymax, zmin, zmax)`` of the labelmap.
            image_to_world_source: Image data to read the IJK→RAS matrix from.

        Returns:
            ``(R, A, S)`` centroid or ``None`` if the mask is empty.
        """
        mask = _labelmap_to_mask(image_data)
        if mask is None:
            return None
        return _SegmentAnalysisMixin._bbox_centroid_ras(mask, extent, image_to_world_source)

    def get_segment_centroid(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
    ) -> tuple[float, float, float] | None:
        """Compute the RAS centroid of a named segment via per-segment labelmap.

        Extracts a per-segment copy of the binary labelmap using the MRML
        node-level API (not the shared representation from the segment
        object). Computes the tight bounding-box center from actual non-zero
        voxels using numpy, which works correctly regardless of shared
        labelmaps or missing extent metadata.

        Safe to call from observer callbacks — no event processing, no
        re-entry risk.

        The per-segment extraction — and the shared-labelmap pitfall it avoids
        (Slicer 5.0+) — lives in :func:`_extract_segment_labelmap`.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            segment_name: Name of the segment to find.

        Returns:
            (R, A, S) centroid tuple, or None if the segment is empty.
        """
        node = self._unwrap_node(segmentation)
        vtk_seg = node.GetSegmentation()

        seg_id = _find_segment_id(vtk_seg, segment_name)
        if seg_id is None:
            return None

        extracted = _extract_segment_labelmap(node, seg_id)
        if extracted is None:
            return None
        labelmap, extent = extracted

        return self._centroid_from_labelmap(labelmap, extent, labelmap)

    def count_segment_components(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
    ) -> int:
        """Count connected components in a named segment.

        Uses per-segment binary labelmap (not shared) and
        ``scipy.ndimage.label`` with default 6-connectivity.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            segment_name: Name of the segment to find.

        Returns:
            Number of connected components. 0 if segment is empty or not found.
        """
        return count_segment_components(self._unwrap_node(segmentation), segment_name)

    def get_largest_island_centroid(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
    ) -> tuple[float, float, float] | None:
        """Compute the RAS centroid of the largest connected component in a segment.

        Like ``get_segment_centroid`` but isolates the largest island first
        via ``scipy.ndimage.label``. Useful for segments that contain
        multiple disconnected regions (e.g. ``_pool`` in second_review) where
        the overall bounding-box center would fall in empty space.

        Safe to call from observer callbacks — pure numpy + scipy, no event
        processing.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            segment_name: Name of the segment to find.

        Returns:
            (R, A, S) centroid of the largest island, or None if empty.
        """
        node = self._unwrap_node(segmentation)
        vtk_seg = node.GetSegmentation()

        seg_id = _find_segment_id(vtk_seg, segment_name)
        if seg_id is None:
            return None

        extracted = _extract_segment_labelmap(node, seg_id)
        if extracted is None:
            return None
        labelmap, extent = extracted

        mask = _labelmap_to_mask(labelmap)
        if mask is None:
            return None

        # Isolate the largest connected component with scipy.ndimage.label.
        #
        # vtkImageConnectivityFilter is NOT usable here: in VTK 9.5 (Slicer
        # 5.10) it ignores the foreground ScalarRange and labels every voxel —
        # background included — as a single region, so "largest region" spans
        # the whole bounding box and the centroid collapses onto
        # get_segment_centroid (dist == 0). scipy is bundled with Slicer and
        # already used by count_segment_components; ndimage.label is pure CPU
        # (no Qt event processing), so it stays observer-callback-safe.
        import numpy as np
        from scipy.ndimage import label

        labeled, num = label(mask)
        if num == 0:
            return None

        # bincount index 0 is background; pick the heaviest foreground label.
        sizes = np.bincount(labeled.ravel())
        sizes[0] = 0
        largest = labeled == int(sizes.argmax())

        return self._bbox_centroid_ras(largest, extent, labelmap)

    def _apply_parent_transform(
        self,
        node: Any,
        local: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        """Apply the node's parent MRML transform to a local RAS point.

        If the node has no parent transform, returns *local* unchanged.
        """
        parent_tf = node.GetParentTransformNode()
        if parent_tf is None:
            return local
        mat = vtk.vtkMatrix4x4()
        parent_tf.GetMatrixTransformToWorld(mat)
        world = mat.MultiplyPoint([local[0], local[1], local[2], 1.0])
        return (world[0], world[1], world[2])

    def _local_to_world_centroid(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
    ) -> tuple[float, float, float] | None:
        """Convert a segment centroid from local RAS to world RAS.

        ``get_segment_centroid`` returns coordinates in the labelmap's own
        image-to-world space (local RAS). If the segmentation node has a
        parent MRML transform (e.g. an alignment transform), this method
        applies it to produce world RAS coordinates suitable for
        ``JumpSlice``.
        """
        local = self.get_segment_centroid(segmentation, segment_name)
        if local is None:
            return None
        return self._apply_parent_transform(self._unwrap_node(segmentation), local)

    def _local_to_world_island_centroid(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
    ) -> tuple[float, float, float] | None:
        """Convert largest-island centroid from local RAS to world RAS.

        Same as ``_local_to_world_centroid`` but delegates to
        ``get_largest_island_centroid`` for segments with disconnected
        islands (e.g. ``_pool``).
        """
        local = self.get_largest_island_centroid(segmentation, segment_name)
        if local is None:
            return None
        return self._apply_parent_transform(self._unwrap_node(segmentation), local)


# TYPE_CHECKING-only: none of these are real imports at runtime — the
# correspondence engine is concatenated as source text (see
# correspondence_bundle.py) and exec'd into this module's globals only when the
# caller passes execute(..., include_correspondence=True). detect_overlaps()
# and subtract_segmentations() below read the symbols via globals() (see their
# guards); these imports exist solely so mypy can resolve the names and
# type-check the calls.
if TYPE_CHECKING:
    from clarinet.services.image.correspondence.graph import build_overlap_graph, correspond
    from clarinet.services.image.correspondence.matching import strategy_from_thresholds
    from clarinet.services.image.correspondence.operations import Difference


class _SegmentEditMixin(_SlicerHelperBase):
    def create_segmentation(self, name: str) -> SegmentationBuilder:
        """Create an empty segmentation node.

        Args:
            name: Display name for the segmentation.

        Returns:
            SegmentationBuilder for fluent segment addition.
        """
        node = self._scene.AddNewNodeByClass("vtkMRMLSegmentationNode", name)
        node.CreateDefaultDisplayNodes()
        self._apply_reference_geometry(node)
        return SegmentationBuilder(node, self._image_node)

    def load_segmentation(self, path: str, name: str | None = None) -> Any:
        """Load existing segmentation from file.

        Args:
            path: File path relative to working_folder, or absolute.
            name: Optional display name. Defaults to filename stem.

        Returns:
            The loaded segmentation node.

        Raises:
            SlicerHelperError: If the file fails to load, or the loaded
                segmentation's reference geometry does not match the source
                volume's grid (fail-fast guard; the node is removed first).
        """
        full_path = path if os.path.isabs(path) else os.path.join(self.working_folder, path)
        seg_node = slicer.util.loadSegmentation(full_path)

        if seg_node is None:
            raise SlicerHelperError(
                f"Failed to load segmentation: {full_path!r}. "
                f"Check that the file exists and the format is supported."
            )

        if name is not None:
            seg_node.SetName(name)

        if self._image_node is not None:
            try:
                _assert_segmentation_matches_volume(seg_node, self._image_node)
            except SlicerHelperError:
                slicer.mrmlScene.RemoveNode(seg_node)
                raise
        self._apply_reference_geometry(seg_node)

        seg_node.CreateDefaultDisplayNodes()
        return seg_node

    def set_source_volume(self, node: Any) -> None:
        """Set the source volume node for segmentation editing.

        Args:
            node: A vtkMRMLScalarVolumeNode to use as source volume.
        """
        self._image_node = node

    def set_segmentation_visibility(
        self,
        segmentation: SegmentationBuilder | Any,
        visible: bool,
    ) -> None:
        """Show or hide a segmentation in all views.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            visible: Whether the segmentation should be visible.
        """
        node = self._unwrap_node(segmentation)
        display = node.GetDisplayNode()
        if display is not None:
            display.SetVisibility(int(visible))

    def configure_segment_display(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
        *,
        color: tuple[float, float, float] | None = None,
        fill_opacity: float | None = None,
        outline_opacity: float | None = None,
        outline_thickness: int | None = None,
    ) -> None:
        """Configure display properties for a single segment.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            segment_name: Name of the segment to configure.
            color: RGB color tuple (0-1 range). None to keep current.
            fill_opacity: 2D fill opacity (0.0 = transparent, 1.0 = opaque).
            outline_opacity: 2D outline opacity (0.0 = hidden, 1.0 = opaque).
            outline_thickness: Slice intersection line thickness in pixels
                (applies to the whole segmentation display node).
        """
        node = self._unwrap_node(segmentation)
        vtk_seg = node.GetSegmentation()
        display = node.GetDisplayNode()

        seg_id = _find_segment_id(vtk_seg, segment_name)
        if seg_id is None:
            return

        if color is not None:
            vtk_seg.GetSegment(seg_id).SetColor(*color)

        if display is not None:
            if fill_opacity is not None:
                display.SetSegmentOpacity2DFill(seg_id, fill_opacity)
            if outline_opacity is not None:
                display.SetSegmentOpacity2DOutline(seg_id, outline_opacity)
            if outline_thickness is not None:
                display.SetSliceIntersectionThickness(outline_thickness)

    def setup_editor(
        self,
        segmentation: SegmentationBuilder | Any,
        effect: EditorEffectName | None = "Paint",
        brush_size: float = 20.0,
        threshold: tuple[float, float] | None = None,
        sphere_brush: bool = True,
        source_volume: Any = None,
        overwrite_mode: OverwriteMode = OverwriteMode.OVERWRITE_ALL,
    ) -> None:
        """Open SegmentEditor and configure tools.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            effect: Effect name to activate, or ``None`` for read-only / observer
                mode — the editor opens with no active drawing tool. Use this
                when the editor is needed only as a container for
                ``setup_segment_focus_observer`` (label navigation in a viewer
                without editing).
            brush_size: Brush diameter in mm.
            threshold: Optional (min, max) threshold values for Threshold effect.
            sphere_brush: Use spherical brush (True) or circular (False).
            source_volume: Override source volume node. Falls back to ``_image_node``.
            overwrite_mode: "Modify other segments" masking mode applied to the
                shared ``vtkMRMLSegmentEditorNode``. Defaults to
                ``OverwriteMode.OVERWRITE_ALL`` so every effect (Paint, Erase,
                Scissors, Islands, ...) forces overwrite regardless of the
                user's persisted Slicer preferences.
        """
        seg_node = self._unwrap_node(segmentation)

        slicer.util.selectModule("SegmentEditor")
        self._editor_widget = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        self._editor_widget.setSegmentationNode(seg_node)

        # Force the masking mode after setSegmentationNode() — Slicer may reset
        # the editor node's state when a new segmentation is attached, so the
        # overwrite mode has to be reapplied every time setup_editor() runs.
        # Ask the widget directly for its parameter-set node instead of scanning
        # the scene with GetFirstNodeByClass(): the scene may hold several
        # vtkMRMLSegmentEditorNode instances and only the widget knows which
        # one actually drives the active editor.
        editor_node = self._editor_widget.mrmlSegmentEditorNode()
        if editor_node is not None:
            editor_node.SetOverwriteMode(_resolve_overwrite_mode(overwrite_mode))

        volume = source_volume if source_volume is not None else self._image_node
        if volume is not None:
            self._editor_widget.setSourceVolumeNode(volume)

        if effect is None:
            # Read-only / observer mode: clear any previously-active tool so
            # Paint/Erase is not silently armed. Empty name deactivates the
            # current effect in Slicer.
            self._editor_widget.setActiveEffectByName("")
        else:
            self._editor_widget.setActiveEffectByName(effect)
            active_effect = self._editor_widget.activeEffect()

            if active_effect is not None:
                if effect in ("Paint", "Erase"):
                    active_effect.setCommonParameter("BrushDiameterIsRelative", 0)
                    active_effect.setCommonParameter("BrushAbsoluteDiameter", brush_size)
                    active_effect.setCommonParameter("BrushSphere", int(sphere_brush))
                elif effect == "Threshold" and threshold is not None:
                    active_effect.setParameter("MinimumThreshold", threshold[0])
                    active_effect.setParameter("MaximumThreshold", threshold[1])
                    active_effect.self().onUseForPaint()
                elif effect == "Islands":
                    active_effect.setParameter("Operation", "ADD_SELECTED_ISLAND")

    def copy_segments(
        self,
        source_seg: SegmentationBuilder | Any,
        target_seg: SegmentationBuilder | Any,
        segment_names: list[str] | None = None,
        empty: bool = False,
    ) -> None:
        """Copy segments from one segmentation to another.

        Args:
            source_seg: Source segmentation (SegmentationBuilder or node).
            target_seg: Target segmentation (SegmentationBuilder or node).
            segment_names: Optional list of segment names to copy. Copies all if None.
            empty: If True, copy only segment metadata (name + color) without data.
        """
        source_node = self._unwrap_node(source_seg)
        target_node = self._unwrap_node(target_seg)
        source_vtk_seg = source_node.GetSegmentation()
        target_vtk_seg = target_node.GetSegmentation()

        for i in range(source_vtk_seg.GetNumberOfSegments()):
            seg_id = source_vtk_seg.GetNthSegmentID(i)
            segment = source_vtk_seg.GetSegment(seg_id)
            name = segment.GetName()

            if segment_names is not None and name not in segment_names:
                continue

            if empty:
                color = segment.GetColor()
                target_vtk_seg.AddEmptySegment(name, name, color)
            else:
                target_vtk_seg.CopySegmentFromSegmentation(source_vtk_seg, seg_id)

    def sync_segments(
        self,
        source_seg: SegmentationBuilder | Any,
        target_seg: SegmentationBuilder | Any,
        empty: bool = False,
    ) -> list[str]:
        """Copy segments from source that are missing in target (by name).

        Args:
            source_seg: Source segmentation with reference segments.
            target_seg: Target segmentation to sync into.
            empty: If True, copy only metadata (name + color) without data.

        Returns:
            List of segment names that were added, in source order.
        """
        source_names = self.get_segment_names(source_seg)
        existing = set(self.get_segment_names(target_seg))
        missing = [name for name in source_names if name not in existing]
        if missing:
            self.copy_segments(source_seg, target_seg, segment_names=missing, empty=empty)
        return missing

    def rename_segments(
        self,
        segmentation: SegmentationBuilder | Any,
        prefix: str = "NEW",
        color: tuple[float, float, float] | None = None,
        start_from: int = 1,
    ) -> int:
        """Rename all segments to {prefix}_{N} with optional color.

        Args:
            segmentation: Segmentation node or SegmentationBuilder.
            prefix: Name prefix for segments.
            color: Optional RGB color tuple (0.0-1.0) to apply to all segments.
            start_from: Starting number for renaming.

        Returns:
            Number of renamed segments.
        """
        node = self._unwrap_node(segmentation)
        vtk_seg = node.GetSegmentation()
        count = vtk_seg.GetNumberOfSegments()
        for i in range(count):
            sid = vtk_seg.GetNthSegmentID(i)
            segment = vtk_seg.GetSegment(sid)
            segment.SetName(f"{prefix}_{start_from + i}")
            if color is not None:
                segment.SetColor(*color)
        return int(count)

    def auto_number_segment(
        self,
        segmentation: SegmentationBuilder | Any,
        prefix: str = "ROI",
        start_from: int | None = None,
    ) -> int:
        """Add a new numbered segment with the next available number.

        Parses existing segment names matching ``{prefix}_{N}`` (or just ``N``
        when *prefix* is empty) to find the highest number, then creates the
        next one.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            prefix: Name prefix for numbered segments. Empty string produces
                plain numeric names (``"1"``, ``"2"``, …).
            start_from: Force a specific number instead of auto-detecting.

        Returns:
            The number assigned to the new segment.
        """
        node = self._unwrap_node(segmentation)
        vtk_seg = node.GetSegmentation()
        names = self.get_segment_names(segmentation)

        if start_from is not None:
            next_num = start_from
        else:
            max_num = 0
            if prefix:
                pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
            else:
                pattern = re.compile(r"^(\d+)$")
            for name in names:
                m = pattern.match(name)
                if m:
                    max_num = max(max_num, int(m.group(1)))
            next_num = max_num + 1

        seg_name = f"{prefix}_{next_num}" if prefix else str(next_num)
        vtk_seg.AddEmptySegment(seg_name, seg_name)
        return next_num

    def _export_segments_labelmap(
        self, node: Any, tmp_name: str, *, what: str, resample: bool = False
    ) -> tuple[Any, Any | None]:
        """Export all segments of *node* into a fresh temp labelmap volume.

        Applies the source-volume reference geometry first, then exports with
        ``extentComputationMode=0`` (reference-geometry extent — see
        ``slicer-helper-api.md`` pitfall 5) so repeated exports share one grid.
        The caller owns the returned node and must ``RemoveNode()`` it; when
        the foreign-grid guard raises, the temp node is already removed.

        Unless *resample* is set, a non-empty source is checked against the
        source volume's grid before re-gridding (see
        ``_assert_segmentation_matches_volume``) — a partially-overlapping
        misaligned grid would otherwise export non-empty-but-wrong voxels and
        slip through the empty/foreign-grid guard below undetected.

        Returns:
            ``(labelmap_node, array)`` — the temp ``vtkMRMLLabelMapVolumeNode``
            and its voxels as a numpy array; the array is ``None`` when the
            source is genuinely empty (see ``_labelmap_array_or_raise``).

        Raises:
            SlicerHelperError: Empty export from a source that carries voxels —
                a flipped/foreign grid that does not overlap the reference
                extent (see ``_labelmap_array_or_raise``); or, when *resample*
                is False, a non-empty source whose recorded reference geometry
                does not match the source volume's grid (see
                ``_assert_segmentation_matches_volume``).
        """
        if not resample and _segmentation_has_voxels(node):
            _assert_segmentation_matches_volume(node, self._image_node)
        self._apply_reference_geometry(node)
        seg_logic = slicer.modules.segmentations.logic()
        labelmap = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", tmp_name)
        seg_logic.ExportAllSegmentsToLabelmapNode(node, labelmap, 0)
        try:
            arr = _labelmap_array_or_raise(labelmap, node, what=what)
        except SlicerHelperError:
            slicer.mrmlScene.RemoveNode(labelmap)
            raise
        return labelmap, arr

    def subtract_segmentations(
        self,
        seg_a: SegmentationBuilder | Any,
        seg_b: SegmentationBuilder | Any,
        output_name: str | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
        resample: bool = False,
        *,
        strategy: Any = None,
        granularity: Literal["label", "union"] = "label",
    ) -> Any:
        """ROI-level subtraction backed by the shared correspondence engine.

        Removal decisions run the same path as the image service's
        ``Segmentation.difference``: ``correspond()`` -> ``Difference()`` ->
        ``KeepPlan``. Operands enter the graph labeled per segment (segment ``i`` ->
        label ``i+1``): a multi-island segment is scored as one label, unlike
        server-side autolabel arrays where each connected component is its own label.
        Scalar thresholds derive the strategy via the bundled
        ``strategy_from_thresholds``: with ``max_overlap_ratio`` set the ratio wins (a
        segment is removed iff ``overlap / size >= max_overlap_ratio`` against its
        best-scoring counterpart; ``max_overlap`` is ignored), otherwise a segment is
        removed when its largest single-pair overlap exceeds ``max_overlap`` voxels.

        Args:
            seg_a: Segmentation to subtract from (SegmentationBuilder or node).
            seg_b: Segmentation to subtract (SegmentationBuilder or node).
            output_name: If set, create a new node with surviving segments instead
                         of modifying seg_a in-place. Slicer-only node handling —
                         not part of the shared engine parameter set.
            max_overlap: Maximum allowed per-pair overlap voxel count.
                Ignored when ``strategy`` or ``max_overlap_ratio`` is set.
            max_overlap_ratio: Maximum allowed overlap ratio (overlap/segment size).
                Takes precedence over ``max_overlap``; boundary is ``>=``.
            resample: If True, skip the source-vs-volume geometry check and re-grid a
                mismatched input onto the reference extent (legacy behavior). Default
                False raises SlicerHelperError on a grid mismatch. Re-gridding is
                runtime-native (labelmap re-export here, ``reindex_to`` server-side) —
                near-threshold verdicts may differ between runtimes on this path.
            strategy: Matching-strategy override built from bundle symbols, e.g.
                ``ThresholdMatch(IoU(), min_score=0.5)``. When set, the scalar
                thresholds are ignored.
            granularity: How seg_b enters the overlap graph. ``"label"`` (default):
                each subtracted segment scored separately, matching
                ``Segmentation.difference``. ``"union"``: all subtracted segments
                flattened to one mask — each base segment is scored against their
                combined extent (the legacy sum-over-union rule).

        Returns:
            The output segmentation node (new node if output_name, else seg_a node).

        Raises:
            SlicerHelperError: The script was sent without the correspondence
                bundle — call ``execute(..., include_correspondence=True)`` (raises
                regardless of operand content); unknown ``granularity``; or a grid
                mismatch was detected (see ``_export_segments_labelmap``).
        """
        import numpy as np

        if "correspond" not in globals():
            raise SlicerHelperError(
                "subtract_segmentations requires the correspondence bundle; "
                "call execute(..., include_correspondence=True)."
            )
        if granularity not in ("label", "union"):
            raise SlicerHelperError(f"granularity must be 'label' or 'union', got {granularity!r}")

        node_a = self._unwrap_node(seg_a)
        node_b = self._unwrap_node(seg_b)

        # Export both with mode 0 (reference geometry extent) → same shape
        labelmap_b, arr_b = self._export_segments_labelmap(
            node_b, "_sub_b", what="the subtracted segmentation (seg_b)", resample=resample
        )
        try:
            labelmap_a, arr_a = self._export_segments_labelmap(
                node_a, "_sub_a", what="the base segmentation (seg_a)", resample=resample
            )
        except SlicerHelperError:
            slicer.mrmlScene.RemoveNode(labelmap_b)
            raise

        vtk_seg_a = node_a.GetSegmentation()
        segments_to_remove: list[str] = []

        try:
            # A None array means a source was genuinely empty (already warned, tolerated):
            # an empty base subtracts to itself, an empty subtrahend removes nothing —
            # either way no segment is dropped, so skip the engine entirely.
            if arr_a is not None and arr_b is not None:
                if granularity == "union":
                    arr_b = (arr_b > 0).astype(np.uint8)
                if strategy is None:
                    strategy = strategy_from_thresholds(max_overlap, max_overlap_ratio)
                # Labelmap array axes are (z,y,x); GetSpacing() is (x,y,z) — reverse to match.
                sx, sy, sz = labelmap_a.GetSpacing()
                corr = correspond(arr_a, arr_b, spacing=(sz, sy, sx), strategy=strategy)
                keep = {lbl for lbl, _out in Difference()(corr).from_a}
                # Segments absent from the export (0 voxels) never enter the graph:
                # keep them, as the legacy loop did.
                present = {int(v) for v in np.unique(arr_a) if v}
                for i in range(vtk_seg_a.GetNumberOfSegments()):
                    label = i + 1  # merged labelmap: segment 0 → label 1
                    if label in present and label not in keep:
                        segments_to_remove.append(vtk_seg_a.GetNthSegmentID(i))
        finally:
            slicer.mrmlScene.RemoveNode(labelmap_a)
            slicer.mrmlScene.RemoveNode(labelmap_b)

        if output_name is not None:
            # Create new segmentation with surviving segments
            output_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", output_name)
            output_node.CreateDefaultDisplayNodes()
            self._apply_reference_geometry(output_node)
            output_vtk = output_node.GetSegmentation()
            for i in range(vtk_seg_a.GetNumberOfSegments()):
                seg_id = vtk_seg_a.GetNthSegmentID(i)
                if seg_id not in segments_to_remove:
                    output_vtk.CopySegmentFromSegmentation(vtk_seg_a, seg_id)
            return output_node

        # In-place removal
        for seg_id in segments_to_remove:
            vtk_seg_a.RemoveSegment(seg_id)
        return node_a

    def detect_overlaps(
        self,
        seg_a: SegmentationBuilder | Any,
        seg_b: SegmentationBuilder | Any,
        *,
        resample: bool = False,
    ) -> list[dict[str, Any]]:
        """Non-destructive per-segment-pair overlap report between two segmentations.

        Exports both segmentations to integer labelmaps with the same
        reference-geometry/grid-guard export as ``subtract_segmentations``, then
        delegates the pairwise overlap computation to the bundled
        ``build_overlap_graph`` (see ``correspondence_bundle.py``). Neither input's
        segment data is modified; as in ``subtract_segmentations``, the shared
        export first sets each input's reference-geometry metadata to the source
        volume.

        Args:
            seg_a: First segmentation (SegmentationBuilder or node).
            seg_b: Second segmentation (SegmentationBuilder or node).
            resample: If True, skip the source-vs-volume geometry check and re-grid a
                mismatched input onto the reference extent (legacy behavior). Default
                False raises SlicerHelperError on a grid mismatch.

        Returns:
            One dict per overlapping segment pair (``inter > 0``): ``name_a``,
            ``name_b``, ``inter``, ``size_a``, ``size_b``, ``dice``, ``iou``,
            ``centroid_distance_mm``. Empty list when the segmentations are
            disjoint, or when either source is genuinely empty.

        Raises:
            SlicerHelperError: The script was sent without the correspondence
                bundle — call ``execute(..., include_correspondence=True)``; or
                a grid mismatch was detected (see ``_export_segments_labelmap``).

        Note:
            3D Slicer reuses one exec-namespace across all HTTP calls in a
            session, so once any script sends ``execute(...,
            include_correspondence=True)``, the bundle's symbols (including
            ``build_overlap_graph``) persist in that namespace and are never
            popped. The guard above is therefore session-order-dependent: it
            will not raise in a session where a prior script already opted
            in, even when the current script omits the bundle.
        """
        if "build_overlap_graph" not in globals():
            raise SlicerHelperError(
                "detect_overlaps requires the correspondence bundle; "
                "call execute(..., include_correspondence=True)."
            )
        node_a = self._unwrap_node(seg_a)
        node_b = self._unwrap_node(seg_b)

        labelmap_b, arr_b = self._export_segments_labelmap(
            node_b, "_ov_b", what="seg_b", resample=resample
        )
        try:
            labelmap_a, arr_a = self._export_segments_labelmap(
                node_a, "_ov_a", what="seg_a", resample=resample
            )
        except SlicerHelperError:
            slicer.mrmlScene.RemoveNode(labelmap_b)
            raise

        try:
            if arr_a is None or arr_b is None:  # genuinely-empty source (PR#413) → no-op
                return []
            # Spacing from the exported labelmap (arr_a's own grid) — definitionally
            # correct; equals _image_node's on the shared-grid path, but avoids the
            # None fallback and the coupling to the source-volume node.
            sx, sy, sz = labelmap_a.GetSpacing()
            # Labelmap array axes are (z,y,x); GetSpacing() is (x,y,z) — reverse to match.
            graph = build_overlap_graph(arr_a, arr_b, spacing=(sz, sy, sx))
            names_a, names_b = get_segment_names(node_a), get_segment_names(node_b)
            results: list[dict[str, Any]] = []
            for e in graph.edges:
                # Labels are the merged labelmap's sequential values (segment i → label i+1);
                # guard the back-index so a non-sequential label fails diagnosably, not via IndexError.
                if not (0 < e.a <= len(names_a) and 0 < e.b <= len(names_b)):
                    raise SlicerHelperError(
                        f"detect_overlaps: labelmap label out of range "
                        f"(a={e.a}/{len(names_a)}, b={e.b}/{len(names_b)})."
                    )
                denom = e.size_a + e.size_b
                union = denom - e.inter
                results.append(
                    {
                        "name_a": names_a[e.a - 1],
                        "name_b": names_b[e.b - 1],
                        "inter": e.inter,
                        "size_a": e.size_a,
                        "size_b": e.size_b,
                        "dice": (2 * e.inter / denom) if denom else 0.0,
                        "iou": (e.inter / union) if union else 0.0,
                        "centroid_distance_mm": e.centroid_distance,
                    }
                )
            return results
        finally:
            slicer.mrmlScene.RemoveNode(labelmap_a)
            slicer.mrmlScene.RemoveNode(labelmap_b)

    def binarize_and_split_islands(
        self,
        segmentation: SegmentationBuilder | Any,
        output_name: str = "_BinarizedIslands",
        min_island_size: int = 1,
        resample: bool = False,
    ) -> Any:
        """Binarize all segments into one mask, then split into connected components.

        Merges all segments into a single binary labelmap (any label > 0),
        then uses the Islands effect to split connected components into
        individual segments.

        Args:
            segmentation: Input segmentation (SegmentationBuilder or node).
            output_name: Name for the output segmentation node.
            min_island_size: Minimum island size in voxels (smaller removed).
            resample: If True, skip the source-vs-volume geometry check and re-grid a
                mismatched input onto the reference extent (legacy behavior). Default
                False raises SlicerHelperError on a grid mismatch.

        Returns:
            A new vtkMRMLSegmentationNode with one segment per connected component.
        """
        import numpy as np

        node = self._unwrap_node(segmentation)

        seg_logic = slicer.modules.segmentations.logic()

        # Phase A — merge all segments into a single binary labelmap
        labelmap, arr = self._export_segments_labelmap(
            node, "_bin_tmp", what="the segmentation to binarize", resample=resample
        )
        if arr is None:
            # Genuinely empty source — no islands to split. Return an empty node.
            slicer.mrmlScene.RemoveNode(labelmap)
            output_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", output_name)
            output_node.CreateDefaultDisplayNodes()
            self._apply_reference_geometry(output_node)
            return output_node
        arr_binary = (arr > 0).astype(np.uint8)
        slicer.util.updateVolumeFromArray(labelmap, arr_binary)

        output_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", output_name)
        output_node.CreateDefaultDisplayNodes()
        self._apply_reference_geometry(output_node)
        seg_logic.ImportLabelmapToSegmentationNode(labelmap, output_node)
        slicer.mrmlScene.RemoveNode(labelmap)

        # Phase B — split islands via Segment Editor Islands effect
        merged_seg_id = output_node.GetSegmentation().GetNthSegmentID(0)

        editor_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentEditorNode", "_bin_editor")
        editor_node.SetSelectedSegmentID(merged_seg_id)

        widget = slicer.qMRMLSegmentEditorWidget()
        widget.setMRMLScene(slicer.mrmlScene)
        widget.setMRMLSegmentEditorNode(editor_node)
        widget.setSegmentationNode(output_node)
        if self._image_node is not None:
            widget.setSourceVolumeNode(self._image_node)

        widget.setActiveEffectByName("Islands")
        effect = widget.activeEffect()
        effect.setParameter("Operation", "SPLIT_ISLANDS_TO_SEGMENTS")
        effect.setParameter("MinimumSize", str(min_island_size))
        effect.self().onApply()

        slicer.mrmlScene.RemoveNode(editor_node)
        widget.deleteLater()

        return output_node

    def merge_as_pool(
        self,
        source_seg: SegmentationBuilder | Any,
        target_seg: SegmentationBuilder | Any,
        pool_name: str = "_pool",
        color: tuple[float, float, float] = (0.5, 0.5, 0.5),
        resample: bool = False,
    ) -> None:
        """Merge all source segments into a single binary segment in the target.

        Exports all source segments to a labelmap, binarizes (any label > 0),
        and imports the result into the target segmentation as a single segment.
        Useful for cross-segmentation Islands workflow: the pool segment becomes
        visible in the target's merged labelmap, allowing ADD_SELECTED_ISLAND
        to pick islands from it.

        Args:
            source_seg: Source segmentation (SegmentationBuilder or node).
            target_seg: Target segmentation (SegmentationBuilder or node).
            pool_name: Name for the pool segment in the target.
            color: RGB color tuple (0-1 range) for the pool segment.
            resample: If True, skip the source-vs-volume geometry check and re-grid a
                mismatched input onto the reference extent (legacy behavior). Default
                False raises SlicerHelperError on a grid mismatch.
        """
        import numpy as np

        source_node = self._unwrap_node(source_seg)
        target_node = self._unwrap_node(target_seg)

        # Apply reference geometry to the target (it is imported into below);
        # the source's is applied inside _export_segments_labelmap.
        self._apply_reference_geometry(target_node)

        seg_logic = slicer.modules.segmentations.logic()

        # Export source → binarize
        labelmap, arr = self._export_segments_labelmap(
            source_node, "_pool_tmp", what="the pool source segmentation", resample=resample
        )
        if arr is None:
            # Genuinely empty source — nothing to pool (pre-guard no-op).
            slicer.mrmlScene.RemoveNode(labelmap)
            return
        arr_binary = (arr > 0).astype(np.uint8)
        slicer.util.updateVolumeFromArray(labelmap, arr_binary)

        # Import into target as a single segment
        vtk_seg = target_node.GetSegmentation()
        ids_before = {vtk_seg.GetNthSegmentID(i) for i in range(vtk_seg.GetNumberOfSegments())}
        seg_logic.ImportLabelmapToSegmentationNode(labelmap, target_node)
        slicer.mrmlScene.RemoveNode(labelmap)

        # Derive the new segment ID from set difference
        ids_after = {vtk_seg.GetNthSegmentID(i) for i in range(vtk_seg.GetNumberOfSegments())}
        new_ids = ids_after - ids_before
        if not new_ids:
            return  # Source was empty — nothing imported

        # Rename the imported segment to pool_name and set color
        pool_seg_id = new_ids.pop()
        pool_segment = vtk_seg.GetSegment(pool_seg_id)
        pool_segment.SetName(pool_name)
        pool_segment.SetColor(*color)


class _PacsLoadMixin(_SlicerHelperBase):
    def _post_pacs_load(
        self,
        node_ids: list[str] | None,
        window: tuple[float, float] | None,
        raise_on_empty: bool,
        empty_message: str,
    ) -> list[str]:
        """Finalize a PACS load: adopt the first scalar volume, enforce non-empty.

        Sets ``_image_node`` to the first loaded ``vtkMRMLScalarVolumeNode`` (and
        applies *window* if given) so the Segment Editor has a source volume,
        then raises ``SlicerHelperError(empty_message)`` when nothing was loaded
        and *raise_on_empty* is set.

        Returns:
            The (never-``None``) list of loaded MRML node IDs.
        """
        node_ids = node_ids or []
        for nid in node_ids:
            node = self._scene.GetNodeByID(nid)
            if node is not None and node.IsA("vtkMRMLScalarVolumeNode"):
                self._image_node = node
                if window is not None:
                    self._apply_window(node, window)
                break

        if raise_on_empty and not node_ids:
            raise SlicerHelperError(empty_message)
        return node_ids

    def load_study_from_pacs(
        self,
        study_instance_uid: str,
        *,
        server_name: str | None = None,
        raise_on_empty: bool = True,
        window: tuple[float, float] | None = None,
    ) -> list[str]:
        """Load a DICOM study from PACS into the current scene.

        Uses PACS connection params from Clarinet settings (injected via context
        variables). Falls back to Slicer's DICOM module config if context is absent.

        Args:
            study_instance_uid: DICOM Study Instance UID to retrieve.
            server_name: Optional PACS server name configured in Slicer
                (only used in fallback mode).
            raise_on_empty: If True (default), raise SlicerHelperError when no
                DICOM nodes are loaded. Set to False for optional/fallback loads.
            window: Optional (min, max) window level values for the loaded volume.

        Returns:
            List of loaded MRML node IDs.

        Raises:
            SlicerHelperError: If no nodes were loaded and raise_on_empty is True.
        """
        pacs = _get_pacs_helper(server_name)
        node_ids = pacs.retrieve_study(study_instance_uid)
        return self._post_pacs_load(
            node_ids,
            window,
            raise_on_empty,
            f"No DICOM nodes loaded for study '{study_instance_uid}'. "
            f"Check PACS configuration in Edit > Application Settings > DICOM.",
        )

    def load_series_from_pacs(
        self,
        study_instance_uid: str,
        series_instance_uid: str,
        *,
        server_name: str | None = None,
        raise_on_empty: bool = True,
        window: tuple[float, float] | None = None,
    ) -> list[str]:
        """Load a single DICOM series from PACS into the current scene.

        Uses PACS connection params from Clarinet settings (injected via context
        variables). Falls back to Slicer's DICOM module config if context is absent.

        Args:
            study_instance_uid: DICOM Study Instance UID.
            series_instance_uid: DICOM Series Instance UID to retrieve.
            server_name: Optional PACS server name configured in Slicer
                (only used in fallback mode).
            raise_on_empty: If True (default), raise SlicerHelperError when no
                DICOM nodes are loaded. Set to False for optional/fallback loads.
            window: Optional (min, max) window level values for the loaded volume.

        Returns:
            List of loaded MRML node IDs.

        Raises:
            SlicerHelperError: If no nodes were loaded and raise_on_empty is True.
        """
        pacs = _get_pacs_helper(server_name)
        node_ids = pacs.retrieve_series(study_instance_uid, series_instance_uid)
        return self._post_pacs_load(
            node_ids,
            window,
            raise_on_empty,
            f"No DICOM nodes loaded for series '{series_instance_uid}' "
            f"(study '{study_instance_uid}'). "
            f"Check PACS configuration in Edit > Application Settings > DICOM.",
        )

    def download_series_zip(
        self,
        study_uid: str,
        series_uid: str,
        server_url: str,
        auth_cookie: str,
    ) -> str:
        """Download DICOM series ZIP from Clarinet, extract, import into Slicer DB.

        Alternative to DIMSE retrieval — downloads via HTTP from Clarinet's
        DICOMweb cache endpoint.

        Args:
            study_uid: DICOM Study Instance UID.
            series_uid: DICOM Series Instance UID.
            server_url: Clarinet API base URL (e.g. "http://host:8000").
            auth_cookie: Cookie header value (e.g. "clarinet_session=token").

        Returns:
            Path to the directory containing extracted DICOM files.
        """
        import shutil
        import tempfile
        import urllib.request
        import zipfile

        url = f"{server_url}/dicom-web/studies/{study_uid}/series/{series_uid}/archive"
        req = urllib.request.Request(url)
        req.add_header("Cookie", auth_cookie)

        extract_dir = os.path.join(self.working_folder, "_dicom_download", series_uid)
        os.makedirs(extract_dir, exist_ok=True)

        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)  # noqa: SIM115
        tmp_path = tmp.name
        tmp.close()
        try:
            with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as f:
                shutil.copyfileobj(resp, f)
            with zipfile.ZipFile(tmp_path) as zf:
                zf.extractall(extract_dir)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # Import into Slicer DICOM database (if running inside Slicer)
        try:
            from DICOMLib import DICOMUtils

            DICOMUtils.importDicom(extract_dir)
        except ImportError:
            pass

        return extract_dir


class _AlignmentMixin(_SlicerHelperBase):
    def align_by_center(
        self,
        moving_volume: Any,
        reference_volume: Any,
        moving_segmentation: SegmentationBuilder | Any | None = None,
        transform_name: str = "AlignTransform",
    ) -> Any:
        """Align two volumes by translating their image centers.

        Creates a ``vtkMRMLLinearTransformNode`` that shifts *moving_volume*
        so its center coincides with *reference_volume*'s center. Optionally
        applies the same transform to a segmentation.

        Args:
            moving_volume: Volume node to be transformed.
            reference_volume: Target volume node (stays in place).
            moving_segmentation: Optional segmentation to co-transform.
            transform_name: MRML node name for the transform.

        Returns:
            The created ``vtkMRMLLinearTransformNode``.
        """
        ref_bounds = [0.0] * 6
        mov_bounds = [0.0] * 6
        reference_volume.GetRASBounds(ref_bounds)
        moving_volume.GetRASBounds(mov_bounds)

        ref_center = [(ref_bounds[i] + ref_bounds[i + 1]) / 2.0 for i in (0, 2, 4)]
        mov_center = [(mov_bounds[i] + mov_bounds[i + 1]) / 2.0 for i in (0, 2, 4)]

        mat = vtk.vtkMatrix4x4()
        mat.Identity()
        for i in range(3):
            mat.SetElement(i, 3, ref_center[i] - mov_center[i])

        tf_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLinearTransformNode",
            transform_name,
        )
        tf_node.SetMatrixTransformToParent(mat)

        # Chain with any pre-existing transform (e.g. acquisition transform
        # added by Slicer's DICOM loader for irregular geometry).
        # Result: volume → existing_tf → alignment_tf → world
        existing_tf = moving_volume.GetParentTransformNode()
        if existing_tf is not None:
            existing_tf.SetAndObserveTransformNodeID(tf_node.GetID())
        else:
            moving_volume.SetAndObserveTransformNodeID(tf_node.GetID())

        if moving_segmentation is not None:
            self._unwrap_node(moving_segmentation).SetAndObserveTransformNodeID(tf_node.GetID())

        # Reset slice views so they recompute their extent/range to account
        # for the newly transformed volume position.  Without this, scrolling
        # in one view sends the other view outside the transformed data → black.
        self._layout_manager.resetSliceViews()

        return tf_node

    def refine_alignment_by_centroids(
        self,
        moving_seg: SegmentationBuilder | Any,
        reference_seg: SegmentationBuilder | Any,
        transform_node: Any,
        min_landmarks: int = 1,
    ) -> int:
        """Refine alignment using matching segment centroids.

        Collects centroids of segments present in both segmentations,
        computes a rigid-body transform via ``vtkLandmarkTransform``
        (Horn method), and **replaces** the matrix on *transform_node*.

        Moving centroids are in LOCAL RAS (before transform); reference
        centroids are in world RAS (reference has no parent transform).

        Args:
            moving_seg: Moving segmentation (under *transform_node*).
            reference_seg: Reference segmentation (stationary).
            transform_node: Transform node to update in-place.
            min_landmarks: Minimum landmark pairs required to update.

        Returns:
            Number of landmark pairs used (0 means no change applied).
        """
        moving_names = set(self.get_segment_names(moving_seg))
        ref_names = set(self.get_segment_names(reference_seg))
        common = moving_names & ref_names
        print(
            f"[Alignment] common segments: {sorted(common)} (mov={len(moving_names)}, ref={len(ref_names)})"
        )

        source_pts = vtk.vtkPoints()
        target_pts = vtk.vtkPoints()

        for name in sorted(common):
            mov_c = self.get_segment_centroid(moving_seg, name)
            ref_c = self.get_segment_centroid(reference_seg, name)
            if mov_c is None or ref_c is None:
                print(f"[Alignment] skip '{name}': mov={mov_c}, ref={ref_c}")
                continue
            print(
                f"[Alignment] pair '{name}': "
                f"mov=({mov_c[0]:.1f}, {mov_c[1]:.1f}, {mov_c[2]:.1f}) "
                f"ref=({ref_c[0]:.1f}, {ref_c[1]:.1f}, {ref_c[2]:.1f})"
            )
            source_pts.InsertNextPoint(mov_c)
            target_pts.InsertNextPoint(ref_c)

        n_pairs = source_pts.GetNumberOfPoints()
        if n_pairs < min_landmarks:
            return 0

        landmark_tf = vtk.vtkLandmarkTransform()
        landmark_tf.SetSourceLandmarks(source_pts)
        landmark_tf.SetTargetLandmarks(target_pts)
        landmark_tf.SetModeToRigidBody()
        landmark_tf.Update()

        matrix = vtk.vtkMatrix4x4()
        landmark_tf.GetMatrix(matrix)
        transform_node.SetMatrixTransformToParent(matrix)

        # Reset slice views so they recompute extent for the new transform
        # (same as align_by_center — without this, views go black).
        self._layout_manager.resetSliceViews()

        return int(n_pairs)


class _ObserverMixin(_SlicerHelperBase):
    def setup_segment_focus_observer(
        self,
        editable_seg: SegmentationBuilder | Any,
        reference_seg: SegmentationBuilder | Any,
        reference_views: list[str] | None = None,
        editable_views: list[str] | None = None,
        only_empty: bool = True,
        on_refine: Any | None = None,
        island_segments: list[str] | None = None,
    ) -> None:
        """Auto-navigate to segment centroid when selecting a segment.

        When the user selects a segment in the editor, navigates configured
        views to the centroid of the matching segment. Reference views always
        jump to the reference segmentation centroid. Editable views jump to
        the editable segmentation centroid (or fall back to reference if empty).

        For segments listed in ``island_segments``, the centroid of the
        **largest connected component** is used instead of the overall
        bounding-box center (via ``get_largest_island_centroid``). This
        prevents navigation to empty space between disconnected islands
        (e.g. ``_pool`` in second_review). Island centroids are never
        cached because the segment content changes as the user classifies.

        .. note:: **Observer design — lessons learned**

           This callback is attached to ``vtkMRMLSegmentEditorNode`` via
           ``vtkCommand.ModifiedEvent``. Several VTK/Slicer behaviors
           required workarounds:

           1. **Re-entry guard** (``_in_callback``): ``JumpSlice`` can
              trigger further ModifiedEvents on the editor node (e.g. slice
              position changes propagate back). Without the guard the
              callback recurses until Python hits the stack limit or Slicer
              hangs.

           2. **No slicer.app.processEvents()**: Earlier versions used
              ``SegmentStatistics`` which internally calls
              ``processEvents()``. Inside a VTK observer callback this
              causes re-entrant event processing — the callback fires
              again while still executing, leading to deadlocks. All code
              here is pure VTK + numpy with no event processing.

           3. **Per-segment extraction, not SegmentStatistics**: The
              ``SegmentStatistics`` module is convenient but heavyweight
              (processes ALL segments, calls ``processEvents()``). We only
              need one segment's centroid, so direct labelmap extraction
              via ``get_segment_centroid()`` is both faster and
              observer-safe.

        .. note:: **Performance optimizations**

           Each ``get_segment_centroid()`` call does one VTK labelmap
           extraction + one numpy scan. The original code did up to 3
           extractions per click:

           - ``_is_segment_empty()`` → extraction #1 (just to check extent)
           - ``get_segment_centroid(reference)`` → extraction #2
           - ``get_segment_centroid(editable)`` → extraction #3

           Optimizations applied:

           - **Merged emptiness check**: ``get_segment_centroid()`` returns
             ``None`` for empty segments, so a separate ``_is_segment_empty``
             extraction is redundant. We call ``get_segment_centroid()`` on
             the editable node first and use its return value as the
             emptiness signal.
           - **Reference centroid cache** (``_ref_centroids``): The reference
             segmentation (e.g. MasterModel) doesn't change during a
             session. After the first click on a segment, its reference
             centroid is cached. Subsequent clicks skip the VTK extraction
             entirely.

           Result: first click = 2 extractions, subsequent clicks = 1.

        Args:
            editable_seg: The segmentation being edited.
            reference_seg: Reference segmentation with populated segments.
            reference_views: Views to navigate to reference centroid.
                Defaults to ``["Red", "Yellow"]``.
            editable_views: Views to navigate to editable centroid.
                Defaults to ``[]`` (no editable navigation).
            only_empty: If True (default), only navigate when the selected
                segment is empty. If False, navigate for all segments.
            on_refine: Optional callback invoked before centroid computation
                on each segment switch. Use to update alignment transforms
                so that centroids reflect the latest registration.
            island_segments: Segment names whose centroid should be
                computed via largest-island extraction (no cache).
        """
        if reference_views is None:
            reference_views = ["Red", "Yellow"]
        if editable_views is None:
            editable_views = []
        _island_set = set(island_segments) if island_segments else set()

        editable_node = self._unwrap_node(editable_seg)
        reference_node = self._unwrap_node(reference_seg)

        # Ask the widget for its parameter-set node instead of scanning the
        # scene with GetFirstNodeByClass(): the scene may hold several
        # vtkMRMLSegmentEditorNode instances and only the widget knows which
        # one actually drives the active editor (mirrors setup_editor at
        # helper.py:1052-1058).
        if self._editor_widget is None:
            raise SlicerHelperError(
                "setup_segment_focus_observer() requires the segment editor "
                "widget. Call setup_editor() first (pass effect=None for a "
                "viewer that only needs label navigation, no active drawing "
                "tool)."
            )
        editor_node = self._editor_widget.mrmlSegmentEditorNode()

        helper_ref = self  # prevent garbage collection of SlicerHelper

        # Reference segmentation (e.g. MasterModel) is immutable during the
        # session — cache centroids to avoid repeated VTK extraction + numpy.
        _ref_centroids: dict[str, tuple[float, float, float] | None] = {}

        def _jump_views(view_names: list[str], centroid: tuple[float, float, float]) -> None:
            r, a, s = centroid
            for name in view_names:
                node = helper_ref._scene.GetNodeByID(f"vtkMRMLSliceNode{name}")
                if node is not None:
                    node.JumpSlice(r, a, s)

        # Re-entry guard: JumpSlice can trigger ModifiedEvent on the editor
        # node, which would re-invoke this callback. See docstring note #1.
        _in_callback = False

        def _handle_island_segment(segment_name: str) -> None:
            """Navigate all views to the largest-island centroid (no cache).

            Island segments (e.g. ``_pool``) are always navigated regardless
            of ``only_empty`` — their content changes as the user classifies,
            so recomputing each time is intentional.
            """
            print(f"[SegFocus] island segment '{segment_name}' — computing largest-island centroid")

            # Compute separate centroids for each view set: reference and
            # editable may be in different world spaces after alignment.
            ref_centroid = helper_ref._local_to_world_island_centroid(reference_node, segment_name)
            edit_centroid = helper_ref._local_to_world_island_centroid(editable_node, segment_name)

            if ref_centroid is None and edit_centroid is None:
                print(f"[SegFocus] island segment '{segment_name}' is empty, skipping")
                return

            if ref_centroid is not None and reference_views:
                _jump_views(reference_views, ref_centroid)
                print(
                    f"[SegFocus] jumped reference views {reference_views} to island centroid "
                    f"R={ref_centroid[0]:.1f}, A={ref_centroid[1]:.1f}, S={ref_centroid[2]:.1f}"
                )

            if edit_centroid is not None and editable_views:
                _jump_views(editable_views, edit_centroid)
                print(
                    f"[SegFocus] jumped editable views {editable_views} to island centroid "
                    f"R={edit_centroid[0]:.1f}, A={edit_centroid[1]:.1f}, S={edit_centroid[2]:.1f}"
                )

        def _handle_regular_segment(segment_name: str, seg_id: str) -> None:
            """Navigate reference/editable views using cached centroids."""
            # Editable centroid doubles as emptiness check: None = empty.
            edit_centroid = helper_ref._local_to_world_centroid(editable_node, segment_name)
            empty = edit_centroid is None
            print(f"[SegFocus] selected: '{segment_name}' (id={seg_id}, empty={empty})")

            if only_empty and not empty:
                print(f"[SegFocus] skipping non-empty segment (only_empty={only_empty})")
                return

            # Lookup reference centroid (cached after first computation).
            if segment_name not in _ref_centroids:
                ref_centroid = helper_ref._local_to_world_centroid(reference_node, segment_name)
                _ref_centroids[segment_name] = ref_centroid
                print(
                    f"[SegFocus] ref centroid computed and cached for '{segment_name}' (cache_id={id(_ref_centroids):#x})"
                )
            else:
                ref_centroid = _ref_centroids[segment_name]
                print(
                    f"[SegFocus] ref centroid from cache for '{segment_name}' (cache_id={id(_ref_centroids):#x})"
                )

            if ref_centroid is None:
                print(f"[SegFocus] no centroid for '{segment_name}' in reference, skipping")
                return

            print(
                f"[SegFocus] ref centroid: R={ref_centroid[0]:.1f}, "
                f"A={ref_centroid[1]:.1f}, S={ref_centroid[2]:.1f}"
            )
            _jump_views(reference_views, ref_centroid)
            print(f"[SegFocus] jumped reference views {reference_views}")

            if editable_views:
                target = edit_centroid or ref_centroid
                _jump_views(editable_views, target)
                source = "edit" if edit_centroid else "ref (predicted)"
                print(
                    f"[SegFocus] jumped editable views {editable_views} "
                    f"(using {source}: R={target[0]:.1f}, A={target[1]:.1f}, S={target[2]:.1f})"
                )

        def on_segment_changed(caller: Any, _event: Any) -> None:
            nonlocal _in_callback
            if _in_callback:
                return
            _in_callback = True
            try:
                seg_id = caller.GetSelectedSegmentID()
                if seg_id is None:
                    print("[SegFocus] no segment selected, skipping")
                    return

                vtk_seg = editable_node.GetSegmentation()
                segment = vtk_seg.GetSegment(seg_id)
                if segment is None:
                    print(f"[SegFocus] segment {seg_id} not found, skipping")
                    return

                segment_name = segment.GetName()

                # Run refinement callback (e.g. update alignment transform)
                # BEFORE centroid computation so coordinates are up-to-date.
                if on_refine is not None:
                    on_refine()

                if segment_name in _island_set:
                    _handle_island_segment(segment_name)
                else:
                    _handle_regular_segment(segment_name, seg_id)
            except Exception as e:
                print(f"[SlicerHelper] segment focus error: {e}")
            finally:
                _in_callback = False

        tag = editor_node.AddObserver(vtk.vtkCommand.ModifiedEvent, on_segment_changed)
        self._observer_tags.append((editor_node, tag))


class SlicerHelper(
    _VolumeLayoutMixin,
    _SegmentAnalysisMixin,
    _SegmentEditMixin,
    _PacsLoadMixin,
    _AlignmentMixin,
    _ObserverMixin,
):
    """DSL for concise 3D Slicer workspace setup.

    Provides a high-level API that wraps verbose Slicer Python calls into
    short, chainable methods. Designed to run inside the 3D Slicer environment.
    """
