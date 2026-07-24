"""Integration tests for SlicerHelper DSL — require a running 3D Slicer instance.

These tests send the helper DSL + user code to a real Slicer and verify
the workspace is set up correctly.
"""

import shutil
from pathlib import Path
from typing import ClassVar

import numpy as np
import pydicom
import pydicom.uid
import pytest
from pydicom.dataset import FileDataset

from clarinet.services.image import FileType, Image
from clarinet.services.slicer.helper import PacsHelper, SlicerHelper
from clarinet.services.slicer.service import SlicerService

pytestmark = [
    pytest.mark.slicer,
    pytest.mark.asyncio,
    pytest.mark.usefixtures("_check_slicer"),
    pytest.mark.xdist_group("slicer"),
]


async def test_load_volume(
    slicer_service: SlicerService,
    slicer_url: str,
    test_images_path: str,
) -> None:
    """Load a test volume file and verify the node exists."""
    context = {"working_folder": str(test_images_path)}
    script = """
import os
os.makedirs(working_folder, exist_ok=True)
s = SlicerHelper(working_folder)
# List files to find a volume
files = os.listdir(working_folder)
nrrd_files = [f for f in files if f.endswith(('.nrrd', '.nii', '.nii.gz'))]
if nrrd_files:
    vol = s.load_volume(nrrd_files[0], window=(-1100, 35))
    print(vol.GetName())
else:
    print('no_test_files')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_create_segmentation(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Create a segmentation with segments and verify it exists."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
seg = s.create_segmentation('TestSeg')
seg.add_segment('Segment1', color=(1, 0, 0)).add_segment('Segment2', color=(0, 1, 0))
seg_obj = seg.node.GetSegmentation()
print(seg_obj.GetNumberOfSegments())
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_setup_editor(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Set up the segment editor with Paint effect."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
seg = s.create_segmentation('EditorTest')
seg.add_segment('Test', color=(1, 0, 0))
s.setup_editor(seg, effect='Paint', brush_size=30, sphere_brush=True)
print('editor_ok')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_setup_editor_no_effect(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """setup_editor(effect=None) opens the editor without any active drawing tool."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
seg = s.create_segmentation('NoEffectTest')
seg.add_segment('Probe', color=(1, 0, 0))
s.setup_editor(seg, effect=None)
editor = slicer.modules.segmenteditor.widgetRepresentation().self().editor
__execResult = {'has_active_effect': editor.activeEffect() is not None}
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)
    assert result.get("has_active_effect") is False


async def test_setup_segment_focus_observer_requires_editor(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Observer raises SlicerHelperError when no editor node is in the scene."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)  # __init__ clears the scene
seg = s.create_segmentation('NoEditorObserver')
seg.add_segment('A', color=(1, 0, 0))
try:
    s.setup_segment_focus_observer(seg, seg)
    raised = False
    msg = ''
except SlicerHelperError as e:
    raised = True
    msg = str(e)
__execResult = {'raised': raised, 'msg': msg}
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)
    assert result.get("raised") is True
    assert "setup_editor" in result.get("msg", "")


