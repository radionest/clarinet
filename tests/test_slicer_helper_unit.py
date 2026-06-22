"""Pure-Python unit tests for SlicerHelper module-level guards.

No running 3D Slicer required: helper.py imports under the _Dummy fallback, and
the guard's raise path never touches slicer.util. The happy path is monkeypatched.
Full set-op behaviour is covered by the Slicer-gated integration tests in
tests/integration/test_slicer_helper.py.
"""

from unittest.mock import MagicMock

import pytest

from clarinet.services.slicer.helper import (
    SlicerHelperError,
    _labelmap_array_or_raise,
)


def test_labelmap_array_or_raise_no_image_data() -> None:
    """GetImageData() is None → SlicerHelperError, slicer.util never touched."""
    node = MagicMock()
    node.GetImageData.return_value = None
    with pytest.raises(SlicerHelperError, match="empty labelmap"):
        _labelmap_array_or_raise(node, what="the base segmentation (seg_a)")


def test_labelmap_array_or_raise_no_scalars() -> None:
    """ImageData present but no scalars → error pointing at conform_seg_to_grid."""
    node = MagicMock()
    image = MagicMock()
    node.GetImageData.return_value = image
    image.GetPointData.return_value.GetScalars.return_value = None
    with pytest.raises(SlicerHelperError, match="conform_seg_to_grid"):
        _labelmap_array_or_raise(node, what="the pool source segmentation")


def test_labelmap_array_or_raise_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scalars present → returns slicer.util.arrayFromVolume(node) unchanged."""
    import clarinet.services.slicer.helper as helper_mod

    node = MagicMock()
    image = MagicMock()
    node.GetImageData.return_value = image
    image.GetPointData.return_value.GetScalars.return_value = MagicMock()

    sentinel = object()
    fake_util = MagicMock()
    fake_util.arrayFromVolume.return_value = sentinel
    monkeypatch.setattr(helper_mod.slicer, "util", fake_util)

    assert _labelmap_array_or_raise(node, what="x") is sentinel
    fake_util.arrayFromVolume.assert_called_once_with(node)
