"""Contract tests for the public NRRD header resolver (clarinet/services/image/nrrd_space.py)."""

from __future__ import annotations

import ast
import inspect
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

    def test_oblique_ras_directions_negate_world_columns_not_array_rows(self) -> None:
        """Pins the conversion to ``dirs @ transform``, not ``transform @ dirs``.

        Every other `space directions` in this suite is diagonal, and diagonal matrices
        commute — only an oblique, non-symmetric one tells the two orders apart. The
        transform is a signed diagonal, so the row norms (and hence `spacing`) are
        identical either way: `direction` is the assertion that discriminates.
        """
        # NRRD does not require orthogonal rows; Pythagorean triples keep the row norms
        # exact at 5 / 13 / 10 so the expected direction is checkable by hand.
        dirs = np.array([[3.0, 0.0, 4.0], [0.0, 5.0, 12.0], [8.0, 6.0, 0.0]])
        g = nrrd_grid_from_header(
            {
                "space": "RAS",
                "space directions": dirs,
                "space origin": np.array([-10.0, -20.0, 30.0]),
            }
        )
        assert g.spacing == pytest.approx((5.0, 13.0, 10.0))
        assert g.origin == pytest.approx((10.0, 20.0, 30.0))
        # diag(-1, -1, 1) post-multiplied negates the world x/y *columns* of every row:
        # rows become [-3, 0, 4], [0, -5, 12], [-8, -6, 0]. Row-normalize by 5 / 13 / 10,
        # then transpose, so column j of `direction` is array axis j's unit vector.
        expected = np.array(
            [
                [-0.6, 0.0, -0.8],
                [0.0, -5 / 13, -0.6],
                [0.8, 12 / 13, 0.0],
            ]
        )
        np.testing.assert_allclose(g.direction, expected)
        # Guard the fixture itself: pre-multiplying negates array *rows* instead, which
        # must give a different answer or this test proves nothing.
        flipped_rows = grid.LPS_TO_RAS @ dirs
        flipped_rows = (flipped_rows / np.linalg.norm(flipped_rows, axis=1)[:, np.newaxis]).T
        assert not np.allclose(expected, flipped_rows), "fixture is not oblique enough"

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

    def test_out_of_range_spatial_slice_yields_a_plausible_unit_grid(self) -> None:
        """Pins the quietest *spatial* footgun, the one the resolver's Warning block names.

        An empty slice leaves `spacings[spatial]` empty, and an empty list is falsy, so the
        unit-spacing fallback takes over while `keep_spacing` stays True: the result is a
        well-formed (1, 1, 1) grid in the declared space rather than the error the one- and
        two-axis cases raise. Behaviour is frozen by design, so this test exists to keep the
        documented failure modes honest — the docstring claimed an exception here.
        """
        header = {"space": "RAS", "spacings": [0.5, 0.6, 0.7]}
        g = nrrd_grid_from_header(header, spatial=slice(3, 6))
        assert g.spacing == pytest.approx((1.0, 1.0, 1.0))
        np.testing.assert_allclose(g.direction, np.diag([-1.0, -1.0, 1.0]))
        # A partial slice does raise, which is what makes the empty case worth pinning.
        with pytest.raises((IndexError, ValueError)):
            nrrd_grid_from_header(header, spatial=slice(0, 2))

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
    """What the facade exports, which module owns the resolver, and which modules may import which."""

    def test_facade_exports_the_resolver_and_the_repair_only(self) -> None:
        import clarinet.services.image as facade
        from clarinet.services.image.nrrd_repair import declare_nrrd_space

        assert facade.nrrd_grid_from_header is nrrd_grid_from_header
        assert facade.NrrdGrid is NrrdGrid
        assert facade.declare_nrrd_space is declare_nrrd_space
        assert {"NrrdGrid", "nrrd_grid_from_header", "declare_nrrd_space"} <= set(facade.__all__)
        internal = {"nrrd_space_transform", "nrrd_space_to_lps", "canonical_nrrd_space"}
        assert not internal & set(facade.__all__)

    def test_resolver_module_imports_are_limited(self) -> None:
        """nrrd_space.py stays a pure header resolver: numpy + stdlib + two clarinet leaves.

        An AST scan over the source, not the module globals: `from nrrd import read_header`
        binds a function rather than a module, `import nrrd.reader as r` binds a module named
        `nrrd.reader`, and a function-local import binds nothing at all — none of which a
        `vars()` sweep can see. The allowlist is the point: it also refuses a future
        `from clarinet.services.image.grid_io import read_grid`, which would reintroduce both
        the import cycle and the disk dependency this module exists to avoid.
        """
        from clarinet.services.image import nrrd_space

        allowed_roots = {
            "__future__",
            "collections",
            "dataclasses",
            "pathlib",
            "typing",
            "numpy",
            "clarinet",
        }
        imported: set[str] = set()
        for node in ast.walk(ast.parse(inspect.getsource(nrrd_space))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, (
                    f"nrrd_space.py line {node.lineno}: relative import "
                    f"{'.' * node.level}{node.module or ''} — imports must be absolute"
                )
                assert node.module is not None
                imported.add(node.module)

        roots = {name.split(".")[0] for name in imported}
        assert roots <= allowed_roots, (
            f"nrrd_space.py imports outside the allowlist: {sorted(roots - allowed_roots)}"
        )
        assert "nrrd" not in roots, "nrrd_space.py must not import pynrrd — it is header-only"
        clarinet_imports = {name for name in imported if name.split(".")[0] == "clarinet"}
        assert clarinet_imports == {
            "clarinet.exceptions.domain",
            "clarinet.services.image.grid",
        }, f"unexpected clarinet imports in nrrd_space.py: {sorted(clarinet_imports)}"

    def test_layered_reader_does_not_import_the_3d_reader(self) -> None:
        """The 4-D reader rests on the shared resolver, never on the 3-D reader.

        An AST scan rather than a substring check: `from clarinet.services.image import
        image` and `import clarinet.services.image.image` both restore the edge this
        change removed without ever spelling the one string a substring guard watches.
        """
        from clarinet.services.image import layered_segmentation

        reader = "clarinet.services.image.image"
        offenders: list[str] = []
        for node in ast.walk(ast.parse(inspect.getsource(layered_segmentation))):
            if isinstance(node, ast.Import):
                offenders += [alias.name for alias in node.names if alias.name == reader]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module == reader:
                    offenders.append(node.module)
                elif node.module == "clarinet.services.image":
                    offenders += [
                        f"{node.module}.{alias.name}"
                        for alias in node.names
                        if alias.name == "image"
                    ]
        assert not offenders, f"layered_segmentation.py imports the 3-D reader: {sorted(offenders)}"

    def test_no_private_frame_constant_copies(self) -> None:
        from clarinet.services.image import image, layered_segmentation, nrrd_repair, nrrd_space

        for mod in (image, layered_segmentation, nrrd_repair, nrrd_space):
            assert "_LPS_TO_RAS" not in vars(mod), mod.__name__
            assert "_LAS_TO_LPS" not in vars(mod), mod.__name__
