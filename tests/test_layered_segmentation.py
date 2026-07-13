"""Tests for clarinet.services.image.LayeredSegmentation — 4-D overlapping NRRD I/O."""

from pathlib import Path

import nrrd
import numpy as np
import pytest

from clarinet.exceptions.domain import ImageError
from clarinet.services.image import LayeredSegmentation


def _overlapping_layers() -> tuple[np.ndarray, np.ndarray]:
    """psoas ⊆ skeletal_muscle — a shared voxel is nonzero in both layers."""
    shape = (8, 8, 6)
    psoas = np.zeros(shape, dtype=np.uint8)
    psoas[2:5, 2:5, 1:4] = 1
    skm = np.zeros(shape, dtype=np.uint8)
    skm[1:6, 1:6, 1:5] = 1  # strict superset of psoas
    return psoas, skm


class TestLayeredSegmentationWrite:
    def test_from_layers_save_header(self, tmp_path: Path) -> None:
        psoas, skm = _overlapping_layers()
        lseg = LayeredSegmentation.from_layers(
            [("psoas", psoas), ("skeletal_muscle", skm)],
            spacing=(1.0, 1.0, 2.0),
            origin=(0.0, 0.0, 0.0),
            direction=np.eye(3),
        )
        out = lseg.save(tmp_path / "rois.seg.nrrd")
        assert out.is_file()

        header = nrrd.read_header(str(out))
        assert int(header["dimension"]) == 4
        assert list(header["kinds"]) == ["list", "domain", "domain", "domain"]
        assert header["encoding"] == "raw"
        assert int(header["sizes"][0]) == 2  # layer axis slowest (first)
        assert tuple(int(s) for s in header["sizes"][1:]) == (8, 8, 6)
        assert np.all(np.isnan(np.asarray(header["space directions"][0], dtype=float)))
        assert header["Segment0_Name"] == "psoas"
        assert header["Segment1_Name"] == "skeletal_muscle"
        assert header["Segment0_Layer"] == "0"
        assert header["Segment1_Layer"] == "1"
        assert header["Segment0_LabelValue"] == "1"

    def test_save_fills_in_place_releases_sources(self, tmp_path: Path) -> None:
        psoas, skm = _overlapping_layers()
        lseg = LayeredSegmentation.from_layers(
            [("a", psoas), ("b", skm)],
            spacing=(1.0, 1.0, 1.0),
            origin=(0.0, 0.0, 0.0),
            direction=np.eye(3),
        )
        lseg.save(tmp_path / "x.seg.nrrd")
        assert all(arr is None for arr in lseg._layer_arrays)  # fill-in-place freed each source

    def test_from_layers_shape_mismatch_raises(self) -> None:
        a = np.zeros((4, 4, 4), dtype=np.uint8)
        b = np.zeros((4, 4, 5), dtype=np.uint8)
        with pytest.raises(ImageError, match="shape"):
            LayeredSegmentation.from_layers(
                [("a", a), ("b", b)],
                spacing=(1.0, 1.0, 1.0),
                origin=(0.0, 0.0, 0.0),
                direction=np.eye(3),
            )

    def test_from_layers_empty_raises(self) -> None:
        with pytest.raises(ImageError, match="at least one layer"):
            LayeredSegmentation.from_layers(
                [], spacing=(1.0, 1.0, 1.0), origin=(0.0, 0.0, 0.0), direction=np.eye(3)
            )

    def test_read_header_recovers_grid(self, tmp_path: Path) -> None:
        psoas, skm = _overlapping_layers()
        LayeredSegmentation.from_layers(
            [("psoas", psoas), ("skeletal_muscle", skm)],
            spacing=(0.9, 0.9, 2.5),
            origin=(10.0, 20.0, 30.0),
            direction=np.eye(3),
        ).save(tmp_path / "rois.seg.nrrd")

        hdr = LayeredSegmentation.read_header(tmp_path / "rois.seg.nrrd")
        assert hdr.shape == (8, 8, 6)
        assert pytest.approx(hdr.spacing, abs=1e-4) == (0.9, 0.9, 2.5)
        assert pytest.approx(hdr.origin, abs=1e-4) == (10.0, 20.0, 30.0)
        assert {name for name, _layer, _label in hdr.segments} == {"psoas", "skeletal_muscle"}

    def test_read_header_recovers_rotated_direction(self, tmp_path: Path) -> None:
        """Non-identity, non-transpose-symmetric direction (90 deg about Z).

        ``np.eye(3)`` is transpose-symmetric, so every other grid test would pass even
        with a ``.T`` bug in the direction round-trip. This direction's transpose is a
        different (the -90 deg) rotation, so a transpose bug would surface here.
        """
        psoas, skm = _overlapping_layers()
        direction = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        LayeredSegmentation.from_layers(
            [("psoas", psoas), ("skeletal_muscle", skm)],
            spacing=(0.9, 0.9, 2.5),
            origin=(10.0, 20.0, 30.0),
            direction=direction,
        ).save(tmp_path / "rot.seg.nrrd")

        hdr = LayeredSegmentation.read_header(tmp_path / "rot.seg.nrrd")
        np.testing.assert_array_almost_equal(hdr.direction, direction, decimal=5)
        assert pytest.approx(hdr.spacing, abs=1e-4) == (0.9, 0.9, 2.5)
        assert pytest.approx(hdr.origin, abs=1e-4) == (10.0, 20.0, 30.0)
        assert hdr.shape == (8, 8, 6)

        # voxel path unaffected by the rotated header
        ps = hdr.read_layer(tmp_path / "rot.seg.nrrd", "psoas")
        np.testing.assert_array_equal(ps, psoas)


