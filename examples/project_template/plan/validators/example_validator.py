"""Validator — runs after the operator saves the Slicer segmentation.

Same context variables as the script (see scripts/example.py docstring) plus
the built-in helper ``export_segmentation(node_name, output_file)``.
"""

node = slicer.util.getNode("Segmentation")  # type: ignore[name-defined]  # noqa: F821
seg = node.GetSegmentation()

# TODO: replace with your project's expected segment names.
expected = {"foreground", "background"}
current = set()
for i in range(seg.GetNumberOfSegments()):
    sid = seg.GetNthSegmentID(i)
    current.add(seg.GetSegment(sid).GetName())

if current != expected:
    raise ValueError(f"Expected segments {expected}, got {current}")

export_segmentation("Segmentation", output_file)  # type: ignore[name-defined]  # noqa: F821
