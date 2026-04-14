"""Demo: dual-layout master model projection in 3D Slicer.

Mirrors the ``create_projection.py`` script from clarinet_nir_liver:
model CT + master segmentation on the left, target volume + empty
projection on the right. Center-aligned with segment focus observer.

Requires a patient with 2+ studies on Orthanc PACS.

Run: ``make slicer-demo-project``
"""

from pathlib import Path
from typing import Any

import pytest

from clarinet.services.slicer.service import SlicerService

pytestmark = [pytest.mark.slicer, pytest.mark.demo, pytest.mark.asyncio]


async def test_demo_project_model(
    slicer_service: SlicerService,
    slicer_url: str,
    pacs_monkey_patch: str,
    demo_working_dir: Path,
    orthanc_patient_with_two_studies: dict[str, Any],
) -> None:
    """Open Slicer with a dual-layout projection workspace."""
    patient = orthanc_patient_with_two_studies
    study_a = patient["studies"][0]
    study_b = patient["studies"][1]

    model_study_uid = study_a["study_uid"]
    target_study_uid = study_b["study_uid"]

    script = f"""\
{pacs_monkey_patch}

s = SlicerHelper('{demo_working_dir}')

# 1. Load model (reference) volume from PACS
model_loaded = s.load_study_from_pacs('{model_study_uid}', window=(-200, 300))
model_vol = s._image_node
print(f"[Demo] model volume loaded from study {model_study_uid}")

# 2. Create master model segmentation with sample segments
master_seg = (
    s.create_segmentation("MasterModel")
    .add_segment("ROI_1", (1.0, 0.0, 0.0))
    .add_segment("ROI_2", (0.0, 1.0, 0.0))
    .add_segment("ROI_3", (0.0, 0.0, 1.0))
)

# 3. Load target volume from PACS (different study)
target_loaded = s.load_study_from_pacs('{target_study_uid}', window=(-200, 300))
target_vol = s._image_node
print(f"[Demo] target volume loaded from study {target_study_uid}")

# 4. Create empty projection (copy segment metadata from master)
projection = s.create_segmentation("Projection")
s.copy_segments(master_seg, projection, empty=True)

# 5. Dual layout: model + master (left) | target + projection (right)
s.set_dual_layout(model_vol, target_vol, seg_a=master_seg, seg_b=projection, linked=False)

# 6. Center-based alignment
align_tf = s.align_by_center(target_vol, model_vol, moving_segmentation=projection)

# 7. Setup editor on projection with target as source volume
s.setup_editor(projection, effect="Paint", brush_size=5.0, source_volume=target_vol)

# 8. Auto-navigate: Red -> master centroid, Yellow -> projection centroid
def _refine():
    s.refine_alignment_by_centroids(projection, master_seg, align_tf)

s.setup_segment_focus_observer(
    projection,
    master_seg,
    reference_views=["Red"],
    editable_views=["Yellow"],
    only_empty=False,
    on_refine=_refine,
)

s.add_view_shortcuts()
s.annotate("Demo: project master model onto target study")

__execResult = {{
    "status": "ok",
    "model_study": "{model_study_uid}",
    "target_study": "{target_study_uid}",
}}
"""
    result = await slicer_service.execute(slicer_url, script, request_timeout=120.0)
    assert result.get("status") == "ok"
    print(f"[Demo] project demo loaded: model={model_study_uid}, target={target_study_uid}")
