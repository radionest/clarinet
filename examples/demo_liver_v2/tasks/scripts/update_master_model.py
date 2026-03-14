"""Slicer script — update the master model with new ROIs.

Loads the master model segmentation and allows the expert to add new
numbered ROIs. Press ``N`` to auto-add the next numbered segment.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    master_model: Path to the master model segmentation file (auto, from file_registry).
    output_file: Same as master_model (auto, first OUTPUT file).
    best_study_uid: Anon UID of the patient's first study (hydrated, for PACS load).
    pacs_*: PACS connection parameters (auto).
"""

import os

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821

# Load volume from PACS (best_study_uid is hydrated for PATIENT-level records)
try:
    s.load_study_from_pacs(best_study_uid)  # type: ignore[name-defined]
except NameError:
    pass

# Load or create master model
if os.path.isfile(master_model):  # type: ignore[name-defined]  # noqa: F821
    master_seg = s.load_segmentation(master_model, "MasterModel")  # type: ignore[name-defined]  # noqa: F821
else:
    master_seg = s.create_segmentation("MasterModel")

# Setup editor on master model
s.setup_editor(master_seg, effect="Paint", brush_size=5.0)
s.set_layout("axial")
s.add_view_shortcuts()

# Add shortcut N to auto-number a new ROI segment
s.add_shortcuts([("n", 's.auto_number_segment(master_seg, prefix="")')])

s.annotate("Update master model — press N for new ROI")
