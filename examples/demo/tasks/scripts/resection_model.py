"""Slicer script — create 3D resection model.

Loads the master model and allows the expert to segment liver parenchyma,
portal/hepatic veins, and correct lesion ROI boundaries for surgical planning.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    master_model: Path to the master model segmentation file (auto, from file_registry).
    output_file: Path to the resection model output file (auto, first OUTPUT file).
    best_study_uid: Anon UID of the patient's first study (hydrated, for PACS load).
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

# Load or create resection model
if os.path.isfile(output_file):  # type: ignore[name-defined]  # noqa: F821
    resection_seg = s.load_segmentation(output_file, "ResectionModel")  # type: ignore[name-defined]  # noqa: F821
else:
    resection_seg = (
        s.create_segmentation("ResectionModel")
        .add_segment("liver", (0.8, 0.6, 0.4))
        .add_segment("portal_vein", (0.0, 0.0, 1.0))
        .add_segment("hepatic_vein", (0.0, 0.5, 1.0))
    )

# Setup editor
s.setup_editor(resection_seg, effect="Paint", brush_size=3.0)
s.set_layout("axial")
s.add_view_shortcuts()

# Add shortcut N to auto-number a new lesion ROI segment
s.add_shortcuts([("n", 's.auto_number_segment(resection_seg, prefix="lesion_")')])

s.annotate("Resection model — segment liver, vessels; press N for new lesion ROI")
