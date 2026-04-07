"""Slicer script — example segmentation task.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    study_uid: DICOM Study UID to load from PACS (auto, STUDY-level).
    output_file: Path to the first OUTPUT file (auto).
    best_series_uid: From hydrator best_series_from_first_check (may be None).
    pacs_*: PACS connection parameters (auto).
"""

import os

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821

# Load only the best series if a hydrator provided it; otherwise the full study.
if best_series_uid is not None:  # type: ignore[name-defined]  # noqa: F821
    s.load_series_from_pacs(study_uid, best_series_uid)  # type: ignore[name-defined]  # noqa: F821
else:
    s.load_study_from_pacs(study_uid)  # type: ignore[name-defined]  # noqa: F821

# Idempotent: load existing segmentation if the file already exists.
if os.path.isfile(output_file):  # type: ignore[name-defined]  # noqa: F821
    seg = s.load_segmentation(output_file, "Segmentation")  # type: ignore[name-defined]  # noqa: F821
else:
    # TODO: replace with your real segment categories and colors.
    seg = (
        s.create_segmentation("Segmentation")
        .add_segment("foreground", (1.0, 0.0, 0.0))
        .add_segment("background", (0.0, 1.0, 0.0))
    )

s.setup_editor(seg, effect="Paint", brush_size=5.0)
s.set_layout("axial")
s.add_view_shortcuts()
s.annotate("TODO: instructions for the operator")
