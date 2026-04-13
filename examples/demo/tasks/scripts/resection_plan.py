"""Slicer script — resection planning.

Loads the resection model and master model, allows the expert to define
cluster assignments, resection zones, and calculate residual parenchyma volume.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    resection_model_file: Path to the resection model segmentation (auto, from file_registry).
    master_model: Path to the master model segmentation file (auto, from file_registry).
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

# Load resection model
if os.path.isfile(resection_model_file):  # type: ignore[name-defined]  # noqa: F821
    resection_seg = s.load_segmentation(resection_model_file, "ResectionModel")  # type: ignore[name-defined]  # noqa: F821

# Load master model as reference
if os.path.isfile(master_model):  # type: ignore[name-defined]  # noqa: F821
    s.load_segmentation(master_model, "MasterModel")  # type: ignore[name-defined]  # noqa: F821

# Setup editor on resection model
s.setup_editor(resection_seg, effect="Paint", brush_size=3.0)
s.set_layout("axial")
s.add_view_shortcuts()

s.annotate("Resection planning — define clusters and resection zones")