async def test_set_layout(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Switch between different layouts."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
for layout in ['axial', 'sagittal', 'coronal', 'four_up']:
    s.set_layout(layout)
print('layouts_ok')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_full_workspace_setup(
    slicer_service: SlicerService,
    slicer_url: str,
    test_images_path: str,
) -> None:
    """Full workspace setup scenario (without real volume if no test files)."""
    context = {
        "working_folder": str(test_images_path),
        "patient_name": "TEST_PATIENT",
    }
    script = """
import os
os.makedirs(working_folder, exist_ok=True)
s = SlicerHelper(working_folder)

# Create segmentation even without volume
seg = s.create_segmentation('WorkspaceTest')
seg.add_segment('Region1', color=(0, 0, 1))

s.set_layout('axial')
s.add_view_shortcuts()
print('workspace_ok')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


# --- PacsHelper unit tests (run without Slicer via _Dummy fallback) ---


class TestPacsHelperConstruction:
    """Tests for PacsHelper that don't require a running Slicer."""

    pytestmark: ClassVar[list[pytest.MarkDecorator]] = [
        pytest.mark.slicer,
        pytest.mark.usefixtures("_check_slicer"),
        pytest.mark.xdist_group("slicer"),
    ]

    def test_constructor_stores_params(self) -> None:
        """PacsHelper stores all connection parameters."""
        pacs = PacsHelper(
            host="192.168.1.10",
            port=4242,
            called_aet="PACS",
            calling_aet="SLICER",
            retrieve_mode="c-get",
            move_aet="SLICER",
        )
        assert pacs.host == "192.168.1.10"
        assert pacs.port == 4242
        assert pacs.called_aet == "PACS"
        assert pacs.calling_aet == "SLICER"
        assert pacs.retrieve_mode == "c-get"
        assert pacs.move_aet == "SLICER"

    def test_constructor_defaults(self) -> None:
        """PacsHelper uses sensible defaults for optional params."""
        pacs = PacsHelper(
            host="10.0.0.1",
            port=11112,
            called_aet="ORTHANC",
            calling_aet="MYSCU",
        )
        assert pacs.retrieve_mode == "c-get"
        assert pacs.move_aet == "MYSCU"

    def test_slicer_helper_has_load_study_from_pacs(self) -> None:
        """SlicerHelper exposes load_study_from_pacs method."""
        assert hasattr(SlicerHelper, "load_study_from_pacs")
        assert callable(SlicerHelper.load_study_from_pacs)


# --- SlicerHelper new methods: unit tests (no Slicer needed) ---


class TestSlicerHelperMethodsExist:
    """Verify new methods exist on SlicerHelper (no running Slicer required)."""

    def test_get_segment_names_exists(self) -> None:
        """SlicerHelper exposes get_segment_names method."""
        assert hasattr(SlicerHelper, "get_segment_names")
        assert callable(SlicerHelper.get_segment_names)

    def test_get_segment_centroid_exists(self) -> None:
        """SlicerHelper exposes get_segment_centroid method."""
        assert hasattr(SlicerHelper, "get_segment_centroid")
        assert callable(SlicerHelper.get_segment_centroid)

    def test_copy_segments_exists(self) -> None:
        """SlicerHelper exposes copy_segments method."""
        assert hasattr(SlicerHelper, "copy_segments")
        assert callable(SlicerHelper.copy_segments)

    def test_auto_number_segment_exists(self) -> None:
        """SlicerHelper exposes auto_number_segment method."""
        assert hasattr(SlicerHelper, "auto_number_segment")
        assert callable(SlicerHelper.auto_number_segment)

    def test_load_study_from_pacs_raise_on_empty_param(self) -> None:
        """load_study_from_pacs has raise_on_empty param defaulting to True."""
        import inspect

        sig = inspect.signature(SlicerHelper.load_study_from_pacs)
        param = sig.parameters["raise_on_empty"]
        assert param.default is True
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_load_series_from_pacs_raise_on_empty_param(self) -> None:
        """load_series_from_pacs has raise_on_empty param defaulting to True."""
        import inspect

        sig = inspect.signature(SlicerHelper.load_series_from_pacs)
        param = sig.parameters["raise_on_empty"]
        assert param.default is True
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_subtract_segmentations_exists(self) -> None:
        """SlicerHelper exposes subtract_segmentations method."""
        assert hasattr(SlicerHelper, "subtract_segmentations")
        assert callable(SlicerHelper.subtract_segmentations)

    def test_set_dual_layout_exists(self) -> None:
        """SlicerHelper exposes set_dual_layout method."""
        assert hasattr(SlicerHelper, "set_dual_layout")
        assert callable(SlicerHelper.set_dual_layout)

    def test_setup_segment_focus_observer_exists(self) -> None:
        """SlicerHelper exposes setup_segment_focus_observer method."""
        assert hasattr(SlicerHelper, "setup_segment_focus_observer")
        assert callable(SlicerHelper.setup_segment_focus_observer)

    def test_get_largest_island_centroid_exists(self) -> None:
        """SlicerHelper exposes get_largest_island_centroid method."""
        assert hasattr(SlicerHelper, "get_largest_island_centroid")
        assert callable(SlicerHelper.get_largest_island_centroid)

    def test_setup_segment_focus_observer_island_segments_param(self) -> None:
        """setup_segment_focus_observer accepts island_segments with default None."""
        import inspect

        sig = inspect.signature(SlicerHelper.setup_segment_focus_observer)
        param = sig.parameters["island_segments"]
        assert param.default is None

    def test_setup_editor_effect_accepts_none(self) -> None:
        """setup_editor accepts effect=None (read-only / observer mode)."""
        import inspect
        from typing import get_args, get_type_hints

        sig = inspect.signature(SlicerHelper.setup_editor)
        assert sig.parameters["effect"].default == "Paint"
        # Resolved annotation is ``EditorEffectName | None`` — the union
        # must include ``NoneType``.
        hints = get_type_hints(SlicerHelper.setup_editor)
        assert type(None) in get_args(hints["effect"])

    def test_setop_resample_param_defaults_false(self) -> None:
        """The three set-ops expose resample=False (safe-by-default geometry guard)."""
        import inspect

        for name in ("subtract_segmentations", "binarize_and_split_islands", "merge_as_pool"):
            sig = inspect.signature(getattr(SlicerHelper, name))
            assert "resample" in sig.parameters, f"{name} missing resample param"
            assert sig.parameters["resample"].default is False, f"{name} resample default"

    def test_export_segments_labelmap_resample_param(self) -> None:
        """The choke point accepts a keyword-only resample=False."""
        import inspect

        sig = inspect.signature(SlicerHelper._export_segments_labelmap)
        param = sig.parameters["resample"]
        assert param.default is False
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


# --- SlicerHelper new methods: integration tests (require running Slicer) ---


async def test_get_segment_names(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Create segmentation, add segments, verify names list."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
seg = s.create_segmentation('NameTest')
seg.add_segment('Alpha', (1, 0, 0))
seg.add_segment('Beta', (0, 1, 0))
seg.add_segment('Gamma', (0, 0, 1))
names = s.get_segment_names(seg)
assert names == ['Alpha', 'Beta', 'Gamma'], f"Expected ['Alpha', 'Beta', 'Gamma'], got {names}"
print('names_ok')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_copy_segments_full(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Copy segments with data and verify target has them."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
source = s.create_segmentation('CopySource')
source.add_segment('Seg1', (1, 0, 0)).add_segment('Seg2', (0, 1, 0))
target = s.create_segmentation('CopyTarget')
s.copy_segments(source, target)
target_names = s.get_segment_names(target)
assert target_names == ['Seg1', 'Seg2'], f"Expected ['Seg1', 'Seg2'], got {target_names}"
print('copy_full_ok')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_copy_segments_empty(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Copy segment structure only (empty=True), verify names match."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
source = s.create_segmentation('EmptySource')
source.add_segment('R1', (1, 0, 0)).add_segment('R2', (0, 1, 0))
target = s.create_segmentation('EmptyTarget')
s.copy_segments(source, target, empty=True)
target_names = s.get_segment_names(target)
assert target_names == ['R1', 'R2'], f"Expected ['R1', 'R2'], got {target_names}"
print('copy_empty_ok')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_auto_number_segment(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Auto-number segments: ROI_1, ROI_2 exist → auto_number returns 3."""
    context = {"working_folder": "/tmp"}
    script = """
s = SlicerHelper(working_folder)
seg = s.create_segmentation('AutoNumTest')
seg.node.GetSegmentation().AddEmptySegment('ROI_1', 'ROI_1')
seg.node.GetSegmentation().AddEmptySegment('ROI_2', 'ROI_2')
num = s.auto_number_segment(seg)
assert num == 3, f"Expected 3, got {num}"
names = s.get_segment_names(seg)
assert 'ROI_3' in names, f"ROI_3 not found in {names}"
print('auto_number_ok')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_set_dual_layout(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Set dual layout — verify it runs without error."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np
s = SlicerHelper(working_folder)
seg_a = s.create_segmentation('DualA')
seg_b = s.create_segmentation('DualB')
# Create volumes with actual image data to avoid VTK "Input port 0 has 0 connections"
# warnings that flood the event loop and block subsequent HTTP requests
vol_a = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', 'VolA')
vol_b = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', 'VolB')
dummy = np.zeros((2, 2, 2), dtype=np.int16)
slicer.util.updateVolumeFromArray(vol_a, dummy)
slicer.util.updateVolumeFromArray(vol_b, dummy)
s.set_dual_layout(vol_a, vol_b, seg_a=seg_a, seg_b=seg_b, linked=True)
print('dual_layout_ok')
# Clear scene to stop VTK rendering on empty views between tests
slicer.mrmlScene.Clear(0)
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_get_largest_island_centroid(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Largest-island centroid lands inside the bigger blob, not between islands."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np

s = SlicerHelper(working_folder)

# Create a volume so the segmentation has reference geometry.
vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_IslandVol')
dummy = np.zeros((60, 60, 60), dtype=np.int16)
slicer.util.updateVolumeFromArray(vol, dummy)

seg_node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', '_IslandSeg')
seg_node.CreateDefaultDisplayNodes()
seg_node.SetReferenceImageGeometryParameterFromVolumeNode(vol)

seg_logic = slicer.modules.segmentations.logic()

# Build a labelmap with two disconnected blobs:
#   Small blob: voxels [5:10, 5:10, 5:10]   (125 voxels)
#   Large blob: voxels [40:55, 40:55, 40:55] (3375 voxels)
lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', '_IslandLM')
arr = np.zeros((60, 60, 60), dtype=np.uint8)
arr[5:10, 5:10, 5:10] = 1      # small island
arr[40:55, 40:55, 40:55] = 1   # large island
slicer.util.updateVolumeFromArray(lm, arr)
seg_logic.ImportLabelmapToSegmentationNode(lm, seg_node)
slicer.mrmlScene.RemoveNode(lm)

# Rename the imported segment to "_pool"
vtk_seg = seg_node.GetSegmentation()
seg_id = vtk_seg.GetNthSegmentID(0)
vtk_seg.GetSegment(seg_id).SetName('_pool')

# Compute centroids
island_c = s.get_largest_island_centroid(seg_node, '_pool')
overall_c = s.get_segment_centroid(seg_node, '_pool')

assert island_c is not None, "island centroid should not be None"
assert overall_c is not None, "overall centroid should not be None"

# The large blob spans voxels 40-54 in each axis.
# The overall BB center spans 5-54, midpoint ~(29.5, 29.5, 29.5) — between blobs.
# Verify island centroid is different from overall (not in the gap between islands).
dist = sum((a - b) ** 2 for a, b in zip(island_c, overall_c)) ** 0.5
assert dist > 1.0, (
    f"island and overall centroids should differ significantly, "
    f"got dist={dist:.2f}, island={island_c}, overall={overall_c}"
)

__execResult = {
    "island_centroid": list(island_c),
    "overall_centroid": list(overall_c),
    "distance": dist,
}
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)
    assert result.get("distance", 0) > 1.0


async def test_segmentation_has_voxels_real_slicer(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """_segmentation_has_voxels: True for painted voxels, False for empty/0-segment.

    Validates the set-op guard's empty-vs-foreign discriminator against real VTK
    bindings (the pure-Python unit tests monkeypatch this primitive).
    """
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np
s = SlicerHelper(working_folder)
vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_HVVol')
slicer.util.updateVolumeFromArray(vol, np.zeros((20, 20, 20), dtype=np.int16))
s._image_node = vol

# Segmentation with painted voxels.
filled = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', '_Filled')
filled.CreateDefaultDisplayNodes()
filled.SetReferenceImageGeometryParameterFromVolumeNode(vol)
lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', '_HVLM')
arr = np.zeros((20, 20, 20), dtype=np.uint8)
arr[5:10, 5:10, 5:10] = 1
slicer.util.updateVolumeFromArray(lm, arr)
slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(lm, filled)
slicer.mrmlScene.RemoveNode(lm)

# Segmentation with a segment but no voxels.
voxelless = s.create_segmentation('_Voxelless').add_segment('e', (1, 0, 0)).node

# Segmentation with no segments at all.
empty = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', '_NoSeg')
empty.CreateDefaultDisplayNodes()
empty.SetReferenceImageGeometryParameterFromVolumeNode(vol)

__execResult = {
    "filled": _segmentation_has_voxels(filled),
    "voxelless": _segmentation_has_voxels(voxelless),
    "no_segments": _segmentation_has_voxels(empty),
}
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert result.get("filled") is True
    assert result.get("voxelless") is False
    assert result.get("no_segments") is False


async def test_labelmap_guard_discriminates_empty_vs_voxels(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """_labelmap_array_or_raise on a no-image-data labelmap: raises iff source has voxels.

    A labelmap node with no image data is the exact precondition that crashed
    arrayFromVolume. The guard must raise (flipped/foreign grid) when the source
    carries voxels, and tolerate (return None) when the source is genuinely empty.
    """
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np
s = SlicerHelper(working_folder)
vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_GuardVol')
slicer.util.updateVolumeFromArray(vol, np.zeros((20, 20, 20), dtype=np.int16))

# Source WITH voxels.
filled = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', '_GFilled')
filled.CreateDefaultDisplayNodes()
filled.SetReferenceImageGeometryParameterFromVolumeNode(vol)
lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', '_GLM')
arr = np.zeros((20, 20, 20), dtype=np.uint8)
arr[5:10, 5:10, 5:10] = 1
slicer.util.updateVolumeFromArray(lm, arr)
slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(lm, filled)
slicer.mrmlScene.RemoveNode(lm)

# Genuinely empty source (no segments).
empty = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', '_GEmpty')
empty.CreateDefaultDisplayNodes()
empty.SetReferenceImageGeometryParameterFromVolumeNode(vol)

# Labelmaps with NO image data — the crash precondition.
bad_lm_a = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', '_BadLMa')
raised_on_voxels = False
try:
    _labelmap_array_or_raise(bad_lm_a, filled, what='a seg with voxels')
except SlicerHelperError:
    raised_on_voxels = True

bad_lm_b = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', '_BadLMb')
tolerated_empty = _labelmap_array_or_raise(bad_lm_b, empty, what='an empty seg') is None

__execResult = {
    "raised_on_voxels": raised_on_voxels,
    "tolerated_empty": tolerated_empty,
}
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert result.get("raised_on_voxels") is True
    assert result.get("tolerated_empty") is True


async def test_setop_tolerates_emptied_source(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """NDT demo second_review flow: a subtract empties the source, set-ops tolerate it.

    Reproduces the regression: when the inspector covers every projected defect,
    `missed` becomes empty and feeds merge_as_pool / subtract / binarize. These
    must no-op (with a warning), not raise.
    """
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np
s = SlicerHelper(working_folder)
vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_ChainVol')
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
inspector = _seg_with_blob('Inspector', np.s_[8:17, 8:17, 8:17])

# Inspector fully covers the projected ROI → ROI-level subtract empties the result.
missed = s.subtract_segmentations(projection, inspector, output_name='_Missed')
missed_segments = missed.GetSegmentation().GetNumberOfSegments()

classification = s.create_segmentation('Classification').add_segment('defect', (1, 0, 0))

# All three must tolerate the now-empty `missed` (pre-fix they raised an opaque error).
s.subtract_segmentations(missed, classification, max_overlap_ratio=0.05)
s.merge_as_pool(missed, classification)
islands = s.binarize_and_split_islands(missed)

__execResult = {
    "missed_segments": missed_segments,
    "islands": islands.GetSegmentation().GetNumberOfSegments(),
    "ok": True,
}
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )
    assert result.get("ok") is True
    assert result.get("missed_segments") == 0
    assert result.get("islands") == 0


async def test_load_segmentation_grid_guard(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """load_segmentation raises on a grid mismatch, loads cleanly on a matching grid."""
    context = {"working_folder": "/tmp"}
    script = """
import os
import numpy as np

s = SlicerHelper(working_folder)
vol_a = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', 'VolA')
slicer.util.updateVolumeFromArray(vol_a, np.zeros((20, 20, 20), dtype=np.int16))

save_seg = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', 'SaveSeg')
save_seg.CreateDefaultDisplayNodes()
save_seg.SetReferenceImageGeometryParameterFromVolumeNode(vol_a)
lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', 'SaveLM')
arr = np.zeros((20, 20, 20), dtype=np.uint8)
arr[5:10, 5:10, 5:10] = 1
slicer.util.updateVolumeFromArray(lm, arr)
slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(lm, save_seg)
slicer.mrmlScene.RemoveNode(lm)
seg_path = os.path.join(working_folder, '_grid_guard_test.seg.nrrd')
slicer.util.exportNode(save_seg, seg_path)

# Mismatched grid: different dims + origin → guard must raise.
s_bad = SlicerHelper(working_folder)  # clears the scene
vol_b = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', 'VolB')
slicer.util.updateVolumeFromArray(vol_b, np.zeros((30, 25, 15), dtype=np.int16))
vol_b.SetOrigin(100.0, 50.0, 25.0)
s_bad._image_node = vol_b
mismatch_raised = False
try:
    s_bad.load_segmentation(seg_path, 'LoadedBad')
except SlicerHelperError:
    mismatch_raised = True

# Matching grid: same dims + origin as the saved geometry → must load cleanly.
s_ok = SlicerHelper(working_folder)  # clears the scene
vol_ok = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', 'VolOk')
slicer.util.updateVolumeFromArray(vol_ok, np.zeros((20, 20, 20), dtype=np.int16))
s_ok._image_node = vol_ok
match_ok = True
try:
    s_ok.load_segmentation(seg_path, 'LoadedOk')
except SlicerHelperError:
    match_ok = False

os.remove(seg_path)
__execResult = {"mismatch_raised": mismatch_raised, "match_ok": match_ok}
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert result.get("mismatch_raised") is True
    assert result.get("match_ok") is True


async def test_subtract_segmentations_guards_grid_mismatch(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """A seg whose recorded geometry mismatches the source volume grid raises by
    default; resample=True re-grids a still-overlapping mask instead of raising."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np

def _volume(name, dim):
    node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', name)
    arr = np.zeros((dim, dim, dim), dtype=np.int16)
    arr[1:4, 1:4, 1:4] = 1
    slicer.util.updateVolumeFromArray(node, arr)
    node.SetOrigin(0.0, 0.0, 0.0)
    return node

s = SlicerHelper(working_folder)
vol_ref = _volume('VolRef', 6)   # seg is recorded on this 6^3 grid
vol_src = _volume('VolSrc', 8)   # source volume is a different 8^3 grid (dims mismatch, overlapping)

seg = s.create_segmentation('MismatchSeg')
seg.node.SetReferenceImageGeometryParameterFromVolumeNode(vol_ref)
lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', 'SeedLM')
slicer.util.updateVolumeFromArray(lm, (slicer.util.arrayFromVolume(vol_ref) > 0).astype(np.uint8))
lm.SetOrigin(0.0, 0.0, 0.0)
slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(lm, seg.node)
slicer.mrmlScene.RemoveNode(lm)

other = s.create_segmentation('Other')  # empty second operand (tolerated, exports first)
s._image_node = vol_src

raised = False
try:
    s.subtract_segmentations(seg, other)
except Exception as exc:
    raised = 'does not match the volume grid' in str(exc)

bypassed = True
try:
    s.subtract_segmentations(seg, other, resample=True)
except Exception:
    bypassed = False

__execResult = {'raised': raised, 'bypassed': bypassed}
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )
    assert isinstance(result, dict)
    assert result["raised"] is True
    assert result["bypassed"] is True


async def test_subtract_strategy_override_live(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """An explicit strategy built from bundle symbols drives the verdict."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np
s = SlicerHelper(working_folder)
vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_StratVol')
slicer.util.updateVolumeFromArray(vol, np.zeros((20, 20, 20), dtype=np.int16))
s._image_node = vol
seg_logic = slicer.modules.segmentations.logic()


def _seg_from(name, slices):
    node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', name)
    node.CreateDefaultDisplayNodes()
    node.SetReferenceImageGeometryParameterFromVolumeNode(vol)
    lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', name + '_lm')
    arr = np.zeros((20, 20, 20), dtype=np.uint8)
    for value, sl in enumerate(slices, start=1):
        arr[sl] = value
    slicer.util.updateVolumeFromArray(lm, arr)
    seg_logic.ImportLabelmapToSegmentationNode(lm, node)
    slicer.mrmlScene.RemoveNode(lm)
    return node


# Base: one 4x4x4 blob. Other: identical blob -> IoU 1.0.
base = _seg_from('StratBase', [np.s_[5:9, 5:9, 5:9]])
other = _seg_from('StratOther', [np.s_[5:9, 5:9, 5:9]])

# Permissive scalar thresholds would keep nothing anyway; prove the strategy
# object wins by using one that only matches at IoU >= 0.9.
out = s.subtract_segmentations(
    base, other, output_name='_StratOut',
    strategy=ThresholdMatch(IoU(), min_score=0.9),
)
removed_by_strategy = out.GetSegmentation().GetNumberOfSegments()

# Same scene, disjoint other -> IoU 0 -> kept.
disjoint = _seg_from('StratDisjoint', [np.s_[15:18, 15:18, 15:18]])
out2 = s.subtract_segmentations(
    base, disjoint, output_name='_StratOut2',
    strategy=ThresholdMatch(IoU(), min_score=0.9),
)
kept_when_disjoint = out2.GetSegmentation().GetNumberOfSegments()

__execResult = {"removed_by_strategy": removed_by_strategy, "kept_when_disjoint": kept_when_disjoint}
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )
    assert result["removed_by_strategy"] == 0
    assert result["kept_when_disjoint"] == 1


async def test_subtract_union_granularity_live(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Fragmented sub-threshold overlap: default keeps, union removes (D8)."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np
s = SlicerHelper(working_folder)
vol = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', '_GranVol')
slicer.util.updateVolumeFromArray(vol, np.zeros((20, 20, 20), dtype=np.int16))
s._image_node = vol
seg_logic = slicer.modules.segmentations.logic()


def _seg_from(name, slices):
    node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', name)
    node.CreateDefaultDisplayNodes()
    node.SetReferenceImageGeometryParameterFromVolumeNode(vol)
    lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', name + '_lm')
    arr = np.zeros((20, 20, 20), dtype=np.uint8)
    for value, sl in enumerate(slices, start=1):
        arr[sl] = value
    slicer.util.updateVolumeFromArray(lm, arr)
    seg_logic.ImportLabelmapToSegmentationNode(lm, node)
    slicer.mrmlScene.RemoveNode(lm)
    return node


# Base row of 10 voxels; two disjoint 3-voxel subtracted segments (0.3 each, 0.6 joint).
base = _seg_from('GranBase', [np.s_[5, 3:13, 5]])
frags = _seg_from('GranFrags', [np.s_[5, 3:6, 5], np.s_[5, 8:11, 5]])

out_label = s.subtract_segmentations(
    base, frags, output_name='_GranLabel', max_overlap_ratio=0.5,
)
out_union = s.subtract_segmentations(
    base, frags, output_name='_GranUnion', max_overlap_ratio=0.5, granularity='union',
)
__execResult = {
    "kept_default": out_label.GetSegmentation().GetNumberOfSegments(),
    "kept_union": out_union.GetSegmentation().GetNumberOfSegments(),
}
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )
    assert result["kept_default"] == 1
    assert result["kept_union"] == 0


async def test_binarize_and_split_islands_guards_grid_mismatch(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """A seg whose recorded geometry mismatches the source volume grid raises by
    default; resample=True re-grids a still-overlapping mask instead of raising."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np

def _volume(name, dim):
    node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', name)
    arr = np.zeros((dim, dim, dim), dtype=np.int16)
    arr[1:4, 1:4, 1:4] = 1
    slicer.util.updateVolumeFromArray(node, arr)
    node.SetOrigin(0.0, 0.0, 0.0)
    return node

s = SlicerHelper(working_folder)
vol_ref = _volume('VolRef', 6)   # seg is recorded on this 6^3 grid
vol_src = _volume('VolSrc', 8)   # source volume is a different 8^3 grid (dims mismatch, overlapping)

seg = s.create_segmentation('MismatchSeg')
seg.node.SetReferenceImageGeometryParameterFromVolumeNode(vol_ref)
lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', 'SeedLM')
slicer.util.updateVolumeFromArray(lm, (slicer.util.arrayFromVolume(vol_ref) > 0).astype(np.uint8))
lm.SetOrigin(0.0, 0.0, 0.0)
slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(lm, seg.node)
slicer.mrmlScene.RemoveNode(lm)

s._image_node = vol_src

raised = False
try:
    s.binarize_and_split_islands(seg)
except Exception as exc:
    raised = 'does not match the volume grid' in str(exc)

bypassed = True
try:
    s.binarize_and_split_islands(seg, output_name='_BinBypass', resample=True)
except Exception:
    bypassed = False

__execResult = {'raised': raised, 'bypassed': bypassed}
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)
    assert result["raised"] is True
    assert result["bypassed"] is True


async def test_export_segmentation_conform_to_roundtrip(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """conform_to repairs the load-time slice mirror end-to-end (design D4, Release A gate).

    Writes a synthetic left-handed (``det<0``) volume to disk, loads it (Slicer
    silently flips the slice axis), paints a fresh two-segment OVERLAPPING
    segmentation (distinct names; Slicer stores each overlapping segment in its
    own layer with the natural per-layer label value 1), then exports twice:

    * plain (``conform_to=None``) -> the export lands on the loaded node's
      flipped grid, so it relates to the on-disk volume as REARRANGED;
    * ``conform_to=<volume>`` -> re-gridded back onto the volume's on-disk grid
      (SAME, ``det<0`` verbatim), both overlapping layers + both segment names +
      per-segment label values preserved, voxels physically coincident with the
      plain export. The conformed file must stay metadata-identical to the plain
      export -- only the grid differs -- which pins Minor 2: the REARRANGED
      rebuild must not renumber layers/segments.

    A FOREIGN reference (different-shape / shifted-grid volume) must raise and
    write no file.
    """
    context = {"working_folder": "/tmp"}
    script = """
import os
import numpy as np
import SimpleITK as sitk

base = os.path.join(working_folder, 't6_conform')
os.makedirs(base, exist_ok=True)
vol_path = os.path.join(base, 'lh_volume.nrrd')
foreign_path = os.path.join(base, 'foreign_volume.nrrd')
plain_path = os.path.join(base, 'export_plain.seg.nrrd')
conf_path = os.path.join(base, 'export_conformed.seg.nrrd')
foreign_out = os.path.join(base, 'export_foreign.seg.nrrd')
for p in (vol_path, foreign_path, plain_path, conf_path, foreign_out):
    if os.path.isfile(p):
        os.remove(p)

nx, ny, nz = 6, 6, 6

# 1. Left-handed (det<0) source volume, written verbatim to disk via sitk.
vol_arr = np.zeros((nz, ny, nx), dtype=np.int16)   # sitk array is (z, y, x)
vol_arr[1:4, 2:4, 1:3] = 100
vimg = sitk.GetImageFromArray(vol_arr)
vimg.SetSpacing((1.0, 1.0, 1.0))
vimg.SetOrigin((0.0, 0.0, 0.0))
vimg.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0))  # det = -1
sitk.WriteImage(vimg, vol_path)

# Foreign reference: different shape + shifted origin -> guaranteed FOREIGN.
farr = np.zeros((5, 7, 8), dtype=np.int16)   # -> grid shape (8, 7, 5)
fimg = sitk.GetImageFromArray(farr)
fimg.SetSpacing((1.0, 1.0, 1.0))
fimg.SetOrigin((50.0, 60.0, 70.0))
fimg.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
sitk.WriteImage(fimg, foreign_path)

vol_grid = _read_grid_on_disk(vol_path)
vol_det = float(np.linalg.det(vol_grid.direction))


def _sitk_layers_and_coords(path):
    img = sitk.ReadImage(path)
    ncomp = int(img.GetNumberOfComponentsPerPixel())
    arr = sitk.GetArrayFromImage(img)
    if arr.ndim == 3:
        arr = arr[..., None]
    labelset = sorted({int(v) for v in np.unique(arr) if v != 0})
    fg = (arr != 0).any(axis=-1)
    zz, yy, xx = np.nonzero(fg)
    coords = set()
    for xi, yi, zi in zip(xx.tolist(), yy.tolist(), zz.tolist()):
        wp = img.TransformIndexToPhysicalPoint((int(xi), int(yi), int(zi)))
        coords.add((round(wp[0], 2), round(wp[1], 2), round(wp[2], 2)))
    return ncomp, labelset, coords


def _loadback_name_labels(path):
    node = slicer.util.loadSegmentation(path)
    vseg = node.GetSegmentation()
    out = {}
    for i in range(vseg.GetNumberOfSegments()):
        sid = vseg.GetNthSegmentID(i)
        seg_i = vseg.GetSegment(sid)
        out[seg_i.GetName()] = int(seg_i.GetLabelValue())
    slicer.mrmlScene.RemoveNode(node)
    return out


def _node_state(node):
    v = node.GetSegmentation()
    n = v.GetNumberOfSegments()
    names = []
    labels = {}
    for i in range(n):
        sid = v.GetNthSegmentID(i)
        s_i = v.GetSegment(sid)
        names.append(s_i.GetName())
        labels[s_i.GetName()] = int(s_i.GetLabelValue())
    return {'count': n, 'names': sorted(names), 'labels': labels}


# 2. Load the left-handed volume (Slicer flips the slice axis) + overlapping seg.
s = SlicerHelper(working_folder)
loaded = s.load_volume(vol_path, window=(-100, 200))
seg = s.create_segmentation('RoundTrip')
seg.add_segment('Alpha', (1.0, 0.0, 0.0)).add_segment('Beta', (0.0, 1.0, 0.0))
seg.node.SetReferenceImageGeometryParameterFromVolumeNode(loaded)
vtk_seg = seg.node.GetSegmentation()
sid_a = vtk_seg.GetNthSegmentID(0)
sid_b = vtk_seg.GetNthSegmentID(1)

mask_a = np.zeros((nz, ny, nx), dtype=np.uint8)
mask_b = np.zeros((nz, ny, nx), dtype=np.uint8)
mask_a[1:3, 2:4, 1:3] = 1
mask_b[2:4, 2:4, 1:3] = 1   # overlaps Alpha at z=2
slicer.util.updateSegmentBinaryLabelmapFromArray(mask_a, seg.node, sid_a, loaded)
slicer.util.updateSegmentBinaryLabelmapFromArray(mask_b, seg.node, sid_b, loaded)

# Overlapping segments live one-per-layer; Slicer gives each the per-layer
# foreground value 1. Identity is carried by the distinct NAMES (a genuinely
# distinct non-1 pair is not co-representable with 2 layers -- SetLabelValue
# collapses the overlap at export). Capture the natural label values as-is.
source_labels = {
    vtk_seg.GetSegment(sid_a).GetName(): int(vtk_seg.GetSegment(sid_a).GetLabelValue()),
    vtk_seg.GetSegment(sid_b).GetName(): int(vtk_seg.GetSegment(sid_b).GetLabelValue()),
}

# Snapshot the caller's node BEFORE any export -- the re-grid must never mutate it.
source_before = _node_state(seg.node)

result = {'vol_det': vol_det, 'source_labels': source_labels, 'source_before': source_before}

# 3a. Plain export -> REARRANGED vs the on-disk volume grid.
try:
    export_segmentation('RoundTrip', plain_path)
    result['plain_written'] = os.path.isfile(plain_path)
    plain_grid = _read_grid_on_disk(plain_path)
    result['plain_relation'] = grid_relation(plain_grid, vol_grid).kind.value
    ncomp_p, labels_p, coords_p = _sitk_layers_and_coords(plain_path)
    result['plain_layers'] = ncomp_p
    result['plain_labelset'] = labels_p
    result['plain_names_labels'] = _loadback_name_labels(plain_path)
except Exception as exc:
    result['plain_error'] = repr(exc)
    coords_p = None

# 3b. Conformed export -> SAME grid, det<0 verbatim, structure preserved.
try:
    export_segmentation('RoundTrip', conf_path, conform_to=vol_path)
    result['conf_written'] = os.path.isfile(conf_path)
    conf_grid = _read_grid_on_disk(conf_path)
    result['conf_relation'] = grid_relation(conf_grid, vol_grid).kind.value
    result['conf_det'] = float(np.linalg.det(conf_grid.direction))
    ncomp_c, labels_c, coords_c = _sitk_layers_and_coords(conf_path)
    result['conf_layers'] = ncomp_c
    result['conf_labelset'] = labels_c
    result['conf_names_labels'] = _loadback_name_labels(conf_path)
    result['coincident'] = (coords_p is not None and coords_c == coords_p)
    result['coord_count'] = [
        (len(coords_p) if coords_p is not None else -1),
        len(coords_c),
    ]
    # The caller's original node must be untouched by the conform re-grid.
    result['source_after'] = _node_state(seg.node)
except Exception as exc:
    result['conf_error'] = repr(exc)

# 4. FOREIGN reference -> raise, write nothing.
foreign_raised = False
foreign_msg = ''
try:
    export_segmentation('RoundTrip', foreign_out, conform_to=foreign_path)
except SlicerHelperError as exc:
    foreign_raised = True
    foreign_msg = str(exc)
result['foreign_raised'] = foreign_raised
result['foreign_msg'] = foreign_msg[:200]
result['foreign_no_file'] = not os.path.isfile(foreign_out)

__execResult = result
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )
    assert isinstance(result, dict)
    assert "plain_error" not in result, result.get("plain_error")
    assert "conf_error" not in result, result.get("conf_error")

    # Left-handed source; overlapping segments carry Slicer's natural per-layer
    # label value under distinct names.
    assert result["vol_det"] < 0
    assert result["source_labels"] == {"Alpha": 1, "Beta": 1}

    # Plain export mirrors the load-time slice flip: REARRANGED vs the volume.
    assert result["plain_written"] is True
    assert result["plain_relation"] == "rearranged"
    assert result["plain_layers"] == 2

    # Conformed export lands back on the volume's on-disk grid, det = -1.0 verbatim
    # (conf_relation == "same" already pins the full affine; this is precision).
    assert result["conf_written"] is True
    assert result["conf_relation"] == "same"
    assert abs(result["conf_det"] + 1.0) < 1e-6
    assert result["conf_layers"] == 2

    # Both segment names AND per-segment label values survive the re-grid, and
    # the conformed export stays metadata-identical to the plain one -- only the
    # grid differs (Minor 2: the REARRANGED rebuild must not renumber layers).
    assert result["conf_names_labels"] == result["source_labels"]
    assert result["conf_names_labels"] == result["plain_names_labels"]
    assert result["conf_labelset"] == result["plain_labelset"]

    # Re-gridded voxels are physically coincident with the plain export -- and the
    # paint was non-empty (8 + 8 - 4 overlap), so coincidence cannot pass vacuously.
    assert result["coord_count"] == [12, 12]
    assert result["coincident"] is True, result.get("coord_count")

    # The conform re-grid never mutates the caller's original node -- segment count,
    # names, and per-segment label values are identical before and after export.
    assert result["source_before"] == {
        "count": 2,
        "names": ["Alpha", "Beta"],
        "labels": {"Alpha": 1, "Beta": 1},
    }
    assert result["source_after"] == result["source_before"]

    # FOREIGN reference is refused for the right reason and leaves no artifact behind.
    assert result["foreign_raised"] is True
    assert "foreign" in result["foreign_msg"].lower()
    assert result["foreign_no_file"] is True


async def test_merge_as_pool_guards_grid_mismatch(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """A source seg whose recorded geometry mismatches the source volume grid
    raises by default; resample=True re-grids a still-overlapping mask instead
    of raising."""
    context = {"working_folder": "/tmp"}
    script = """
import numpy as np

def _volume(name, dim):
    node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', name)
    arr = np.zeros((dim, dim, dim), dtype=np.int16)
    arr[1:4, 1:4, 1:4] = 1
    slicer.util.updateVolumeFromArray(node, arr)
    node.SetOrigin(0.0, 0.0, 0.0)
    return node

s = SlicerHelper(working_folder)
vol_ref = _volume('VolRef', 6)   # source seg is recorded on this 6^3 grid
vol_src = _volume('VolSrc', 8)   # source volume is a different 8^3 grid (dims mismatch, overlapping)

source = s.create_segmentation('MismatchSource')
source.node.SetReferenceImageGeometryParameterFromVolumeNode(vol_ref)
lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', 'SeedLM')
slicer.util.updateVolumeFromArray(lm, (slicer.util.arrayFromVolume(vol_ref) > 0).astype(np.uint8))
lm.SetOrigin(0.0, 0.0, 0.0)
slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(lm, source.node)
slicer.mrmlScene.RemoveNode(lm)

target = s.create_segmentation('MismatchTarget')
s._image_node = vol_src

raised = False
try:
    s.merge_as_pool(source, target)
except Exception as exc:
    raised = 'does not match the volume grid' in str(exc)

bypassed = True
try:
    s.merge_as_pool(source, target, pool_name='_pool_bypass', resample=True)
except Exception:
    bypassed = False

__execResult = {'raised': raised, 'bypassed': bypassed}
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)
    assert result["raised"] is True
    assert result["bypassed"] is True


def _write_canonical_axial_series(dcm_dir: Path) -> None:
    """Write a standard axial (canonical +Z) synthetic DICOM series.

    Mirrors ``tests/test_image.py``'s ``TestDicomVolume`` construction
    (``dicom_dir`` / ``_write_axial_series``): ``ImageOrientationPatient =
    [1, 0, 0, 0, 1, 0]`` (positive-dominant IOP normal, +Z) with ascending
    ``ImagePositionPatient[2]``. ``Image.read_dicom_series`` emits this as a
    canonical right-handed (``det > 0``) volume under the Release-B converter
    epoch (Tasks 7-8). Asymmetric ``Rows``/``Columns`` and ``PixelSpacing`` keep
    the grid non-degenerate, so a SAME classification cannot pass under symmetry.
    """
    dcm_dir.mkdir(parents=True, exist_ok=True)
    suid = pydicom.uid.generate_uid()
    rows, cols = 8, 10
    for i in range(6):
        filename = dcm_dir / f"slice_{i:03d}.dcm"
        file_meta = pydicom.Dataset()
        file_meta.MediaStorageSOPClassUID = pydicom.uid.CTImageStorage
        file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
        file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

        ds = FileDataset(str(filename), {}, file_meta=file_meta, preamble=b"\x00" * 128)
        ds.SeriesInstanceUID = suid
        ds.Rows = rows
        ds.Columns = cols
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelSpacing = [0.7, 0.9]  # asymmetric row/col spacing
        ds.SliceThickness = 2.0
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [0.0, 0.0, float(i * 2)]
        ds.InstanceNumber = i + 1
        ds.PixelData = np.full((rows, cols), 100 + i * 10, dtype=np.uint16).tobytes()
        pydicom.dcmwrite(str(filename), ds)


async def test_fresh_seg_on_canonical_volume_exports_same_without_conform(
    slicer_service: SlicerService,
    slicer_url: str,
) -> None:
    """Release B live gate (probe P6): a fresh segmentation painted on a
    canonical (``det > 0``) converter-emitted volume exports grid-identical
    (SAME) with **no** ``conform_to`` step.

    Exercises the whole Release-B chain end-to-end: a synthetic standard axial
    DICOM series -> ``Image.read_dicom_series`` (the Tasks 7-8 canonical
    converter, now emitting ``det > 0``) -> ``save_as`` NIfTI -> live Slicer
    load -> fresh paint -> plain export. Because the emitted volume is
    right-handed, Slicer 5.10 loads it byte-faithful (no ITK slice-axis flip,
    P1), so the fresh segmentation's grid already coincides with the on-disk
    volume grid -- the plain export is SAME with no conform round-trip (P6).
    This is the no-conform counterpart to the Release-A ``conform_to`` gate
    (``test_export_segmentation_conform_to_roundtrip``), whose left-handed
    volume needs the conform step to reach SAME.

    The gate fails if EITHER half regresses: a converter that stops emitting
    ``det > 0`` (Slicer would flip it at load -> the fresh export lands
    REARRANGED vs the on-disk volume), OR a Slicer/sitk that starts
    canonicalizing ``det > 0`` volumes at load (loaded grid != on-disk grid ->
    REARRANGED). Either way the plain export would no longer classify SAME.
    """
    base = Path("/tmp/t9_canon_gate")
    shutil.rmtree(base, ignore_errors=True)
    dcm_dir = base / "axial_series"
    _write_canonical_axial_series(dcm_dir)

    # Convert via the canonical converter; the emitted grid must be right-handed.
    img = Image()
    img.read_dicom_series(dcm_dir)
    emitted_det = float(np.linalg.det(np.asarray(img.direction)))
    assert emitted_det > 0, f"converter emitted a non-canonical grid: det={emitted_det}"

    vol_path = base / "volume.nii.gz"
    img.save_as(vol_path, FileType.NIFTI)

    context = {"working_folder": str(base), "vol_path": str(vol_path)}
    script = """
import os
import numpy as np
import SimpleITK as sitk

# 1. Load the canonical (det>0) converter-emitted volume. Slicer 5.10 loads a
#    right-handed grid byte-faithful (P1) -- no slice-axis flip at load time.
s = SlicerHelper(working_folder)
loaded = s.load_volume(vol_path, window=(-100, 300))

# The on-disk volume grid, read faithfully -- never via loadVolume (pitfall 7).
vol_grid = _read_grid_on_disk(vol_path)
vol_det = float(np.linalg.det(vol_grid.direction))

# 2. Fresh segmentation on the loaded volume; paint one interior marker block.
seg = s.create_segmentation('CanonFresh')
seg.add_segment('Marker', (1.0, 0.0, 0.0))
seg.node.SetReferenceImageGeometryParameterFromVolumeNode(loaded)
sid = seg.node.GetSegmentation().GetNthSegmentID(0)

arr = slicer.util.arrayFromVolume(loaded)   # (z, y, x)
mask = np.zeros(arr.shape, dtype=np.uint8)
zc, yc, xc = [d // 2 for d in arr.shape]
mask[max(0, zc - 1):zc + 2, max(0, yc - 1):yc + 2, max(0, xc - 1):xc + 2] = 1
painted_voxels = int(mask.sum())
slicer.util.updateSegmentBinaryLabelmapFromArray(mask, seg.node, sid, loaded)

# 3. Export WITHOUT conform_to -- the whole point of the gate.
export_path = os.path.join(working_folder, 'fresh_export.seg.nrrd')
if os.path.isfile(export_path):
    os.remove(export_path)
export_segmentation('CanonFresh', export_path)

# 4. Classify the exported grid against the on-disk volume grid.
exported_grid = _read_grid_on_disk(export_path)
exported_det = float(np.linalg.det(exported_grid.direction))
relation = grid_relation(exported_grid, vol_grid).kind.value

# Non-vacuous: the painted marker actually reached the exported file (a grid
# relation compares geometry only -- an empty seg could pass SAME vacuously).
exp_arr = sitk.GetArrayFromImage(sitk.ReadImage(export_path))
exported_fg = int((exp_arr != 0).sum())

__execResult = {
    'vol_det': vol_det,
    'exported_det': exported_det,
    'relation': relation,
    'painted_voxels': painted_voxels,
    'exported_fg': exported_fg,
    'exported_written': os.path.isfile(export_path),
}
"""
    result = await slicer_service.execute(
        slicer_url, script, context=context, include_correspondence=True
    )

    assert isinstance(result, dict)
    assert result.get("exported_written") is True, result
    # Canonical on disk AND after export -- neither the converter nor Slicer flipped it.
    assert result["vol_det"] > 0, result
    assert result["exported_det"] > 0, result
    # Non-vacuous: the painted marker survived verbatim to the exported file.
    assert result["painted_voxels"] > 0, result
    assert result["exported_fg"] == result["painted_voxels"], result
    # THE GATE: a fresh seg on a canonical volume is grid-identical with NO conform_to.
    assert result["relation"] == "same", result
