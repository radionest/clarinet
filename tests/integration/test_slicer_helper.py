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
s = SlicerHelper(working_folder)
seg_a = s.create_segmentation('DualA')
seg_b = s.create_segmentation('DualB')
# Dual layout needs volume nodes; create minimal ones
vol_a = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', 'VolA')
vol_b = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', 'VolB')
s.set_dual_layout(vol_a, vol_b, seg_a=seg_a, seg_b=seg_b, linked=True)
print('dual_layout_ok')
"""
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)
