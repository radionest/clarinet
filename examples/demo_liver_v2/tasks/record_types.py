"""Liver study v2 — RecordType definitions (Python config mode).

All file definitions and record types in a single self-contained file.
"""

from clarinet.flow import FileDef, FileRef, RecordDef

# ---------------------------------------------------------------------------
# File definitions
# ---------------------------------------------------------------------------

master_model = FileDef(
    pattern="master_model.seg.nrrd",
    level="PATIENT",
    description="Master model segmentation — one ROI per lesion with unique number",
)

segmentation_single = FileDef(
    pattern="segmentation_single_{user_id}.seg.nrrd",
    description="Segmentation from single-study review",
)

segmentation_with_archive = FileDef(
    pattern="segmentation_with_archive_{user_id}.seg.nrrd",
    description="Segmentation from review with archive CT studies",
)

master_projection = FileDef(
    pattern="master_projection.seg.nrrd",
    description="Projection of master model onto a specific series coordinate space",
)

second_review_output = FileDef(
    pattern="second_review_{user_id}.seg.nrrd",
    description="Second review classification: metastasis/unclear/benign/invisible",
)

# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------

first_check = RecordDef(
    name="first_check",
    description="Initial assessment of every study added to the trial",
    label="First check",
    level="STUDY",
    role="doctor",
    min_records=1,
    max_records=1,
)

anonymize_study = RecordDef(
    name="anonymize_study",
    description="Automatic study anonymization — fetches from PACS, anonymizes DICOM, distributes",
    label="Anonymize study",
    level="STUDY",
    role="auto",
    min_records=1,
    max_records=1,
)

segment_CT_single = RecordDef(
    name="segment_CT_single",
    description="CT lesion segmentation — only the current study is available for review",
    label="CT segment (single)",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_CT",
    slicer_script="segment.py",
    slicer_result_validator="segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation_single, "output")],
    # study_uid, segmentation_single, output_file, working_folder, best_series_uid — auto-injected
)

segment_CT_with_archive = RecordDef(
    name="segment_CT_with_archive",
    description="CT lesion segmentation — current study plus all archive CT studies are available for review",
    label="CT segment (with archive)",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_CT",
    slicer_script="segment.py",
    slicer_result_validator="segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation_with_archive, "output")],
    # study_uid, segmentation_with_archive, output_file, working_folder, best_series_uid — auto-injected
)

segment_MRI_single = RecordDef(
    name="segment_MRI_single",
    description="MRI lesion segmentation — only the current study is available for review",
    label="MRI segment",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_MRI",
    slicer_script="segment.py",
    slicer_result_validator="segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation_single, "output")],
    # study_uid, segmentation_single, output_file, working_folder, best_series_uid — auto-injected
)

segment_MRIAG_single = RecordDef(
    name="segment_MRIAG_single",
    description="MRI angiography lesion segmentation",
    label="MRI-AG segment",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_MRI",
    slicer_script="segment.py",
    slicer_result_validator="segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation_single, "output")],
    # study_uid, segmentation_single, output_file, working_folder, best_series_uid — auto-injected
)

segment_CTAG_single = RecordDef(
    name="segment_CTAG_single",
    description="CT angiography lesion segmentation",
    label="CT-AG segment",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_CT-AG",
    slicer_script="segment.py",
    slicer_result_validator="segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation_single, "output")],
    # study_uid, segmentation_single, output_file, working_folder, best_series_uid — auto-injected
)

segment_PDCTAG_single = RecordDef(
    name="segment_PDCTAG_single",
    description="PDCT angiography lesion segmentation",
    label="PDCT-AG segment",
    level="STUDY",
    min_records=1,
    max_records=4,
    role="doctor_PDCT",
    slicer_script="segment.py",
    slicer_result_validator="segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation_single, "output")],
    # study_uid, segmentation_single, output_file, working_folder, best_series_uid — auto-injected
)

create_master_projection = RecordDef(
    name="create_master_projection",
    description="Manual projection of the master model onto a specific series coordinate space",
    label="Create projection",
    level="SERIES",
    min_records=1,
    max_records=1,
    role="expert",
    slicer_script="create_projection.py",
    slicer_context_hydrators=["best_series_from_first_check", "model_series_for_projection"],
    slicer_result_validator="projection_validator.py",
    files=[
        FileRef(master_model, "input"),
        FileRef(master_projection, "output"),
    ],
    # study_uid, series_uid — auto (SERIES-level)
    # best_series_uid — from best_series_from_first_check hydrator
    # model_study_uid, model_series_uid — from model_series_for_projection hydrator
    # master_model, master_projection, output_file, working_folder, pacs_* — auto
)

compare_with_projection = RecordDef(
    name="compare_with_projection",
    description="Automatic comparison of doctor segmentation with master model projection",
    label="Compare with projection",
    level="SERIES",
    min_records=1,
    max_records=1,
    role="auto",
    files=[
        FileRef(master_projection, "input"),
        FileRef(segmentation_single, "input"),
    ],
)

second_review = RecordDef(
    name="second_review",
    description="Second review — doctor classifies lesions that were missed in the initial segmentation",
    label="Second review",
    level="SERIES",
    min_records=1,
    max_records=1,
    slicer_script="second_review.py",
    slicer_result_validator="second_review_validator.py",
    files=[
        FileRef(master_projection, "input"),
        FileRef(segmentation_single, "input"),
        FileRef(second_review_output, "output"),
    ],
    # master_projection, segmentation_single, second_review_output,
    # output_file, study_uid, working_folder — auto-injected
)

update_master_model = RecordDef(
    name="update_master_model",
    description="Expert manually adds new ROIs to the master model based on comparison results",
    label="Update master model",
    level="PATIENT",
    min_records=1,
    max_records=1,
    role="expert",
    slicer_script="update_master_model.py",
    slicer_result_validator="master_model_validator.py",
    files=[FileRef(master_model, "output")],
    slicer_context_hydrators=["patient_first_study"],
    # master_model, output_file, working_folder, best_study_uid — auto-injected
)
