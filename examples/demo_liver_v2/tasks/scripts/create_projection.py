"""Slicer script — project master model onto a target series.

Dual-viewport workflow: CT reference (model) volume + master segmentation on
the left, target series volume + empty projection on the right.  Auto-navigates
Red to master ROI centroid and Yellow to projection centroid on segment select.

Loading order matters — ``load_series_from_pacs`` auto-sets ``_image_node`` to
the last loaded volume, which ``load_segmentation`` uses as reference geometry.

Context variables (injected by build_slicer_context):
    working_folder: Absolute path to the working directory (auto).
    study_uid: Target study anonymized UID (auto, SERIES-level).
    series_uid: Target series anonymized UID (auto, SERIES-level).
    best_series_uid: Target study's best series (from best_series_from_first_check).
    model_study_uid: CT reference study anonymized UID (from model_series_for_projection).
    model_series_uid: CT reference series anonymized UID (from model_series_for_projection).
    master_model: Path to the master model segmentation file (auto, from file_registry).
    master_projection: Path to the projection output (auto, from file_registry).
    output_file: Same as master_projection (auto, first OUTPUT file).
    pacs_*: PACS connection parameters (auto).
"""

import os
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    slicer = Any
    from clarinet.services.slicer.helper import SlicerHelper  # type: ignore[import]
s = SlicerHelper(working_folder)  # type: ignore[name-defined]  # noqa: F821

# 1. Load CT reference (model) volume — _image_node = model_vol
model_node_ids = s.load_series_from_pacs(model_study_uid, model_series_uid)  # type: ignore[name-defined]  # noqa: F821
model_vol = slicer.mrmlScene.GetNodeByID(model_node_ids[0])  # type: ignore[name-defined]  # noqa: F821

# 2. Load master model segmentation (reference geometry from model_vol)
master_seg = s.load_segmentation(master_model, "MasterModel")  # type: ignore[name-defined]  # noqa: F821

# 3. Load target series — _image_node = target_vol
target_node_ids = s.load_series_from_pacs(study_uid, best_series_uid)  # type: ignore[name-defined]  # noqa: F821
target_vol = slicer.mrmlScene.GetNodeByID(target_node_ids[0])  # type: ignore[name-defined]  # noqa: F821

# 4. Create or load projection (reference geometry from target_vol)
if os.path.isfile(master_projection):  # type: ignore[name-defined]  # noqa: F821
    projection = s.load_segmentation(master_projection, "Projection")  # type: ignore[name-defined]  # noqa: F821
else:
    projection = s.create_segmentation("Projection")
    s.copy_segments(master_seg, projection, empty=True)

# 5. Side-by-side: model CT on left, target + projection on right (unlinked — different coord spaces)
s.set_dual_layout(model_vol, target_vol, seg_a=master_seg, seg_b=projection, linked=False)

# 6. Rigid alignment: center-based initially, refined by segment centroids on each switch
align_tf = s.align_by_center(target_vol, model_vol, moving_segmentation=projection)


def _refine() -> None:
    s.refine_alignment_by_centroids(projection, master_seg, align_tf)


# 7. Setup editor on projection with target volume as source
s.setup_editor(projection, effect="Paint", brush_size=5.0, source_volume=target_vol)

# 8. Auto-navigate: Red → MasterModel centroid, Yellow → Projection centroid
s.setup_segment_focus_observer(
    projection,
    master_seg,
    reference_views=["Red"],
    editable_views=["Yellow"],
    only_empty=False,
    on_refine=_refine,
)

s.annotate("Project master model ROIs onto target study")
