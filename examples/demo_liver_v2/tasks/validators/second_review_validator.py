"""Validator — check classification: _pool empty, segment names intact."""

import vtkSegmentationCorePython as vtkSegCore  # type: ignore[import-not-found]
from vtk.util.numpy_support import vtk_to_numpy  # type: ignore[import-not-found]

node = slicer.util.getNode("Classification")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

# Find _pool segment by name (_find_segment_id is provided by helper.py)
pool_seg_id = _find_segment_id(seg, "_pool")  # type: ignore[name-defined]  # noqa: F821

# Check that _pool is empty (all islands classified), then remove it
if pool_seg_id is not None:
    labelmap = vtkSegCore.vtkOrientedImageData()
    node.GetBinaryLabelmapRepresentation(pool_seg_id, labelmap)

    extent = labelmap.GetExtent()
    # extent[0] > extent[1] means empty extent — segment has no voxels
    if extent[0] <= extent[1]:
        scalars = labelmap.GetPointData().GetScalars()
        if scalars is not None and vtk_to_numpy(scalars).any():
            raise ValueError(
                "Not all missed lesions have been classified. "
                "Use Islands tool to assign remaining _pool islands to a category."
            )

    seg.RemoveSegment(pool_seg_id)

# Validate segment names
expected = {"mts", "unclear", "benign", "invisible"}
current = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    current.add(seg.GetSegment(sid).GetName())

if current != expected:
    raise ValueError(f"Expected segments {expected}, got {current}")

export_segmentation("Classification", output_file)  # type: ignore[name-defined]  # noqa: F821
