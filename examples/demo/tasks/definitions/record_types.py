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

segmentation = FileDef(
    pattern="segmentation_{origin_type}_{user_id}.seg.nrrd",
    level="STUDY",
    description="Segmentation mask keyed by producing record type",
)

master_projection = FileDef(
    pattern="master_projection.seg.nrrd",
    level="SERIES",
    description="Projection of master model onto a specific series coordinate space",
)

second_review_output = FileDef(
    pattern="second_review_{user_id}.seg.nrrd",
    level="SERIES",
    description="Second review classification: metastasis/unclear/benign/invisible",
)

resection_model_file = FileDef(
    pattern="resection_model.seg.nrrd",
    level="PATIENT",
    description="3D resection model — liver parenchyma, vessels, corrected lesion boundaries",
)

# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------

first_check = RecordDef(
    name="first-check",
    description="Initial assessment of every study added to the trial",
    label="First check",
    level="STUDY",
    role="doctor",
    min_records=2,
    max_records=2,
    data_schema="schemas/first-check.schema.json",
)

anonymize_study = RecordDef(
    name="anonymize-study",
    description="Automatic study anonymization — fetches from PACS, anonymizes DICOM, distributes",
    label="Anonymize study",
    level="STUDY",
    role="auto",
    min_records=1,
    max_records=1,
)

