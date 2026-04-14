"""Demo: dual-layout alignment test with random ROIs.

Loads two studies from the same patient. On the first (reference) volume,
10 ROIs are painted at random positions inside the volume. The second
(moving) volume gets an empty copy. Dual layout + center alignment +
segment focus observer let you verify centroid-based refinement visually.

Exercises helper methods: load_study_from_pacs, create_segmentation,
get_segment_names, copy_segments, set_dual_layout, align_by_center,
refine_alignment_by_centroids, setup_editor, setup_segment_focus_observer,
get_segment_centroid.

Run: ``make slicer-demo-alignment``
"""

from pathlib import Path
from typing import Any

import pytest

from clarinet.services.slicer.service import SlicerService

pytestmark = [pytest.mark.slicer, pytest.mark.demo, pytest.mark.asyncio]


def _paint_spheres_script() -> str:
    """Inline helper: paint a sphere into a segment at a given IJK center.

    This is the only part that uses raw slicer.util API — SlicerHelper
    doesn't have a "paint at position" method.
    """
    return """
def _paint_sphere(seg_node, seg_id, ref_volume, ijk_center, radius=8):
    '''Paint a sphere of `radius` voxels at IJK center into segment.'''
    import numpy as np
    ci, cj, ck = ijk_center
    arr = slicer.util.arrayFromSegmentBinaryLabelmap(seg_node, seg_id, ref_volume)
    k_dim, j_dim, i_dim = arr.shape
    kk, jj, ii = np.ogrid[
        max(0, ck - radius):min(k_dim, ck + radius + 1),
        max(0, cj - radius):min(j_dim, cj + radius + 1),
        max(0, ci - radius):min(i_dim, ci + radius + 1),
    ]
    mask = (ii - ci)**2 + (jj - cj)**2 + (kk - ck)**2 <= radius**2
    arr[
        max(0, ck - radius):min(k_dim, ck + radius + 1),
        max(0, cj - radius):min(j_dim, cj + radius + 1),
        max(0, ci - radius):min(i_dim, ci + radius + 1),
    ][mask] = 1
    slicer.util.updateSegmentBinaryLabelmapFromArray(arr, seg_node, seg_id, ref_volume)
"""


async def test_demo_alignment(
    slicer_service: SlicerService,
    slicer_url: str,
    pacs_monkey_patch: str,
    demo_working_dir: Path,
    orthanc_patient_with_two_studies: dict[str, Any],
) -> None:
    """Open Slicer with two studies, 10 random ROIs on the reference."""
    patient = orthanc_patient_with_two_studies
    study_a = patient["studies"][0]
    study_b = patient["studies"][1]

    ref_study_uid = study_a["study_uid"]
    mov_study_uid = study_b["study_uid"]

    script = f"""\
{pacs_monkey_patch}
{_paint_spheres_script()}
import numpy as np

s = SlicerHelper('{demo_working_dir}')

# --- Load reference volume from PACS ---
s.load_study_from_pacs('{ref_study_uid}', window=(-200, 300))
ref_vol = s._image_node
print(f"[Demo] reference volume loaded from study {ref_study_uid}")

# --- Create reference segmentation with 10 ROIs (helper API) ---
ref_seg = s.create_segmentation("Reference")
colors = [
    (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
    (1.0, 1.0, 0.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0),
    (1.0, 0.5, 0.0), (0.5, 0.0, 1.0), (0.0, 0.5, 0.5),
    (0.8, 0.8, 0.8),
]
for i in range(10):
    ref_seg.add_segment(f"ROI_{{i+1}}", colors[i])

# --- Paint random spheres (test setup, not helper API) ---
ref_node = ref_seg.node
vtk_seg = ref_node.GetSegmentation()

bounds = [0.0] * 6
ref_vol.GetRASBounds(bounds)
margin = [0.15 * (bounds[i+1] - bounds[i]) for i in (0, 2, 4)]
inner_min = [bounds[0] + margin[0], bounds[2] + margin[1], bounds[4] + margin[2]]
inner_max = [bounds[1] - margin[0], bounds[3] - margin[1], bounds[5] - margin[2]]

ras_to_ijk = vtk.vtkMatrix4x4()
ref_vol.GetRASToIJKMatrix(ras_to_ijk)

np.random.seed(42)
for i in range(10):
    seg_id = vtk_seg.GetNthSegmentID(i)
    cx = np.random.uniform(inner_min[0], inner_max[0])
    cy = np.random.uniform(inner_min[1], inner_max[1])
    cz = np.random.uniform(inner_min[2], inner_max[2])
    ijk = ras_to_ijk.MultiplyPoint([cx, cy, cz, 1.0])
    _paint_sphere(ref_node, seg_id, ref_vol, (int(round(ijk[0])), int(round(ijk[1])), int(round(ijk[2]))))
    print(f"[Demo] ROI_{{i+1}} painted at RAS=({{cx:.0f}}, {{cy:.0f}}, {{cz:.0f}})")

# --- Verify centroids computed by helper ---
names = s.get_segment_names(ref_seg)
print(f"[Demo] segments: {{names}}")
for name in names:
    c = s.get_segment_centroid(ref_seg, name)
    if c:
        print(f"[Demo] {{name}} centroid: R={{c[0]:.1f}}, A={{c[1]:.1f}}, S={{c[2]:.1f}}")

# --- Load moving volume from PACS ---
s.load_study_from_pacs('{mov_study_uid}', window=(-200, 300))
mov_vol = s._image_node
print(f"[Demo] moving volume loaded from study {mov_study_uid}")

# --- Copy segments to projection (helper API) ---
projection = s.create_segmentation("Projection")
s.copy_segments(ref_seg, projection, empty=True)

# --- Dual layout (helper API) ---
s.set_dual_layout(ref_vol, mov_vol, seg_a=ref_seg, seg_b=projection, linked=False)

# --- Center alignment (helper API) ---
align_tf = s.align_by_center(mov_vol, ref_vol, moving_segmentation=projection)

# --- Refinement callback (helper API) ---
def _refine():
    n = s.refine_alignment_by_centroids(projection, ref_seg, align_tf)
    print(f"[Demo] alignment refined with {{n}} landmark pairs")

# --- Editor on projection (helper API) ---
s.setup_editor(projection, effect="Paint", brush_size=5.0, source_volume=mov_vol)

# --- Focus observer (helper API) ---
s.setup_segment_focus_observer(
    projection,
    ref_seg,
    reference_views=["Red"],
    editable_views=["Yellow"],
    only_empty=False,
    on_refine=_refine,
)

s.add_view_shortcuts()
s.annotate("Demo: paint ROIs on projection to match reference, alignment refines on each click")

__execResult = {{
    "status": "ok",
    "ref_study": "{ref_study_uid}",
    "mov_study": "{mov_study_uid}",
    "roi_count": 10,
}}
"""
    result = await slicer_service.execute(slicer_url, script, request_timeout=120.0)
    assert result.get("status") == "ok"
    print(f"[Demo] alignment demo loaded: ref={ref_study_uid}, mov={mov_study_uid}, 10 ROIs")
