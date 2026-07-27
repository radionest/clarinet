"""Slicer script — create 3D repair model.

Loads the master model and allows the expert to segment the part body,
primary/secondary internal channels, and correct defect ROI boundaries for
repair planning.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    master_model: Path to the master model segmentation file (auto, from file_registry).
    output_file: Path to the repair model output file (auto, first OUTPUT file).
    best_study_uid: Anon UID of the part's first study (hydrated, for PACS load).
    pacs_*: PACS connection parameters (auto).
"""

import os

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821

# Load volume from PACS
try:
    s.load_study_from_pacs(best_study_uid, raise_on_empty=False, window=(-200, 300))  # type: ignore[name-defined]
except NameError:
    pass

# Load master model as reference (read-only)
if os.path.isfile(master_model):  # type: ignore[name-defined]  # noqa: F821
    s.load_segmentation(master_model, "MasterModel")  # type: ignore[name-defined]  # noqa: F821

# Load or create repair model
if os.path.isfile(output_file):  # type: ignore[name-defined]  # noqa: F821
    repair_seg = s.load_segmentation(output_file, "RepairModel")  # type: ignore[name-defined]  # noqa: F821
else:
    repair_seg = (
        s.create_segmentation("RepairModel")
        .add_segment("part_body", (0.8, 0.6, 0.4))
        .add_segment("primary_channel", (0.0, 0.0, 1.0))
        .add_segment("secondary_channel", (0.0, 0.5, 1.0))
    )

# Setup editor
s.setup_editor(repair_seg, effect="Paint", brush_size=3.0)
s.set_layout("axial")
s.add_view_shortcuts()

# Add shortcut N to auto-number a new defect ROI segment
s.add_shortcuts([("n", 's.auto_number_segment(repair_seg, prefix="defect_")')])

s.annotate("Repair model — segment part body, channels; press N for new defect ROI")
