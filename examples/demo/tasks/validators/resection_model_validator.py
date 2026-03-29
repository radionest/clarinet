"""Validator — check resection model: required structural segments must be present."""

node = slicer.util.getNode("ResectionModel")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

names = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    names.add(seg.GetSegment(sid).GetName())

required = {"liver", "portal_vein", "hepatic_vein"}
missing = required - names
if missing:
    raise ValueError(f"Missing required segments: {missing}")

export_segmentation("ResectionModel", output_file)  # type: ignore[name-defined]  # noqa: F821
