"""Slicer script — lesion segmentation on a single study.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    study_uid: DICOM Study Instance UID to load from PACS (auto).
    best_series_uid: DICOM Series Instance UID of the best series (from first_check hydrator).
    output_file: Path to the first OUTPUT file definition (auto).
    pacs_*: PACS connection parameters (auto).
"""

import os

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821

# Load only the best series if available, otherwise load the full study
if best_series_uid is not None:  # type: ignore[name-defined]  # noqa: F821
    s.load_series_from_pacs(study_uid, best_series_uid, window=(-200, 300))  # type: ignore[name-defined]  # noqa: F821
else:
    s.load_study_from_pacs(study_uid, window=(-200, 300))  # type: ignore[name-defined]  # noqa: F821

if os.path.isfile(output_file):  # type: ignore[name-defined]  # noqa: F821
    seg = s.load_segmentation(output_file, "Segmentation")  # type: ignore[name-defined]  # noqa: F821
else:
    seg = (
        s.create_segmentation("Segmentation")
        .add_segment("mts", (1.0, 0.0, 0.0))
        .add_segment("unclear", (1.0, 1.0, 0.0))
        .add_segment("benign", (0.0, 1.0, 0.0))
    )

s.setup_editor(seg, effect="Paint", brush_size=5.0)
s.set_layout("axial")
s.add_view_shortcuts()
s.annotate("Segment all lesions")
