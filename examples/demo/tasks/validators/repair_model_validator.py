"""Validator — check repair model: required structural segments must be present."""

node = slicer.util.getNode("RepairModel")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

names = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    names.add(seg.GetSegment(sid).GetName())

required = {"part_body", "primary_channel", "secondary_channel"}
missing = required - names
if missing:
    raise ValueError(f"Missing required segments: {missing}")

export_segmentation("RepairModel", output_file)  # type: ignore[name-defined]  # noqa: F821
