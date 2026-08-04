"""Pure-Python unit tests for SlicerHelper module-level set-op guards.

No running 3D Slicer required: helper.py imports under the _Dummy fallback, and
the guards' branching never touches slicer.util except on the happy path (which
is monkeypatched). ``_segmentation_has_voxels`` is monkeypatched where its real
body would need Slicer's VTK bindings. Full set-op behaviour on real grids —
empty source tolerated vs flipped/foreign grid raising — is covered by the
Slicer-gated integration tests in tests/integration/test_slicer_helper.py.
"""

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

import clarinet.services.slicer.helper as helper_mod
from clarinet.services.image.grid import Grid, RelationKind, grid_relation
from clarinet.services.slicer.helper import (
    SlicerHelperError,
    _labelmap_array_or_raise,
    _missing_voxel_segments,
    _segmentation_has_voxels,
    _SegmentEditMixin,
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


# --- _export_segments_labelmap: resample= gate (issue #415) ------------------
#
# The resample= parameter on the set-op choke point gates the pre-regrid
# ``_assert_segmentation_matches_volume`` check. ``test_labelmap_array_*`` above
# covers the post-export empty/foreign-grid discrimination; these cover the
# other half of #415 — that ``resample=True`` opts out of the geometry guard
# (legacy re-grid path) while ``resample=False`` (default) invokes it. The full
# happy-path export touches too much of Slicer's VTK bindings to mock here; we
# stub everything past the gate so only the gate's own branching is exercised.


def _wire_labelmap_export_gate(
    monkeypatch: pytest.MonkeyPatch, *, has_voxels: bool
) -> tuple[Any, MagicMock]:
    """Build a minimal ``_SegmentEditMixin`` + mocks around the resample gate.

    Returns ``(helper, assert_grid_mock)`` so the test asserts the geometry
    guard's call count via the returned mock. Everything the gated block runs
    after the check -- ``_apply_reference_geometry``, ``slicer.modules`` /
    ``slicer.mrmlScene`` access, ``_labelmap_array_or_raise`` -- is stubbed so
    only the gate's branching is observable.
    """
    helper = object.__new__(_SegmentEditMixin)  # skip __init__ (Slicer scene clear)
    helper._image_node = MagicMock(name="volume_node")

    monkeypatch.setattr(helper_mod, "_segmentation_has_voxels", lambda node: has_voxels)
    assert_grid = MagicMock(name="_assert_segmentation_matches_volume")
    monkeypatch.setattr(helper_mod, "_assert_segmentation_matches_volume", assert_grid)
    monkeypatch.setattr(helper, "_apply_reference_geometry", MagicMock())

    fake_seg_logic = MagicMock()
    fake_modules = MagicMock()
    fake_modules.segmentations.logic.return_value = fake_seg_logic
    fake_scene = MagicMock()
    fake_scene.AddNewNodeByClass.return_value = MagicMock(name="labelmap_node")
    monkeypatch.setattr(helper_mod.slicer, "modules", fake_modules)
    monkeypatch.setattr(helper_mod.slicer, "mrmlScene", fake_scene)
    # Short-circuit the empty/foreign classification -- covered above.
    monkeypatch.setattr(helper_mod, "_labelmap_array_or_raise", lambda lm, node, what="": object())

    return helper, assert_grid


def test_export_segments_labelmap_resample_false_invokes_geometry_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default resample=False + source carries voxels → geometry guard runs."""
    helper, assert_grid = _wire_labelmap_export_gate(monkeypatch, has_voxels=True)
    helper._export_segments_labelmap(MagicMock(), "_t", what="seg", resample=False)
    assert_grid.assert_called_once()


def test_export_segments_labelmap_resample_true_skips_geometry_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resample=True opts out of the geometry guard (legacy re-grid path)."""
    helper, assert_grid = _wire_labelmap_export_gate(monkeypatch, has_voxels=True)
    helper._export_segments_labelmap(MagicMock(), "_t", what="seg", resample=True)
    assert_grid.assert_not_called()


def test_export_segments_labelmap_empty_source_skips_geometry_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genuinely-empty source never triggers the geometry guard regardless of resample."""
    helper, assert_grid = _wire_labelmap_export_gate(monkeypatch, has_voxels=False)
    helper._export_segments_labelmap(MagicMock(), "_t", what="seg", resample=False)
    assert_grid.assert_not_called()


# --- export_segmentation: conform_to guard/gate/fail-closed contract ---------
#
# Task 5 (canonicalize-segmentation-grids): only the contract is unit-tested
# here (bundle-absent guard, missing-reference-file raise, unchanged plain
# export, the reference_volume= removal). The SAME/REARRANGED/FOREIGN
# classification and the re-grid mechanics themselves touch the live Slicer
# API (ImportLabelmapToSegmentationNode, addVolumeFromArray, ...) and are
# exercised end-to-end by the live-Slicer test in Task 6.
#
# The post-write-reread-raises fail-closed branch (review follow-up on Task 5)
# is the one exception: it uses the real Grid/grid_relation (pure numpy/stdlib)
# to get a genuine pre-write SAME verdict cheaply, mocking only the
# Slicer/VTK-touching pieces (_node_binary_labelmap_grid, slicer.util).


def test_export_segmentation_conform_to_without_bundle_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conform_to set but the correspondence bundle was never injected → raise."""
    fake_util = MagicMock()
    fake_util.getNode.return_value = MagicMock()
    monkeypatch.setattr(helper_mod.slicer, "util", fake_util)

    with pytest.raises(SlicerHelperError, match="correspondence bundle"):
        helper_mod.export_segmentation(
            "Segmentation", "/tmp/out.seg.nrrd", conform_to="/tmp/ref.nii.gz"
        )


def test_export_segmentation_conform_to_missing_file_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bundle present but conform_to names an unreadable path → raise, not a crash."""
    # Grid/grid_relation are TYPE_CHECKING-only names -- never real module
    # attributes outside the exec'd-in-Slicer bundle, hence raising=False.
    monkeypatch.setattr(helper_mod, "Grid", object(), raising=False)
    monkeypatch.setattr(helper_mod, "grid_relation", object(), raising=False)
    fake_util = MagicMock()
    fake_util.getNode.return_value = MagicMock()
    monkeypatch.setattr(helper_mod.slicer, "util", fake_util)

    missing = str(tmp_path / "does_not_exist.nii.gz")
    with pytest.raises(SlicerHelperError):
        helper_mod.export_segmentation(
            "Segmentation", str(tmp_path / "out.seg.nrrd"), conform_to=missing
        )


def test_export_segmentation_conform_to_post_write_reread_raises_deletes_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Post-write re-read raising (corrupt/unreadable write) still deletes the artifact.

    Fail-closed completeness gap: the pre-fix code only deleted the output file
    when the post-write re-classification *returned* a non-SAME verdict. If
    ``_read_grid_on_disk`` (or ``grid_relation``) *raised* instead, the bad file
    was left on disk. Uses the real ``Grid``/``grid_relation`` (pure numpy/stdlib,
    no Slicer/VTK needed) so the pre-write SAME classification is genuine; only
    disk IO (``_read_grid_on_disk``) and node inspection
    (``_node_binary_labelmap_grid``) are faked.
    """
    monkeypatch.setattr(helper_mod, "Grid", Grid, raising=False)
    monkeypatch.setattr(helper_mod, "RelationKind", RelationKind, raising=False)
    monkeypatch.setattr(helper_mod, "grid_relation", grid_relation, raising=False)

    same_grid = Grid(shape=(2, 2, 2), affine=np.eye(4))
    monkeypatch.setattr(helper_mod, "_node_binary_labelmap_grid", lambda node: same_grid)

    ref_path = str(tmp_path / "ref.nii.gz")
    output_path = str(tmp_path / "out.seg.nrrd")

    def _fake_read_grid_on_disk(path: str) -> Grid:
        if path == output_path:
            raise SlicerHelperError("Cannot read grid from post-write file: corrupt header")
        return same_grid

    monkeypatch.setattr(helper_mod, "_read_grid_on_disk", _fake_read_grid_on_disk)

    fake_util = MagicMock()
    fake_util.getNode.return_value = MagicMock()

    def _fake_export(node: object, path: str) -> None:
        with open(path, "w"):
            pass

    fake_util.exportNode.side_effect = _fake_export
    monkeypatch.setattr(helper_mod.slicer, "util", fake_util)

    with pytest.raises(SlicerHelperError, match="post-write verification failed"):
        helper_mod.export_segmentation("Segmentation", output_path, conform_to=ref_path)

    assert not os.path.isfile(output_path)


def _conform_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[str, str]:
    """Wire a conform_to export whose grid checks all pass, so only the roster guard bites.

    Same shape as the post-write test above -- real Grid/grid_relation for a
    genuine SAME verdict, faked disk IO and node inspection -- but the written
    file re-reads as SAME too, leaving the per-segment voxel guard as the only
    branch that can fail.
    """
    monkeypatch.setattr(helper_mod, "Grid", Grid, raising=False)
    monkeypatch.setattr(helper_mod, "RelationKind", RelationKind, raising=False)
    monkeypatch.setattr(helper_mod, "grid_relation", grid_relation, raising=False)

    same_grid = Grid(shape=(2, 2, 2), affine=np.eye(4))
    monkeypatch.setattr(helper_mod, "_node_binary_labelmap_grid", lambda node: same_grid)
    monkeypatch.setattr(helper_mod, "_read_grid_on_disk", lambda path: same_grid)

    fake_util = MagicMock()
    fake_util.getNode.return_value = MagicMock()

    def _fake_export(node: object, path: str) -> None:
        with open(path, "w"):
            pass

    fake_util.exportNode.side_effect = _fake_export
    monkeypatch.setattr(helper_mod.slicer, "util", fake_util)

    return str(tmp_path / "ref.nii.gz"), str(tmp_path / "out.seg.nrrd")


def test_export_segmentation_per_segment_voxel_loss_deletes_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One segment losing its voxels fails closed, names it, and deletes the artifact.

    The pre-fix guard only fired when the whole export came back voxel-less, so a
    mixed-representation source that lost just its shared-layer segments wrote a
    silently incomplete file (issue #500).
    """
    ref_path, output_path = _conform_harness(monkeypatch, tmp_path)
    monkeypatch.setattr(helper_mod, "_voxeled_segment_roster", lambda node: {"Alpha": 1, "Beta": 1})
    monkeypatch.setattr(helper_mod, "_written_voxel_roster", lambda path: {"Alpha": 1})

    with pytest.raises(SlicerHelperError, match=r"lost the voxels of segment.*Beta"):
        helper_mod.export_segmentation("Segmentation", output_path, conform_to=ref_path)

    assert not os.path.isfile(output_path)


def test_export_segmentation_intact_roster_keeps_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every source segment present in the written file → no raise, artifact survives."""
    ref_path, output_path = _conform_harness(monkeypatch, tmp_path)
    roster = {"Alpha": 1, "Beta": 1}
    monkeypatch.setattr(helper_mod, "_voxeled_segment_roster", lambda node: dict(roster))
    monkeypatch.setattr(helper_mod, "_written_voxel_roster", lambda path: dict(roster))

    assert (
        helper_mod.export_segmentation("Segmentation", output_path, conform_to=ref_path)
        == output_path
    )
    assert os.path.isfile(output_path)


def test_export_segmentation_plain_export_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No conform_to → today's behavior: export as-is, no grid classification."""
    fake_util = MagicMock()
    fake_util.getNode.return_value = MagicMock()

    def _fake_export(node: object, path: str) -> None:
        with open(path, "w"):
            pass

    fake_util.exportNode.side_effect = _fake_export
    monkeypatch.setattr(helper_mod.slicer, "util", fake_util)

    output_path = str(tmp_path / "sub" / "out.seg.nrrd")
    result = helper_mod.export_segmentation("Segmentation", output_path)

    assert result == output_path
    assert os.path.isfile(output_path)
    fake_util.exportNode.assert_called_once_with(fake_util.getNode.return_value, output_path)


def test_export_segmentation_reference_volume_kwarg_removed() -> None:
    """reference_volume= no longer exists -- calling with it is a TypeError."""
    with pytest.raises(TypeError):
        helper_mod.export_segmentation(
            "Segmentation", "/tmp/out.seg.nrrd", reference_volume=object()
        )


class TestMissingVoxelSegments:
    def test_all_present(self) -> None:
        assert _missing_voxel_segments({"A": 1, "B": 1}, {"A": 1, "B": 1}) == []

    def test_partial_loss_detected(self) -> None:
        assert _missing_voxel_segments({"A": 1, "B": 1}, {"A": 1}) == ["B"]

    def test_duplicate_names_compared_by_count(self) -> None:
        assert _missing_voxel_segments({"A": 2}, {"A": 1}) == ["A"]
        assert _missing_voxel_segments({"A": 2}, {"A": 2}) == []

    def test_empty_source_never_required(self) -> None:
        # roster builders only include voxeled segments; empty source => empty roster
        assert _missing_voxel_segments({}, {}) == []

    def test_extra_written_segments_ignored(self) -> None:
        assert _missing_voxel_segments({"A": 1}, {"A": 1, "B": 3}) == []
