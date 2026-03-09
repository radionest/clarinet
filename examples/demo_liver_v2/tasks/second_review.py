"""Slicer script — second review: classify missed lesions.

Subtracts the doctor's segmentation from the master projection to find missed
ROIs, then presents a classification segmentation for the reviewer.

Context variables (injected by SlicerService):
    working_folder: Absolute path to the working directory (auto).
    master_projection_path: Path to the master projection segmentation.
    doctor_segmentation_path: Path to the doctor's initial segmentation.
    output_path: Where to save the classification result.
    study_uid: DICOM Study Instance UID to load from PACS.
    pacs_*: PACS connection parameters (auto).
    previous_review_path: Optional path to a previous review to exclude (auto).
"""

import os

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821
s.load_study_from_pacs(study_uid)  # type: ignore[name-defined]  # noqa: F821

# Load projection and doctor segmentation
projection = s.load_segmentation(master_projection_path, "Projection")  # type: ignore[name-defined]  # noqa: F821
doctor_seg = s.load_segmentation(doctor_segmentation_path, "DoctorSeg")  # type: ignore[name-defined]  # noqa: F821

# Subtract doctor's segmentation from projection to find missed lesions
missed = s.subtract_segmentations(projection, doctor_seg, "MissedLesions")

# If a previous review exists, subtract it as well
try:
    if previous_review_path and os.path.isfile(previous_review_path):  # type: ignore[name-defined]
        prev_review = s.load_segmentation(previous_review_path, "PreviousReview")  # type: ignore[name-defined]
        s.subtract_segmentations(missed, prev_review)
except NameError:
    pass  # previous_review_path not provided

# Create classification segmentation
if os.path.isfile(output_path):  # type: ignore[name-defined]  # noqa: F821
    classification = s.load_segmentation(output_path, "Classification")  # type: ignore[name-defined]  # noqa: F821
else:
    classification = (
        s.create_segmentation("Classification")
        .add_segment("Metastasis", (1.0, 0.0, 0.0))
        .add_segment("Unclear", (1.0, 1.0, 0.0))
        .add_segment("Benign", (0.0, 1.0, 0.0))
        .add_segment("Invisible", (0.5, 0.5, 0.5))
    )

s.setup_editor(classification, effect="Islands", brush_size=5.0)
s.set_layout("axial")
s.add_view_shortcuts()
s.annotate("Classify missed lesions")
