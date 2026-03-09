"""Slicer script — update the master model with new ROIs.

Loads the master model segmentation and allows the expert to add new
numbered ROIs. Press ``N`` to auto-add the next numbered segment.

Context variables (injected by SlicerService):
    working_folder: Absolute path to the working directory (auto).
    master_model_path: Path to the master model segmentation file.
    output_path: Where to save the updated master model.
    study_uid: DICOM Study Instance UID to load from PACS.
    pacs_*: PACS connection parameters (auto).
    doctor_segmentation_path: Optional doctor segmentation for reference (auto).
"""

import os

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821
s.load_study_from_pacs(study_uid)  # type: ignore[name-defined]  # noqa: F821

# Load or create master model
if os.path.isfile(master_model_path):  # type: ignore[name-defined]  # noqa: F821
    master_seg = s.load_segmentation(master_model_path, "MasterModel")  # type: ignore[name-defined]  # noqa: F821
else:
    master_seg = s.create_segmentation("MasterModel")

# Optionally load doctor segmentation for reference
try:
    if doctor_segmentation_path and os.path.isfile(doctor_segmentation_path):  # type: ignore[name-defined]
        s.load_segmentation(doctor_segmentation_path, "DoctorRef")  # type: ignore[name-defined]
except NameError:
    pass  # doctor_segmentation_path not provided

# Setup editor on master model
s.setup_editor(master_seg, effect="Paint", brush_size=5.0)
s.set_layout("axial")
s.add_view_shortcuts()

# Add shortcut N to auto-number a new ROI segment
s.add_shortcuts([("n", "s.auto_number_segment(master_seg)")])

s.annotate("Update master model — press N for new ROI")
