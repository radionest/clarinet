"""Slicer script — repair planning.

Loads the repair model and master model, allows the expert to define
cluster assignments, repair zones, and calculate residual material volume.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    repair_model_file: Path to the repair model segmentation (auto, from file_registry).
    master_model: Path to the master model segmentation file (auto, from file_registry).
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

# Load repair model
if os.path.isfile(repair_model_file):  # type: ignore[name-defined]  # noqa: F821
    repair_seg = s.load_segmentation(repair_model_file, "RepairModel")  # type: ignore[name-defined]  # noqa: F821

# Load master model as reference
if os.path.isfile(master_model):  # type: ignore[name-defined]  # noqa: F821
    s.load_segmentation(master_model, "MasterModel")  # type: ignore[name-defined]  # noqa: F821

# Setup editor on repair model
s.setup_editor(repair_seg, effect="Paint", brush_size=3.0)
s.set_layout("axial")
s.add_view_shortcuts()

s.annotate("Repair planning — define clusters and repair zones")
