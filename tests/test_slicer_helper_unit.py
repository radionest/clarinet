"""Pure-Python unit tests for SlicerHelper module-level set-op guards.

No running 3D Slicer required: helper.py imports under the _Dummy fallback, and
the guards' branching never touches slicer.util except on the happy path (which
is monkeypatched). ``_segmentation_has_voxels`` is monkeypatched where its real
body would need Slicer's VTK bindings. Full set-op behaviour on real grids —
empty source tolerated vs flipped/foreign grid raising — is covered by the
Slicer-gated integration tests in tests/integration/test_slicer_helper.py.
"""

from unittest.mock import MagicMock

import pytest

import clarinet.services.slicer.helper as helper_mod
from clarinet.services.slicer.helper import (
    SlicerHelperError,
    _labelmap_array_or_raise,
    _segmentation_has_voxels,
)


def _labelmap(*, scalars: object | None) -> MagicMock:
    """Labelmap mock whose GetImageData().GetPointData().GetScalars() == scalars."""
    node = MagicMock()
    image = MagicMock()
    node.GetImageData.return_value = image
    image.GetPointData.return_value.GetScalars.return_value = scalars
    return node


# --- _segmentation_has_voxels ------------------------------------------------


def test_segmentation_has_voxels_all_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every segment empty → False (genuinely empty, set-ops tolerate)."""
    monkeypatch.setattr(helper_mod, "is_segment_empty", lambda node, sid: True)
    seg_node = MagicMock()
    seg_node.GetSegmentation.return_value.GetNumberOfSegments.return_value = 2
    assert _segmentation_has_voxels(seg_node) is False


def test_segmentation_has_voxels_some_filled(monkeypatch: pytest.MonkeyPatch) -> None:
    """One non-empty segment → True (foreign-grid signal once the export is empty)."""
    monkeypatch.setattr(helper_mod, "is_segment_empty", lambda node, sid: sid == "seg0")
    seg_node = MagicMock()
    vtk_seg = seg_node.GetSegmentation.return_value
    vtk_seg.GetNumberOfSegments.return_value = 2
    vtk_seg.GetNthSegmentID.side_effect = ["seg0", "seg1"]
    assert _segmentation_has_voxels(seg_node) is True


def test_segmentation_has_voxels_no_segments() -> None:
    """No segments at all → False."""
    seg_node = MagicMock()
    seg_node.GetSegmentation.return_value.GetNumberOfSegments.return_value = 0
    assert _segmentation_has_voxels(seg_node) is False


# --- _labelmap_array_or_raise ------------------------------------------------


def test_labelmap_array_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scalars present → returns arrayFromVolume(node); source is never inspected."""
    sentinel = object()
    fake_util = MagicMock()
    fake_util.arrayFromVolume.return_value = sentinel
    monkeypatch.setattr(helper_mod.slicer, "util", fake_util)
    guard = MagicMock()
    monkeypatch.setattr(helper_mod, "_segmentation_has_voxels", guard)

    node = _labelmap(scalars=MagicMock())
    assert _labelmap_array_or_raise(node, MagicMock(), what="x") is sentinel
    fake_util.arrayFromVolume.assert_called_once_with(node)
    guard.assert_not_called()


def test_labelmap_array_empty_foreign_grid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty export + source carries voxels → SlicerHelperError (flipped/foreign grid)."""
    monkeypatch.setattr(helper_mod, "_segmentation_has_voxels", lambda node: True)
    node = _labelmap(scalars=None)
    with pytest.raises(SlicerHelperError, match="flipped/foreign grid"):
        _labelmap_array_or_raise(node, MagicMock(), what="the base segmentation (seg_a)")


def test_labelmap_array_empty_source_tolerated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty export + genuinely empty source → returns None and warns (no raise)."""
    monkeypatch.setattr(helper_mod, "_segmentation_has_voxels", lambda node: False)
    node = _labelmap(scalars=None)
    result = _labelmap_array_or_raise(node, MagicMock(), what="the pool source segmentation")
    assert result is None
    assert "WARNING" in capsys.readouterr().out


def test_labelmap_array_no_image_data_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """GetImageData() is None → empty export, classified by the source (no crash)."""
    monkeypatch.setattr(helper_mod, "_segmentation_has_voxels", lambda node: True)
    node = MagicMock()
    node.GetImageData.return_value = None
    with pytest.raises(SlicerHelperError, match="flipped/foreign grid"):
        _labelmap_array_or_raise(node, MagicMock(), what="seg")


def test_labelmap_array_none_point_data_no_attributeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #2: image present but GetPointData() is None must not raise AttributeError."""
    monkeypatch.setattr(helper_mod, "_segmentation_has_voxels", lambda node: False)
    node = MagicMock()
    image = MagicMock()
    node.GetImageData.return_value = image
    image.GetPointData.return_value = None
    # Genuinely empty → tolerated (None), and crucially NOT an AttributeError.
    assert _labelmap_array_or_raise(node, MagicMock(), what="seg") is None