class TestLayeredSegmentationRead:
    def test_round_trip_preserves_overlap(self, tmp_path: Path) -> None:
        psoas, skm = _overlapping_layers()
        LayeredSegmentation.from_layers(
            [("psoas", psoas), ("skeletal_muscle", skm)],
            spacing=(1.0, 1.0, 1.0),
            origin=(0.0, 0.0, 0.0),
            direction=np.eye(3),
        ).save(tmp_path / "rois.seg.nrrd")

        hdr = LayeredSegmentation.read_header(tmp_path / "rois.seg.nrrd")
        ps = hdr.read_layer(tmp_path / "rois.seg.nrrd", "psoas")
        sk = hdr.read_layer(tmp_path / "rois.seg.nrrd", "skeletal_muscle")
        np.testing.assert_array_equal(ps, psoas)
        np.testing.assert_array_equal(sk, skm)
        # a shared voxel is nonzero in both layers — overlap preserved
        assert ps[3, 3, 2] == 1 and sk[3, 3, 2] == 1

    def test_read_layer_by_index(self, tmp_path: Path) -> None:
        psoas, skm = _overlapping_layers()
        LayeredSegmentation.from_layers(
            [("psoas", psoas), ("skeletal_muscle", skm)],
            spacing=(1.0, 1.0, 1.0),
            origin=(0.0, 0.0, 0.0),
            direction=np.eye(3),
        ).save(tmp_path / "rois.seg.nrrd")
        hdr = LayeredSegmentation.read_header(tmp_path / "rois.seg.nrrd")
        np.testing.assert_array_equal(hdr.read_layer(tmp_path / "rois.seg.nrrd", 1), skm)

    def test_read_layer_slice(self, tmp_path: Path) -> None:
        psoas, skm = _overlapping_layers()
        LayeredSegmentation.from_layers(
            [("psoas", psoas), ("skeletal_muscle", skm)],
            spacing=(1.0, 1.0, 1.0),
            origin=(0.0, 0.0, 0.0),
            direction=np.eye(3),
        ).save(tmp_path / "rois.seg.nrrd")
        hdr = LayeredSegmentation.read_header(tmp_path / "rois.seg.nrrd")
        sl = hdr.read_layer_slice(tmp_path / "rois.seg.nrrd", "psoas", 2, axis=2)
        np.testing.assert_array_equal(sl, psoas[:, :, 2])

    def test_iter_layers(self, tmp_path: Path) -> None:
        psoas, skm = _overlapping_layers()
        LayeredSegmentation.from_layers(
            [("psoas", psoas), ("skeletal_muscle", skm)],
            spacing=(1.0, 1.0, 1.0),
            origin=(0.0, 0.0, 0.0),
            direction=np.eye(3),
        ).save(tmp_path / "rois.seg.nrrd")
        hdr = LayeredSegmentation.read_header(tmp_path / "rois.seg.nrrd")
        got = dict(hdr.iter_layers(tmp_path / "rois.seg.nrrd"))
        assert set(got) == {"psoas", "skeletal_muscle"}
        np.testing.assert_array_equal(got["psoas"], psoas)

    def test_read_layer_unknown_name_raises(self, tmp_path: Path) -> None:
        psoas, skm = _overlapping_layers()
        LayeredSegmentation.from_layers(
            [("psoas", psoas), ("skeletal_muscle", skm)],
            spacing=(1.0, 1.0, 1.0),
            origin=(0.0, 0.0, 0.0),
            direction=np.eye(3),
        ).save(tmp_path / "rois.seg.nrrd")
        hdr = LayeredSegmentation.read_header(tmp_path / "rois.seg.nrrd")
        with pytest.raises(ImageError, match="no segment named"):
            hdr.read_layer(tmp_path / "rois.seg.nrrd", "nonexistent")
