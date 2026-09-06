"""Contract tests for the public NRRD header resolver (clarinet/services/image/nrrd_space.py)."""

from __future__ import annotations

import inspect
import types
from pathlib import Path

import nrrd
import numpy as np
import pytest

from clarinet.exceptions.domain import ImageReadError
from clarinet.services.image import Image, LayeredSegmentation, grid
from clarinet.services.image.nrrd_space import (
    NrrdGrid,
    canonical_nrrd_space,
    nrrd_grid_from_header,
    nrrd_space_to_lps,
    nrrd_space_transform,
)

_DIRS = np.diag([0.5, 0.6, 0.7])
_ORIGIN = np.array([10.0, 20.0, 30.0])


class TestNrrdGridFromHeader:
    """The one place the `space` rule is applied; exported from the facade."""

    def test_lps_header_passes_through(self) -> None:
        g = nrrd_grid_from_header(
            {"space": "left-posterior-superior", "space directions": _DIRS, "space origin": _ORIGIN}
        )
        assert isinstance(g, NrrdGrid)
        assert g.spacing == pytest.approx((0.5, 0.6, 0.7))
        np.testing.assert_allclose(g.direction, np.eye(3))
        assert g.origin == pytest.approx((10.0, 20.0, 30.0))

    @pytest.mark.parametrize("space", ["RAS", "right-anterior-superior", "Right-Anterior-Superior"])
    def test_ras_header_converts_directions_and_origin(self, space: str) -> None:
        g = nrrd_grid_from_header(
            {"space": space, "space directions": np.eye(3), "space origin": _ORIGIN}
        )
        assert g.spacing == pytest.approx((1.0, 1.0, 1.0))
        np.testing.assert_allclose(g.direction, np.diag([-1.0, -1.0, 1.0]))
        assert g.origin == pytest.approx((-10.0, -20.0, 30.0))

    def test_las_header_flips_only_y(self) -> None:
        g = nrrd_grid_from_header(
            {"space": "LAS", "space directions": np.eye(3), "space origin": _ORIGIN}
        )
        np.testing.assert_allclose(g.direction, np.diag([1.0, -1.0, 1.0]))
        assert g.origin == pytest.approx((10.0, -20.0, 30.0))

    def test_spacings_only_without_space_or_origin_reads_verbatim(self) -> None:
        g = nrrd_grid_from_header({"spacings": [0.5, 0.6, 0.7]})
        assert g.spacing == pytest.approx((0.5, 0.6, 0.7))
        assert g.direction is None
        assert g.origin is None

    def test_spacings_header_in_ras_is_converted(self) -> None:
        g = nrrd_grid_from_header({"space": "RAS", "spacings": [0.5, 0.6, 0.7]})
        assert g.spacing == pytest.approx((0.5, 0.6, 0.7))
        np.testing.assert_allclose(g.direction, np.diag([-1.0, -1.0, 1.0]))
        assert g.origin is None

    def test_four_dimensional_header_uses_the_callers_spatial_rows(self) -> None:
        header = {
            "space": "RAS",
            "space directions": np.vstack([np.full(3, np.nan), np.diag([-0.5, -0.6, 0.7])]),
            "space origin": np.array([-10.0, -20.0, 30.0]),
        }
        g = nrrd_grid_from_header(header, spatial=slice(1, 4))
        assert g.spacing == pytest.approx((0.5, 0.6, 0.7))
        np.testing.assert_allclose(g.direction, np.eye(3))
        assert g.origin == pytest.approx((10.0, 20.0, 30.0))

    def test_geometry_without_space_is_refused_with_repair_hint(self, tmp_path: Path) -> None:
        source = tmp_path / "legacy.nrrd"
        with pytest.raises(ImageReadError, match="declare_nrrd_space") as info:
            nrrd_grid_from_header({"space directions": _DIRS}, source)
        assert str(source) in str(info.value)
        with pytest.raises(ImageReadError, match="declare_nrrd_space"):
            nrrd_grid_from_header({"spacings": [1.0, 1.0, 1.0], "space origin": _ORIGIN})

    def test_unsupported_space_is_refused(self) -> None:
        with pytest.raises(ImageReadError, match="scanner-xyz"):
            nrrd_grid_from_header({"space": "scanner-xyz", "space directions": _DIRS})


