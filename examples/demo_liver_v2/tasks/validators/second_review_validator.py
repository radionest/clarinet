"""Validator — check classification segment names."""

node = slicer.util.getNode("Classification")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

expected = {"mts", "unclear", "benign", "invisible"}
current = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    current.add(seg.GetSegment(sid).GetName())

if current != expected:
    raise ValueError(f"Expected segments {expected}, got {current}")

export_segmentation("Classification", output_file)  # type: ignore[name-defined]  # noqa: F821
