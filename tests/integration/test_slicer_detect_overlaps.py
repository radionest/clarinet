"""Integration tests for SlicerHelper.detect_overlaps — require a running 3D Slicer instance."""

import pytest

from clarinet.exceptions.domain import SlicerError
from clarinet.services.slicer.service import SlicerService

pytestmark = [
    pytest.mark.slicer,
    pytest.mark.asyncio,
    pytest.mark.usefixtures("_check_slicer"),
    pytest.mark.xdist_group("slicer"),
]


async def test_detect_overlaps_pair_and_disjoint(
    slicer_service: SlicerService, slicer_url: str
) -> None:
    """Overlapping segment pair is reported with correct stats; a disjoint pair yields []."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np

s = SlicerHelper(working_folder)

vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_OvVol')
slicer.util.updateVolumeFromArray(vol, np.zeros((20, 20, 20), dtype=np.int16))
s._image_node = vol
seg_logic = slicer.modules.segmentations.logic()


def _seg_with_blob(name, sl):
    node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', name)
    node.CreateDefaultDisplayNodes()
    node.SetReferenceImageGeometryParameterFromVolumeNode(vol)
    lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', name + '_lm')
    arr = np.zeros((20, 20, 20), dtype=np.uint8)
    arr[sl] = 1
    slicer.util.updateVolumeFromArray(lm, arr)
    seg_logic.ImportLabelmapToSegmentationNode(lm, node)
    slicer.mrmlScene.RemoveNode(lm)
    return node


projection = _seg_with_blob('Projection', np.s_[10:15, 10:15, 10:15])
doctor = _seg_with_blob('Doctor', np.s_[8:17, 8:17, 8:17])
far = _seg_with_blob('Far', np.s_[2:4, 2:4, 2:4])

# Rename each blob's single imported segment to a known name so name_a/name_b
# in the detect_overlaps report can be asserted deterministically.
proj_seg = projection.GetSegmentation()
proj_seg.GetSegment(proj_seg.GetNthSegmentID(0)).SetName('proj')
doc_seg = doctor.GetSegmentation()
doc_seg.GetSegment(doc_seg.GetNthSegmentID(0)).SetName('doc')

overlaps = s.detect_overlaps(projection, doctor)
disjoint = s.detect_overlaps(far, doctor)

__execResult = {"overlaps": overlaps, "disjoint": disjoint}
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )

    overlaps = result["overlaps"]
    assert len(overlaps) == 1
    entry = overlaps[0]
    assert set(entry) == {
        "name_a",
        "name_b",
        "inter",
        "size_a",
        "size_b",
        "dice",
        "iou",
        "centroid_distance_mm",
    }
    assert entry["name_a"] == "proj"
    assert entry["name_b"] == "doc"
    # Projection (5**3=125 voxels) sits fully inside Doctor (9**3=729 voxels),
    # so every projection voxel overlaps doctor: inter == size_a. This
    # containment invariant is the primary, grid-independent check. The exact
    # voxel counts below also hold because both segments are exported on the
    # shared 20**3 identity grid via nearest-neighbor labelmap import — no
    # resampling to shift them.
    assert entry["inter"] > 0
    assert entry["inter"] == entry["size_a"]
    assert entry["size_a"] == 125
    assert entry["size_b"] == 729
    assert 0 < entry["dice"] <= 1
    assert 0 < entry["iou"] <= 1

    assert result["disjoint"] == []


async def test_detect_overlaps_without_bundle_raises(
    slicer_service: SlicerService, slicer_url: str
) -> None:
    """detect_overlaps without execute(..., include_correspondence=True) raises SlicerError."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
# Scrub any build_overlap_graph leaked into Slicer's persistent exec-namespace by a
# prior include_correspondence=True call in this session — the guard reads it via the
# method's __globals__, which a bare `del` in this script (running in the _ns copy) can't reach.
SlicerHelper.detect_overlaps.__globals__.pop('build_overlap_graph', None)
seg = s.create_segmentation('Trivial').add_segment('x', (1, 0, 0))
s.detect_overlaps(seg, seg)
"""
    with pytest.raises(SlicerError):
        await slicer_service.execute(slicer_url, script, context=context)
