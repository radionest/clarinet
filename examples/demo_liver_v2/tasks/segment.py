"""Slicer script — lesion segmentation on a single study.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    study_uid: DICOM Study Instance UID to load from PACS (auto).
    output_file: Path to the first OUTPUT file definition (auto).
    pacs_*: PACS connection parameters (auto).
"""

import os

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821
s.load_study_from_pacs(study_uid)  # type: ignore[name-defined]  # noqa: F821

if os.path.isfile(output_file):  # type: ignore[name-defined]  # noqa: F821
    seg = s.load_segmentation(output_file, "Segmentation")  # type: ignore[name-defined]  # noqa: F821
else:
    seg = s.create_segmentation("Segmentation").add_segment("Lesions", (1.0, 0.0, 0.0))

s.setup_editor(seg, effect="Paint", brush_size=5.0)
s.set_layout("axial")
s.add_view_shortcuts()
s.annotate("Segment all lesions")
