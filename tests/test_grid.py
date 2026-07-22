"""Tests for clarinet.services.image.grid — Grid value object + grid_relation classifier."""

import itertools

import numpy as np
import pytest

from clarinet.services.image import Image
from clarinet.services.image.grid import Grid, GridRelation, RelationKind, grid_relation

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_SEED = 20260722
_BASE_SHAPE = (7, 9, 11)


def _rotation_matrix(deg_x: float, deg_y: float, deg_z: float) -> np.ndarray:
    """Fixed XYZ-order rotation matrix (degrees) — an oblique direction for tests."""
    rx, ry, rz = np.radians([deg_x, deg_y, deg_z])
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    rot_x = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    rot_y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rot_z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rot_z @ rot_y @ rot_x


_OBLIQUE_DIRECTION = _rotation_matrix(12.0, -7.0, 25.0)

_DIRECTIONS = [np.eye(3), _OBLIQUE_DIRECTION]
_DIRECTION_IDS = ["axis_aligned", "oblique"]


def _random_grid(
    rng: np.random.Generator, shape: tuple[int, int, int], direction: np.ndarray
) -> Grid:
    """A grid with random (but physically sane) spacing/origin over a fixed direction."""
    spacing = tuple(float(x) for x in rng.uniform(0.3, 2.5, size=3))
    origin = tuple(float(x) for x in rng.uniform(-50.0, 50.0, size=3))
    return Grid.from_components(shape, spacing, origin, direction)


