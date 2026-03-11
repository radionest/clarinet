"""Validator — check segment names and export the Segmentation node."""

node = slicer.util.getNode("Segmentation")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

expected = {"mts", "unclear", "benign"}
current = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    current.add(seg.GetSegment(sid).GetName())

if current != expected:
    raise ValueError(f"Expected segments {expected}, got {current}")

export_segmentation("Segmentation", output_file)  # type: ignore[name-defined]  # noqa: F821
