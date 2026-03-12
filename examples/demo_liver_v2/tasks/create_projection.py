"""Slicer script — project master model onto a target study.

Dual-viewport workflow: master model + volume on the left, target volume +
empty projection on the right. Auto-navigates to master ROI centroid when
selecting an empty projection segment.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    master_model: Path to the master model segmentation file (auto, from file_registry).
    master_projection: Path to the projection output (auto, from file_registry).
    output_file: Same as master_projection (auto, first OUTPUT file).
    target_study_uid: DICOM Study Instance UID for the target volume (custom arg).
    pacs_*: PACS connection parameters (auto).
"""

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clarinet.services.slicer.helper import SlicerHelper  # type: ignore[import]
s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821

# Load master volume (first loaded node is the volume)
master_node_ids = s.load_study_from_pacs(target_study_uid)  # type: ignore[name-defined]  # noqa: F821
target_vol = slicer.mrmlScene.GetNodeByID(master_node_ids[0])  # type: ignore[name-defined]  # noqa: F821

# Load master model segmentation
master_seg = s.load_segmentation(master_model, "MasterModel")  # type: ignore[name-defined]  # noqa: F821

# Create empty projection with same segment structure
if os.path.isfile(master_projection):  # type: ignore[name-defined]  # noqa: F821
    projection = s.load_segmentation(master_projection, "Projection")  # type: ignore[name-defined]  # noqa: F821
else:
    projection = s.create_segmentation("Projection")
    s.copy_segments(master_seg, projection, empty=True)

# Side-by-side: master on left, target + projection on right
s.set_dual_layout(target_vol, target_vol, seg_a=master_seg, seg_b=projection, linked=True)

# Setup editor on projection
s.setup_editor(projection, effect="Paint", brush_size=5.0)

# Auto-navigate to master ROI when selecting empty projection segment
s.setup_segment_focus_observer(projection, master_seg)

s.annotate("Project master model ROIs onto target study")
