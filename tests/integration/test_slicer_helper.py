"""Integration tests for SlicerHelper DSL — require a running 3D Slicer instance.

These tests send the helper DSL + user code to a real Slicer and verify
the workspace is set up correctly.
"""

from typing import ClassVar

import pytest

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
