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
    """nir_liver second_review flow: a subtract empties the source, set-ops tolerate it.

    Reproduces the regression: when the doctor covers every projected lesion,
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
doctor = _seg_with_blob('Doctor', np.s_[8:17, 8:17, 8:17])

# Doctor fully covers the projected ROI → ROI-level subtract empties the result.
missed = s.subtract_segmentations(projection, doctor, output_name='_Missed')
missed_segments = missed.GetSegmentation().GetNumberOfSegments()

classification = s.create_segmentation('Classification').add_segment('mts', (1, 0, 0))

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
    result = await slicer_service.execute(slicer_url, script, context=context)
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
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)
    assert result["raised"] is True
    assert result["bypassed"] is True
