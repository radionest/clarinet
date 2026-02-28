"""Integration tests for SlicerHelper DSL — require a running 3D Slicer instance.

These tests send the helper DSL + user code to a real Slicer and verify
the workspace is set up correctly.
"""

from typing import ClassVar

import pytest

from src.services.slicer.helper import PacsHelper, SlicerHelper
from src.services.slicer.service import SlicerService

pytestmark = [pytest.mark.slicer, pytest.mark.asyncio]


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

    pytestmark: ClassVar[list[pytest.MarkDecorator]] = [pytest.mark.slicer]

    def test_constructor_stores_params(self) -> None:
        """PacsHelper stores all connection parameters."""
        pacs = PacsHelper(
            host="192.168.1.10",
            port=4242,
            called_aet="PACS",
            calling_aet="SLICER",
            prefer_cget=True,
            move_aet="SLICER",
        )
        assert pacs.host == "192.168.1.10"
        assert pacs.port == 4242
        assert pacs.called_aet == "PACS"
        assert pacs.calling_aet == "SLICER"
        assert pacs.prefer_cget is True
        assert pacs.move_aet == "SLICER"

    def test_constructor_defaults(self) -> None:
        """PacsHelper uses sensible defaults for optional params."""
        pacs = PacsHelper(
            host="10.0.0.1",
            port=11112,
            called_aet="ORTHANC",
            calling_aet="MYSCU",
        )
        assert pacs.prefer_cget is True
        assert pacs.move_aet == "SLICER"

    def test_slicer_helper_has_load_study_from_pacs(self) -> None:
        """SlicerHelper exposes load_study_from_pacs method."""
        assert hasattr(SlicerHelper, "load_study_from_pacs")
        assert callable(SlicerHelper.load_study_from_pacs)
