"""Slicer script — second review: classify missed lesions.

Subtracts the doctor's segmentation from the master projection to find missed
ROIs, then presents a classification segmentation for the reviewer.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    master_projection: Path to the master projection segmentation (auto, from file_registry).
    segmentation: Path to the doctor's initial segmentation (auto, from file_registry).
    second_review_output: Path to the classification result (auto, from file_registry).
    output_file: Same as second_review_output (auto, first OUTPUT file).
    study_uid: DICOM Study Instance UID to load from PACS (auto).
    pacs_*: PACS connection parameters (auto).
"""

import os

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821
s.load_study_from_pacs(study_uid)  # type: ignore[name-defined]  # noqa: F821

# Load projection and doctor segmentation
projection = s.load_segmentation(master_projection, "Projection")  # type: ignore[name-defined]  # noqa: F821
doctor_seg = s.load_segmentation(segmentation, "DoctorSeg")  # type: ignore[name-defined]  # noqa: F821

# Subtract doctor's segmentation from projection to find missed lesions
missed = s.subtract_segmentations(projection, doctor_seg, "MissedLesions")

# Create classification segmentation
if os.path.isfile(output_file):  # type: ignore[name-defined]  # noqa: F821
    classification = s.load_segmentation(output_file, "Classification")  # type: ignore[name-defined]  # noqa: F821
else:
    classification = (
        s.create_segmentation("Classification")
        .add_segment("mts", (1.0, 0.0, 0.0))
        .add_segment("unclear", (1.0, 1.0, 0.0))
        .add_segment("benign", (0.0, 1.0, 0.0))
        .add_segment("invisible", (0.5, 0.5, 0.5))
    )

# Merge missed lesions into Classification as _pool for cross-segmentation Islands
s.merge_as_pool(missed, classification)

# Hide auxiliary segmentations, keep Classification and DoctorSeg visible
s.set_segmentation_visibility(projection, False)
s.set_segmentation_visibility(missed, False)

# _pool: yellow outline only, no fill, 2px line
s.configure_segment_display(
    classification,
    "_pool",
    color=(1.0, 1.0, 0.0),
    fill_opacity=0.0,
    outline_opacity=1.0,
    outline_thickness=2,
)

s.setup_editor(classification, effect="Islands")
s.set_layout("axial")
s.add_view_shortcuts()
s.annotate("Classify missed lesions")