class TestSpaceHelpers:
    """Package-internal helpers behind nrrd_grid_from_header."""

    def test_transform_is_the_shared_grid_constant(self) -> None:
        assert nrrd_space_transform("RAS") is grid.LPS_TO_RAS
        assert nrrd_space_transform("las") is grid.LAS_TO_LPS
        with pytest.raises(ValueError, match="read-only"):
            nrrd_space_transform("RAS")[0, 0] = 99.0

    def test_canonical_spellings(self) -> None:
        assert canonical_nrrd_space(" lps ") == "left-posterior-superior"
        assert canonical_nrrd_space("Right-Anterior-Superior") == "right-anterior-superior"
        assert canonical_nrrd_space(None) is None
        assert canonical_nrrd_space("scanner-xyz") is None

    def test_space_to_lps_converts_rows_and_origin(self) -> None:
        dirs, origin = nrrd_space_to_lps(
            "RAS", np.diag([-0.5, -0.6, 0.7]), np.array([-1.0, -2.0, 3.0])
        )
        np.testing.assert_allclose(dirs, np.diag([0.5, 0.6, 0.7]))
        np.testing.assert_allclose(origin, [1.0, 2.0, 3.0])
        _, none_origin = nrrd_space_to_lps("LPS", np.eye(3), None)
        assert none_origin is None


def test_both_readers_share_the_resolver(tmp_path: Path) -> None:
    """A RAS 3-D NRRD and a RAS 4-D layered NRRD with the same geometry land on one LPS grid."""
    layer = np.zeros((4, 5, 6), dtype=np.uint8)
    three_d = tmp_path / "ras.nrrd"
    nrrd.write(
        str(three_d),
        layer,
        {
            "space": "RAS",
            "space directions": np.diag([-0.5, -0.6, 0.7]),
            "space origin": np.array([-10.0, -20.0, 30.0]),
        },
    )
    four_d = tmp_path / "ras.seg.nrrd"
    nrrd.write(
        str(four_d),
        layer[np.newaxis, ...],
        {
            "dimension": 4,
            "space": "RAS",
            "kinds": ["list", "domain", "domain", "domain"],
            "space directions": np.vstack([np.full(3, np.nan), np.diag([-0.5, -0.6, 0.7])]),
            "space origin": np.array([-10.0, -20.0, 30.0]),
            "Segment0_ID": "Segment_0",
            "Segment0_Name": "a",
            "Segment0_LabelValue": "1",
            "Segment0_Layer": "0",
        },
    )

    img = Image()
    img.read(three_d, load_data=False)
    seg = LayeredSegmentation.read_header(four_d)

    assert img.spacing == pytest.approx((0.5, 0.6, 0.7))
    assert seg.spacing == pytest.approx((0.5, 0.6, 0.7))
    assert img.origin == pytest.approx((10.0, 20.0, 30.0))
    assert seg.origin == pytest.approx((10.0, 20.0, 30.0))
    np.testing.assert_allclose(img.direction, np.eye(3), atol=1e-9)
    np.testing.assert_allclose(seg.direction, np.eye(3), atol=1e-9)


class TestModuleBoundaries:
    """Design decisions D2, D4, D6: what is exported, what imports what."""

    def test_facade_exports_the_resolver_and_the_repair_only(self) -> None:
        import clarinet.services.image as facade

        assert facade.nrrd_grid_from_header is nrrd_grid_from_header
        assert facade.NrrdGrid is NrrdGrid
        assert facade.declare_nrrd_space.__module__ == "clarinet.services.image.nrrd_repair"
        assert {"NrrdGrid", "nrrd_grid_from_header", "declare_nrrd_space"} <= set(facade.__all__)
        internal = {"nrrd_space_transform", "nrrd_space_to_lps", "canonical_nrrd_space"}
        assert not internal & set(facade.__all__)

    def test_resolver_module_is_disk_free(self) -> None:
        from clarinet.services.image import nrrd_space

        bound = {v.__name__ for v in vars(nrrd_space).values() if isinstance(v, types.ModuleType)}
        assert "nrrd" not in bound
        assert "logger" not in vars(nrrd_space)

    def test_layered_reader_does_not_import_the_3d_reader(self) -> None:
        from clarinet.services.image import layered_segmentation

        assert "from clarinet.services.image.image import" not in inspect.getsource(
            layered_segmentation
        )

    def test_no_private_frame_constant_copies(self) -> None:
        from clarinet.services.image import image, layered_segmentation, nrrd_repair, nrrd_space

        for mod in (image, layered_segmentation, nrrd_repair, nrrd_space):
            assert "_LPS_TO_RAS" not in vars(mod), mod.__name__
            assert "_LAS_TO_LPS" not in vars(mod), mod.__name__