segment_CT_single = RecordDef(
    name="segment-ct-single",
    description="CT lesion segmentation — only the current study is available for review",
    label="CT segment (single)",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="doctor_CT",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_CT_with_archive = RecordDef(
    name="segment-ct-with-archive",
    description="CT lesion segmentation — current study plus all archive CT studies are available for review",
    label="CT segment (with archive)",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="doctor_CT",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_MRI_single = RecordDef(
    name="segment-mri-single",
    description="MRI lesion segmentation — only the current study is available for review",
    label="MRI segment",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="doctor_MRI",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_MRIAG_single = RecordDef(
    name="segment-mriag-single",
    description="MRI angiography lesion segmentation",
    label="MRI-AG segment",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="doctor_MRI",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_CTAG_single = RecordDef(
    name="segment-ctag-single",
    description="CT angiography lesion segmentation",
    label="CT-AG segment",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="doctor_CT-AG",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_PDCTAG_single = RecordDef(
    name="segment-pdctag-single",
    description="PDCT angiography lesion segmentation",
    label="PDCT-AG segment",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="doctor_PDCT",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

create_master_projection = RecordDef(
    name="create-master-projection",
    description="Manual projection of the master model onto a specific series coordinate space",
    label="Create projection",
    level="SERIES",
    min_records=1,
    max_records=1,
    role="expert",
    slicer_script="scripts/create_projection.py",
    slicer_context_hydrators=["best_series_from_first_check", "model_series_for_projection"],
    slicer_result_validator="validators/projection_validator.py",
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
    name="compare-with-projection",
    description="Automatic comparison of doctor segmentation with master model projection",
    label="Compare with projection",
    level="SERIES",
    min_records=2,
    max_records=4,
    role="auto",
    data_schema="schemas/compare-with-projection.schema.json",
    files=[
        FileRef(master_projection, "input"),
        FileRef(segmentation, "input"),
    ],
)

second_review = RecordDef(
    name="second-review",
    description="Second review — doctor classifies lesions that were missed in the initial segmentation",
    label="Second review",
    level="SERIES",
    min_records=1,
    max_records=1,
    slicer_script="scripts/second_review.py",
    slicer_result_validator="validators/second_review_validator.py",
    files=[
        FileRef(master_projection, "input"),
        FileRef(segmentation, "input"),
        FileRef(second_review_output, "output"),
    ],
    # master_projection, segmentation, second_review_output,
    # output_file, study_uid, working_folder — auto-injected
)

update_master_model = RecordDef(
    name="update-master-model",
    description="Expert manually adds new ROIs to the master model based on comparison results",
    label="Update master model",
    level="PATIENT",
    min_records=1,
    max_records=1,
    role="expert",
    slicer_script="scripts/update_master_model.py",
    slicer_result_validator="validators/master_model_validator.py",
    files=[FileRef(master_model, "output")],
    slicer_context_hydrators=[
        "patient_first_study",  # best_study_uid (fallback for intraop trigger)
        "model_series_for_projection",  # model_study_uid, model_series_uid (CT ref)
        "projection_for_update",  # target_study_uid, target_series_uid, projection_path, doctor_segmentation_path
    ],
    # master_model, output_file, working_folder, best_study_uid — auto-injected
)

# ---------------------------------------------------------------------------
# Stage 8: Retrospective semiotics
# ---------------------------------------------------------------------------

retrospective_semiotics = RecordDef(
    name="retrospective-semiotics",
    description=(
        "Retrospective semiotics assessment — radiological characteristics "
        "of each lesion on a given modality (after 4-7 week washout period)"
    ),
    label="Semiotics",
    level="SERIES",
    role="auto",
    min_records=2,
    max_records=4,
    files=[FileRef(master_projection, "input")],
    # Data: per-lesion semiotics (contrast pattern, signal, morphology, borders, etc.)
    # No Slicer script — OHIF viewer + form-based data entry
)

# ---------------------------------------------------------------------------
# Stage 10: MDK conclusion
# ---------------------------------------------------------------------------

mdk_conclusion = RecordDef(
    name="mdk-conclusion",
    description=(
        "MDK conclusion — multidisciplinary council classifies all lesions "
        "and defines treatment plan"
    ),
    label="MDK",
    level="PATIENT",
    role="mdk",
    min_records=1,
    max_records=1,
    files=[FileRef(master_model, "input")],
    # Data per lesion: classification (metastasis, disappeared_metastasis, unclear,
    #   cyst, hemangioma, benign_unclear) + treatment (cluster_removal, isolated_removal, not_planned)
)

# ---------------------------------------------------------------------------
# Stage 11: Resection planning
# ---------------------------------------------------------------------------

resection_model = RecordDef(
    name="resection-model",
    description=(
        "3D model for resection planning — liver parenchyma, portal/hepatic veins, "
        "corrected lesion ROIs"
    ),
    label="Resection model",
    level="PATIENT",
    role="expert",
    min_records=1,
    max_records=1,
    slicer_script="scripts/resection_model.py",
    slicer_result_validator="validators/resection_model_validator.py",
    slicer_context_hydrators=["patient_first_study"],
    files=[
        FileRef(master_model, "input"),
        FileRef(resection_model_file, "output"),
    ],
)

resection_plan = RecordDef(
    name="resection-plan",
    description=(
        "Resection planning — cluster definition, resection zones, residual parenchyma volume"
    ),
    label="Resection plan",
    level="PATIENT",
    role="expert",
    min_records=1,
    max_records=1,
    slicer_script="scripts/resection_plan.py",
    files=[
        FileRef(resection_model_file, "input"),
        FileRef(master_model, "input"),
    ],
    # Data: per-lesion cluster assignment, resection zones, residual volume
)

resection_report = RecordDef(
    name="resection-report",
    description=("Intraoperative resection report — per-lesion cluster assignment by surgeon"),
    label="Resection report",
    level="PATIENT",
    role="surgeon",
    min_records=1,
    max_records=1,
    data_schema="schemas/resection-report.schema.json",
    files=[FileRef(master_model, "input")],
    # Data: lesions[].lesion_num (readonly, prefilled), lesions[].cluster (editable)
    # additional_lesions[].description, additional_lesions[].cluster
)

# ---------------------------------------------------------------------------
# Stage 12: Intraoperative protocol
# ---------------------------------------------------------------------------

intraop_protocol = RecordDef(
    name="intraop-protocol",
    description=(
        "Intraoperative protocol — US lesion marking, found/not-found/additional "
        "classification, fragment numbering"
    ),
    label="Surgery protocol",
    level="PATIENT",
    role="surgeon",
    min_records=1,
    max_records=1,
    files=[FileRef(master_model, "input")],
    # Data per lesion: us_found (bool), removed (bool), fragment_number (int)
    # Data additional: additionally_found_lesions list
)

# ---------------------------------------------------------------------------
# Stage 13: Post-operative CT review
# ---------------------------------------------------------------------------

postop_ct_review = RecordDef(
    name="postop-ct-review",
    description=(
        "Post-operative CT review — complication screening, "
        "master model update for intraop findings"
    ),
    label="Post-op CT",
    level="STUDY",
    role="doctor_CT",
    min_records=1,
    max_records=2,
)

# ---------------------------------------------------------------------------
# Stage 14: Histology
# ---------------------------------------------------------------------------

histology = RecordDef(
    name="histology",
    description=(
        "Histological examination — macroscopic and microscopic analysis "
        "per fragment and per lesion"
    ),
    label="Histology",
    level="PATIENT",
    role="pathologist",
    min_records=1,
    max_records=1,
    files=[FileRef(master_model, "input")],
    # Data per lesion: macro_visible (bool), micro_visible (bool),
    #   tumor_cells (yes/no/no_data), tumor_fibrotic_ratio (float, nullable)
)