def _related_grid(
    a: Grid,
    perm: tuple[int, int, int],
    flips: tuple[bool, bool, bool],
    offset_error: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Grid:
    """Build ``b`` such that ``M = inv(a.affine) @ b.affine`` is exactly the signed
    permutation described by ``(perm, flips)``, plus ``offset_error`` added on top of
    the exact per-axis target (for boundary/FOREIGN cases).

    ``perm[i]`` = the b-axis feeding a-axis ``i``; ``flips[i]`` = whether that mapping
    is negated (mirrors ``grid_relation``'s own convention, verified by round-trip).
    """
    linear = np.zeros((3, 3))
    t = np.zeros(3)
    for i in range(3):
        j = perm[i]
        sign = -1.0 if flips[i] else 1.0
        linear[i, j] = sign
        target = (a.shape[i] - 1) if flips[i] else 0.0
        t[i] = target + offset_error[i]
    m = np.eye(4)
    m[:3, :3] = linear
    m[:3, 3] = t
    b_affine = a.affine @ m

    inv_perm = [0, 0, 0]
    for i in range(3):
        inv_perm[perm[i]] = i
    b_shape = tuple(int(a.shape[inv_perm[j]]) for j in range(3))
    return Grid(shape=b_shape, affine=b_affine)


_NAMED_TRANSFORMS: dict[str, tuple[tuple[int, int, int], tuple[bool, bool, bool]]] = {
    "slice_mirror": ((0, 1, 2), (False, False, True)),
    "inplane_transpose": ((1, 0, 2), (False, False, False)),
    "transpose_and_mirror": ((1, 0, 2), (True, False, True)),
    "full_reverse": ((0, 1, 2), (True, True, True)),
}


def _random_transforms(
    count: int,
) -> dict[str, tuple[tuple[int, int, int], tuple[bool, bool, bool]]]:
    """``count`` additional random signed-permutation transforms (deterministic, fixed seed)."""
    rng = np.random.default_rng(_SEED)
    perms = list(itertools.permutations(range(3)))
    out: dict[str, tuple[tuple[int, int, int], tuple[bool, bool, bool]]] = {}
    i = 0
    while len(out) < count:
        perm = tuple(int(x) for x in perms[rng.integers(len(perms))])
        flips = tuple(bool(x) for x in rng.integers(0, 2, size=3))
        if perm == (0, 1, 2) and flips == (False, False, False):
            continue  # identity is SAME, covered separately
        out[f"random{i}"] = (perm, flips)
        i += 1
    return out


_ALL_TRANSFORMS = {**_NAMED_TRANSFORMS, **_random_transforms(4)}


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------


class TestGrid:
    def test_from_components_roundtrip(self) -> None:
        shape = (10, 12, 8)
        spacing = (0.5, 0.6, 0.7)
        origin = (1.0, -2.5, 3.25)
        direction = _OBLIQUE_DIRECTION

        grid = Grid.from_components(shape, spacing, origin, direction)

        assert grid.shape == shape
        assert pytest.approx(grid.spacing, abs=1e-9) == spacing
        assert pytest.approx(grid.origin, abs=1e-9) == origin
        assert np.allclose(grid.direction, direction, atol=1e-9)

    def test_affine_matches_image_affine_4x4(self) -> None:
        image = Image()
        image.spacing = (0.5, 0.6, 0.7)
        image.origin = (1.0, -2.5, 3.25)
        image.direction = _OBLIQUE_DIRECTION

        grid = Grid.from_components(
            shape=(10, 12, 8),
            spacing=image.spacing,
            origin=image.origin,
            direction=image.direction,
        )

        assert np.array_equal(grid.affine, image.affine_4x4)

    def test_affine_matches_image_affine_4x4_axis_aligned(self) -> None:
        image = Image()
        image.spacing = (1.0, 1.0, 2.0)
        image.origin = (0.0, 0.0, 0.0)

        grid = Grid.from_components(
            shape=(4, 4, 4),
            spacing=image.spacing,
            origin=image.origin,
            direction=image.direction,
        )

        assert np.array_equal(grid.affine, image.affine_4x4)

    def test_summary_contains_shape_origin_spacing_full_direction(self) -> None:
        shape = (10, 12, 8)
        spacing = (0.5, 0.6, 0.7)
        origin = (1.0, -2.5, 3.25)
        grid = Grid.from_components(shape, spacing, origin, _OBLIQUE_DIRECTION)

        text = grid.summary()

        assert "shape=" in text
        assert "origin=" in text
        assert "spacing=" in text
        assert "direction=" in text
        assert str(tuple(shape)) in text
        # The off-diagonal terms of an oblique direction must be visible (not just the
        # diagonal) — an off-diagonal-only flip must be legible in a mismatch message.
        off_diagonal = round(float(_OBLIQUE_DIRECTION[0, 1]), 3)
        assert str(off_diagonal) in text

    def test_post_init_rejects_wrong_shape_length(self) -> None:
        with pytest.raises(ValueError, match="shape"):
            Grid(shape=(1, 2), affine=np.eye(4))  # type: ignore[arg-type]

    def test_post_init_rejects_wrong_affine_shape(self) -> None:
        with pytest.raises(ValueError, match="affine"):
            Grid(shape=(1, 2, 3), affine=np.eye(3))


# ---------------------------------------------------------------------------
# grid_relation
# ---------------------------------------------------------------------------


class TestGridRelation:
    @pytest.mark.parametrize("direction", _DIRECTIONS, ids=_DIRECTION_IDS)
    def test_identity_is_same(self, direction: np.ndarray) -> None:
        rng = np.random.default_rng(_SEED)
        a = _random_grid(rng, _BASE_SHAPE, direction)
        b = Grid(shape=a.shape, affine=a.affine.copy())

        result = grid_relation(a, b)

        assert result == GridRelation(kind=RelationKind.SAME)
        assert result.kind is RelationKind.SAME
        assert result.perm is None
        assert result.flips is None

    @pytest.mark.parametrize("direction", _DIRECTIONS, ids=_DIRECTION_IDS)
    @pytest.mark.parametrize(
        ("name", "transform"), list(_ALL_TRANSFORMS.items()), ids=list(_ALL_TRANSFORMS.keys())
    )
    def test_signed_permutation_is_rearranged(
        self,
        name: str,
        transform: tuple[tuple[int, int, int], tuple[bool, bool, bool]],
        direction: np.ndarray,
    ) -> None:
        perm, flips = transform
        rng = np.random.default_rng(_SEED)
        a = _random_grid(rng, _BASE_SHAPE, direction)
        b = _related_grid(a, perm, flips)

        result = grid_relation(a, b)

        assert result.kind is RelationKind.REARRANGED
        assert result.perm == perm
        assert result.flips == flips

    def test_shape_mismatch_is_foreign(self) -> None:
        rng = np.random.default_rng(_SEED)
        a = _random_grid(rng, _BASE_SHAPE, np.eye(3))
        b = _related_grid(a, perm=(1, 0, 2), flips=(False, False, False))
        tampered = Grid(shape=(b.shape[0] + 1, b.shape[1], b.shape[2]), affine=b.affine)

        result = grid_relation(a, tampered)

        assert result.kind is RelationKind.FOREIGN
        assert result.perm is None
        assert result.flips is None

    def test_non_integral_offset_is_foreign(self) -> None:
        rng = np.random.default_rng(_SEED)
        a = _random_grid(rng, _BASE_SHAPE, np.eye(3))
        b = _related_grid(
            a, perm=(0, 1, 2), flips=(False, False, False), offset_error=(0.8, 0.0, 0.0)
        )

        result = grid_relation(a, b)

        assert result.kind is RelationKind.FOREIGN

    @pytest.mark.parametrize("direction", _DIRECTIONS, ids=_DIRECTION_IDS)
    def test_small_rotation_is_foreign(self, direction: np.ndarray) -> None:
        rng = np.random.default_rng(_SEED)
        a = _random_grid(rng, _BASE_SHAPE, direction)
        rot = _rotation_matrix(0.0, 0.0, 3.0)
        m = np.eye(4)
        m[:3, :3] = rot
        b = Grid(shape=a.shape, affine=a.affine @ m)

        result = grid_relation(a, b)

        assert result.kind is RelationKind.FOREIGN

    @pytest.mark.parametrize(
        ("offset_error", "expected_kind"),
        [
            (0.49, RelationKind.REARRANGED),
            (0.51, RelationKind.FOREIGN),
        ],
        ids=["under_half_voxel", "over_half_voxel"],
    )
    def test_offset_tolerance_boundary(
        self, offset_error: float, expected_kind: RelationKind
    ) -> None:
        rng = np.random.default_rng(_SEED)
        a = _random_grid(rng, _BASE_SHAPE, np.eye(3))
        b = _related_grid(
            a,
            perm=(0, 1, 2),
            flips=(False, False, True),
            offset_error=(0.0, 0.0, offset_error),
        )

        result = grid_relation(a, b)

        assert result.kind is expected_kind

    def test_atol_controls_linear_part_tolerance(self) -> None:
        """A rotation small enough to hide under a loosened `atol` still classifies."""
        rng = np.random.default_rng(_SEED)
        a = _random_grid(rng, _BASE_SHAPE, np.eye(3))
        rot = _rotation_matrix(0.0, 0.0, 0.001)  # ~1.7e-5 rad sine term
        m = np.eye(4)
        m[:3, :3] = rot
        b = Grid(shape=a.shape, affine=a.affine @ m)

        assert grid_relation(a, b, atol=1e-8).kind is RelationKind.FOREIGN
        assert grid_relation(a, b, atol=1e-3).kind is RelationKind.SAME
