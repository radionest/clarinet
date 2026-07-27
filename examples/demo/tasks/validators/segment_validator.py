"""Validator — check segment names and export the Segmentation node."""

from utils.seg_utils import SEG_LABELS

node = slicer.util.getNode("Segmentation")  # type: ignore[name-defined]  # noqa: F821
if node is None:
    raise ValueError("Segmentation node 'Segmentation' not found in scene")
seg = node.GetSegmentation()

expected = set(SEG_LABELS.keys())
current = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    current.add(seg.GetSegment(sid).GetName())

if current != expected:
    raise ValueError(f"Expected segments {expected}, got {current}")

export_segmentation("Segmentation", output_file)  # type: ignore[name-defined]  # noqa: F821
