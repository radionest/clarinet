"""Validator — auto-number segments, check names are digits, immutability check."""

import os
import re

node = slicer.util.getNode("MasterModel")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

# ---------------------------------------------------------------------------
# Auto-numbering: rename non-numeric segments to next available number
# ---------------------------------------------------------------------------
max_num = 0
non_numeric_sids = []

for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    name = seg.GetSegment(sid).GetName()
    if re.match(r"^\d+$", name):
        max_num = max(max_num, int(name))
    else:
        non_numeric_sids.append(sid)

for sid in non_numeric_sids:
    max_num += 1
    seg.GetSegment(sid).SetName(str(max_num))

# ---------------------------------------------------------------------------
# Validation: all segment names must now be digits
# ---------------------------------------------------------------------------
current_names = []
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    name = seg.GetSegment(sid).GetName()
    if not re.match(r"^\d+$", name):
        raise ValueError(f"Invalid segment name '{name}': must be a number")
    current_names.append(name)

# Immutability: if file already exists, all previous names must be preserved
if os.path.isfile(output_file):  # type: ignore[name-defined]  # noqa: F821
    prev_node = slicer.util.loadSegmentation(output_file)  # type: ignore[name-defined]  # noqa: F821
    prev_seg = prev_node.GetSegmentation()
    prev_names = set()
    for i in range(prev_seg.GetNumberOfSegments()):
        sid = prev_seg.GetNthSegmentID(i)
        prev_names.add(prev_seg.GetSegment(sid).GetName())
    missing = prev_names - set(current_names)
    if missing:
        slicer.mrmlScene.RemoveNode(prev_node)  # type: ignore[name-defined]  # noqa: F821
        raise ValueError(f"Cannot remove or rename segments: {missing}")
    slicer.mrmlScene.RemoveNode(prev_node)  # type: ignore[name-defined]  # noqa: F821

export_segmentation("MasterModel", output_file)  # type: ignore[name-defined]  # noqa: F821
