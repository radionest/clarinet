"""Slicer script — update the master model with new ROIs.

Dual-layout mode (when projection data available):
    Left: CT reference volume + master model segmentation.
    Right: Target modality volume + projection with computed NEW_* false-positive ROIs.
    Expert marks NEW_* lesions from the right panel onto the master model on the left.

Single-layout fallback (intraop trigger, no projection):
    CT volume + master model segmentation in axial view.

Auto-numbering happens at save/validation (master_model_validator.py),
not via manual shortcut.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    master_model: Path to the master model segmentation file (auto, from file_registry).
    output_file: Same as master_model (auto, first OUTPUT file).
    best_study_uid: Anon UID of the patient's first study (from patient_first_study).
    model_study_uid: CT reference study anon UID (from model_series_for_projection).
    model_series_uid: CT reference series anon UID (from model_series_for_projection).
    target_study_uid: Target study anon UID (from projection_for_update).
    target_series_uid: Target series anon UID (from projection_for_update).
    projection_path: Path to master projection segmentation (from projection_for_update).
    doctor_segmentation_path: Path to doctor's segmentation (from projection_for_update).
    pacs_*: PACS connection parameters (auto).
"""

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    slicer = Any
    from clarinet.services.slicer.helper import SlicerHelper  # type: ignore[import]

s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821


# ---------------------------------------------------------------------------
# Detect layout mode: dual (projection available) vs single (fallback)
# ---------------------------------------------------------------------------
has_projection = False
try:
    _proj_path = projection_path  # type: ignore[name-defined]
    _doc_path = doctor_segmentation_path  # type: ignore[name-defined]
    if os.path.isfile(_proj_path) and os.path.isfile(_doc_path):
        has_projection = True
except NameError:
    pass

if has_projection:
    # === DUAL LAYOUT MODE ===

    # 1. Load CT reference (model) volume
    try:
        model_node_ids = s.load_series_from_pacs(model_study_uid, model_series_uid)  # type: ignore[name-defined]
        model_vol = slicer.mrmlScene.GetNodeByID(model_node_ids[0])  # type: ignore[name-defined]
    except NameError:
        # Fallback to best_study_uid if model series not available
        s.load_study_from_pacs(best_study_uid, raise_on_empty=False)  # type: ignore[name-defined]  # noqa: F821
        model_vol = s._image_node

    # 2. Load or create master model segmentation (ref geometry from model_vol)
    if os.path.isfile(master_model):  # type: ignore[name-defined]  # noqa: F821
        master_seg = s.load_segmentation(master_model, "MasterModel")  # type: ignore[name-defined]  # noqa: F821
    else:
        master_seg = s.create_segmentation("MasterModel")

    # 3. Load target modality series
    target_node_ids = s.load_series_from_pacs(target_study_uid, target_series_uid)  # type: ignore[name-defined]  # noqa: F821
    target_vol = slicer.mrmlScene.GetNodeByID(target_node_ids[0])  # type: ignore[name-defined]

    # 4. Load projection segmentation (ref geometry from target_vol)
    projection = s.load_segmentation(projection_path, "Projection")  # type: ignore[name-defined]  # noqa: F821

    # 5. Load doctor's segmentation (ref geometry from target_vol — same coord space)
    doctor_seg = s.load_segmentation(doctor_segmentation_path, "DoctorSeg")  # type: ignore[name-defined]  # noqa: F821

    # 6. Binarize + split doctor's segmentation into connected components
    doctor_islands = s.binarize_and_split_islands(doctor_seg, output_name="_DocIslands")
    slicer.mrmlScene.RemoveNode(doctor_seg)  # type: ignore[name-defined]

    # 7. Compute NEW_* false positives — ROI-level difference
    fp_node = s.subtract_segmentations(doctor_islands, projection, output_name="_FP_tmp")
    if fp_node.GetSegmentation().GetNumberOfSegments() > 0:
        s.rename_segments(fp_node, prefix="NEW", color=(1.0, 1.0, 0.0))
        s.copy_segments(fp_node, projection)  # filled → right panel
        s.copy_segments(fp_node, master_seg, empty=True)  # empty → left panel
    slicer.mrmlScene.RemoveNode(fp_node)  # type: ignore[name-defined]
    slicer.mrmlScene.RemoveNode(doctor_islands)  # type: ignore[name-defined]

    # 8. Side-by-side: model CT + master (left) | target + projection with NEW_* (right)
    s.set_dual_layout(model_vol, target_vol, seg_a=master_seg, seg_b=projection, linked=False)

    # 9. Rigid alignment — projection already has labels, refine immediately
    align_tf = s.align_by_center(target_vol, model_vol, moving_segmentation=projection)
    n = s.refine_alignment_by_centroids(projection, master_seg, align_tf)
    print(f"[Alignment] refined with {n} landmark pairs")

    # 10. Setup editor on master model with model volume as source
    s.setup_editor(master_seg, effect="Paint", brush_size=5.0, source_volume=model_vol)

    # 11. Auto-navigate: Yellow → projection centroid, Red → master centroid
    s.setup_segment_focus_observer(
        master_seg,
        projection,
        reference_views=["Yellow"],
        editable_views=["Red"],
    )

    # 12. View shortcuts
    s.add_view_shortcuts()

    # 13. Annotation
    s.annotate("Update master model \u2014 mark NEW lesions from right panel")

else:
    # === SINGLE LAYOUT FALLBACK (intraop trigger) ===

    # Load CT volume
    try:
        model_node_ids = s.load_series_from_pacs(
            model_study_uid, model_series_uid, raise_on_empty=False
        )  # type: ignore[name-defined]
    except NameError:
        try:
            s.load_study_from_pacs(best_study_uid, raise_on_empty=False)  # type: ignore[name-defined]
        except NameError:
            pass

    # Load or create master model
    if os.path.isfile(master_model):  # type: ignore[name-defined]  # noqa: F821
        master_seg = s.load_segmentation(master_model, "MasterModel")  # type: ignore[name-defined]  # noqa: F821
    else:
        master_seg = s.create_segmentation("MasterModel")

    # Setup editor
    s.setup_editor(master_seg, effect="Paint", brush_size=5.0)
    s.set_layout("axial")
    s.add_view_shortcuts()

    s.annotate("Update master model \u2014 add new ROIs")
