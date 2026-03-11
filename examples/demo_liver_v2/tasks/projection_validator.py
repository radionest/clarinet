"""Validator — check projection: segment names must match master model."""

import os

node = slicer.util.getNode("Projection")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

current_names = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    current_names.add(seg.GetSegment(sid).GetName())

if os.path.isfile(master_model):  # type: ignore[name-defined]  # noqa: F821
    mm_node = slicer.util.loadSegmentation(master_model)  # type: ignore[name-defined]  # noqa: F821
    mm_seg = mm_node.GetSegmentation()
    mm_names = set()
    for i in range(mm_seg.GetNumberOfSegments()):
        sid = mm_seg.GetNthSegmentID(i)
        mm_names.add(mm_seg.GetSegment(sid).GetName())
    slicer.mrmlScene.RemoveNode(mm_node)  # type: ignore[name-defined]  # noqa: F821
    if current_names != mm_names:
        raise ValueError(f"Projection segments {current_names} don't match master model {mm_names}")

export_segmentation("Projection", output_file)  # type: ignore[name-defined]  # noqa: F821
