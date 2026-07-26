"""NDT comparative study — RecordType definitions (Python config mode).

All file definitions and record types in a single self-contained file.
"""

from clarinet.flow import FileDef, FileRef, RecordDef

# ---------------------------------------------------------------------------
# File definitions
# ---------------------------------------------------------------------------

master_model = FileDef(
    pattern="master_model.seg.nrrd",
    level="PATIENT",
    description="Master model segmentation — one ROI per defect with unique number",
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
    description="Second review classification: defect/indeterminate/cosmetic/invisible",
)

volume_nifti = FileDef(
    pattern="volume.nii.gz",
    level="SERIES",
    description="NIfTI volume converted from DICOM series",
)

repair_model_file = FileDef(
    pattern="repair_model.seg.nrrd",
    level="PATIENT",
    description="3D repair model — part body, internal channels, corrected defect boundaries",
)

adjudication_note = FileDef(
    pattern="adjudication_note_{parent_id}.md",
    level="SERIES",
    description="Adjudication note for one comparison result, keyed by its parent record",
)

# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------

first_check = RecordDef(
    name="first-check",
    description="Initial assessment of every study added to the demo",
    label="First check",
    level="STUDY",
    role="inspector",
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
    description="CT defect segmentation — only the current study is available for review",
    label="CT segment (single)",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="inspector_CT",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_CT_with_archive = RecordDef(
    name="segment-ct-with-archive",
    description="CT defect segmentation — current study plus all archive CT studies are available for review",
    label="CT segment (with archive)",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="inspector_CT",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_UT_single = RecordDef(
    name="segment-ut-single",
    description="UT (ultrasonic) defect segmentation — only the current study is available for review",
    label="UT segment",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="inspector_UT",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_UTHD_single = RecordDef(
    name="segment-ut-hd-single",
    description="High-resolution UT defect segmentation",
    label="UT-HD segment",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="inspector_UT",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_CTHD_single = RecordDef(
    name="segment-ct-hd-single",
    description="High-resolution/contrast-enhanced CT defect segmentation",
    label="CT-HD segment",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="inspector_CT-HD",
    slicer_script="scripts/segment.py",
    slicer_result_validator="validators/segment_validator.py",
    slicer_context_hydrators=["best_series_from_first_check"],
    files=[FileRef(segmentation, "output")],
    # study_uid, segmentation, output_file, working_folder, best_series_uid — auto-injected
)

segment_MCT_single = RecordDef(
    name="segment-mct-single",
    description="Micro-CT defect segmentation",
    label="micro-CT segment",
    level="STUDY",
    min_records=2,
    max_records=4,
    role="inspector_MCT",
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
    # One canonical projection per series regardless of which expert (re)creates
    # it — not one copy per user. unique_by=None + max_records=1 is the
    # canonical spelling for a one-per-level singleton (the default
    # {"user", "parent"} would demand {user_id} in master_projection's pattern).
    unique_by=None,
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
    description="Automatic comparison of inspector segmentation with master model projection",
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

comparison_adjudication = RecordDef(
    name="comparison-adjudication",
    description=(
        "Inspector adjudication of one automatic comparison result — confirms or "
        "overturns the automatic match/mismatch verdict. Demonstrates a "
        "parent-scoped uniqueness partition: up to 4 compare-with-projection "
        "records can coexist per series (one per segmentation source), and each "
        "gets its own adjudication record keyed by {parent_id}, regardless of "
        "which inspector claims it."
    ),
    label="Comparison adjudication",
    level="SERIES",
    role="inspector",
    min_records=0,
    max_records=4,  # mirrors compare-with-projection's own cap: one adjudication per parent
    parent_required=True,
    unique_by={"parent"},
    files=[FileRef(adjudication_note, "output")],
)

second_review = RecordDef(
    name="second-review",
    description="Second review — inspector classifies defects that were missed in the initial segmentation",
    label="Second review",
    level="SERIES",
    role="inspector",
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
    # One canonical master model per patient, edited in place by whichever
    # expert updates it — see create-master-projection above for the rationale.
    unique_by=None,
    role="expert",
    slicer_script="scripts/update_master_model.py",
    slicer_result_validator="validators/master_model_validator.py",
    files=[FileRef(master_model, "output")],
    slicer_context_hydrators=[
        "patient_first_study",  # best_study_uid (fallback for in-process trigger)
        "model_series_for_projection",  # model_study_uid, model_series_uid (CT ref)
        "projection_for_update",  # target_study_uid, target_series_uid, projection_path, inspector_segmentation_path
    ],
    # master_model, output_file, working_folder, best_study_uid — auto-injected
)

# ---------------------------------------------------------------------------
# Stage 8: Retrospective characterization
# ---------------------------------------------------------------------------

retrospective_characterization = RecordDef(
    name="retrospective-characterization",
    description=(
        "Retrospective characterization assessment — signal characteristics "
        "of each defect on a given modality (after a blind-reassessment interval)"
    ),
    label="Characterization",
    level="SERIES",
    role="auto",
    min_records=2,
    max_records=4,
    files=[FileRef(master_projection, "input")],
    # Data: per-defect characterization (signal pattern, texture, morphology, edge definition, etc.)
    # No Slicer script — OHIF viewer + form-based data entry
)

# ---------------------------------------------------------------------------
# Stage 10: MRB conclusion
# ---------------------------------------------------------------------------

mrb_conclusion = RecordDef(
    name="mrb-conclusion",
    description=(
        "MRB conclusion — Material Review Board classifies all defects and defines the repair plan"
    ),
    label="MRB",
    level="PATIENT",
    role="mrb",
    min_records=1,
    max_records=1,
    mask_patient_data=False,
    files=[FileRef(master_model, "input")],
    # Data per defect: classification (defect, resolved_defect, indeterminate,
    #   cavity, inclusion, cosmetic_indeterminate) + treatment (cluster_repair, isolated_repair, not_planned)
)

# ---------------------------------------------------------------------------
# Stage 11: Repair planning
# ---------------------------------------------------------------------------

repair_model = RecordDef(
    name="repair-model",
    description=(
        "3D model for repair planning — part body, primary/secondary internal channels, "
        "corrected defect ROIs"
    ),
    label="Repair model",
    level="PATIENT",
    role="expert",
    min_records=1,
    max_records=1,
    # One canonical repair model per patient — see create-master-projection
    # above for the rationale.
    unique_by=None,
    mask_patient_data=False,
    slicer_script="scripts/repair_model.py",
    slicer_result_validator="validators/repair_model_validator.py",
    slicer_context_hydrators=["patient_first_study"],
    files=[
        FileRef(master_model, "input"),
        FileRef(repair_model_file, "output"),
    ],
)

repair_plan = RecordDef(
    name="repair-plan",
    description=("Repair planning — cluster definition, repair zones, residual material volume"),
    label="Repair plan",
    level="PATIENT",
    role="expert",
    min_records=1,
    max_records=1,
    mask_patient_data=False,
    slicer_script="scripts/repair_plan.py",
    files=[
        FileRef(repair_model_file, "input"),
        FileRef(master_model, "input"),
    ],
    # Data: per-defect cluster assignment, repair zones, residual volume
)

repair_report = RecordDef(
    name="repair-report",
    description=("Post-repair report — per-defect cluster assignment by technician"),
    label="Repair report",
    level="PATIENT",
    role="technician",
    min_records=1,
    max_records=1,
    mask_patient_data=False,
    data_schema="schemas/repair-report.schema.json",
    files=[FileRef(master_model, "input")],
    # Data: defects[].defect_num (readonly, prefilled), defects[].cluster (editable)
    # additional_defects[].description, additional_defects[].cluster
)

# ---------------------------------------------------------------------------
# Stage 12: Repair protocol
# ---------------------------------------------------------------------------

repair_protocol = RecordDef(
    name="repair-protocol",
    description=(
        "In-process repair protocol — UT defect marking, found/not-found/additional "
        "classification, fragment numbering"
    ),
    label="Repair protocol",
    level="PATIENT",
    role="technician",
    min_records=1,
    max_records=1,
    mask_patient_data=False,
    files=[FileRef(master_model, "input")],
    # Data per defect: ut_found (bool), removed (bool), fragment_number (int)
    # Data additional: additionally_found_defects list
)

# ---------------------------------------------------------------------------
# Stage 13: Post-repair CT review
# ---------------------------------------------------------------------------

post_repair_ct_review = RecordDef(
    name="post-repair-ct-review",
    description=(
        "Post-repair CT review — anomaly screening, master model update for in-process findings"
    ),
    label="Post-repair CT",
    level="STUDY",
    role="inspector_CT",
    min_records=1,
    max_records=2,
)

# ---------------------------------------------------------------------------
# Stage 14: Metallography
# ---------------------------------------------------------------------------

metallography = RecordDef(
    name="metallography",
    description=(
        "Metallographic examination — macroscopic and microscopic sectioning analysis "
        "per fragment and per defect"
    ),
    label="Metallography",
    level="PATIENT",
    role="analyst",
    min_records=1,
    max_records=1,
    mask_patient_data=False,
    files=[FileRef(master_model, "input")],
    # Data per defect: macro_visible (bool), micro_visible (bool),
    #   defect_confirmed (yes/no/no_data), defect_area_fraction (float, nullable)
)

# ---------------------------------------------------------------------------
# NIfTI volume viewing
# ---------------------------------------------------------------------------

view_nifti = RecordDef(
    name="view-nifti",
    description="View pre-converted NIfTI volume in 3D Slicer",
    label="View NIfTI",
    level="SERIES",
    role="inspector",
    min_records=1,
    max_records=1,
    slicer_script="scripts/view_nifti.py",
    files=[FileRef(volume_nifti, "input")],
)
