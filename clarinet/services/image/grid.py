"""Grid value object and relation classifier — the shared grid vocabulary.

Pure core: **numpy + stdlib only, zero ``clarinet`` imports**. A later task
appends this module to the Slicer helper's script bundle (alongside
``correspondence/*``, see ``correspondence_bundle.py``), where it must run
standalone inside Slicer's embedded Python — which has neither ``clarinet``
nor ``nibabel``/``pynrrd`` on its path. That constraint applies from this
module's introduction, not just once the bundle wiring lands: it carries no
raise-policy of its own — :func:`grid_relation` always returns a verdict
(``FOREIGN`` included, never an exception) so callers in either runtime can
layer their own fail-fast assert on top (``GeometryMismatchError``
clarinet-side, ``SlicerHelperError`` Slicer-side).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np

# Per-axis translation tolerance for REARRANGED, in voxels (index-space units,
# not mm — see grid_relation). A permutation/flip relationship maps one grid's
# index space onto the other's via an exact integer offset (0, or shape-1 for
# a flipped axis); on-disk formats can carry enough float rounding to blur
# that by a fraction of a voxel, so the "correct" target is accepted as long
# as it is nearer than any neighbouring voxel center — i.e. within half a
# voxel. Not derived from `atol`: the two tolerances answer different
# questions (is the linear part a clean permutation vs. which integer offset
# a noisy float is nearest to) and conflating them would make the offset
# window shrink for a caller who only meant to loosen the permutation check.
_OFFSET_TOL_VOXELS = 0.5


class RelationKind(enum.Enum):
    """Taxonomy of how two grids relate, returned by :func:`grid_relation`."""

    SAME = "same"
    REARRANGED = "rearranged"
    FOREIGN = "foreign"


@dataclass(frozen=True)
class GridRelation:
    """Verdict of :func:`grid_relation`: a kind, plus detail for ``REARRANGED``.

    ``perm``/``flips`` are ``None`` for ``SAME``/``FOREIGN`` — only a
    ``REARRANGED`` verdict carries a well-defined axis correspondence to
    report (e.g. for a mismatch message or a re-indexing repair step).
    """

    kind: RelationKind
    perm: tuple[int, int, int] | None = None  # source axis feeding each output axis
    flips: tuple[bool, bool, bool] | None = None  # whether that mapping is negated


@dataclass(frozen=True, eq=False)
class Grid:
    """A voxel grid: shape plus a 4x4 voxel-to-world affine, LPS by construction.

    ``eq``/``hash`` are deliberately left at default object identity
    (``eq=False``): a frozen dataclass's auto-generated ``__eq__`` would
    compare the ``affine`` field with ``==``, and numpy raises on the
    resulting array's ambiguous truth value. Grids are compared via
    :func:`grid_relation`, never ``==``.
    """

    shape: tuple[int, int, int]
    affine: np.ndarray

    def __post_init__(self) -> None:
        if len(self.shape) != 3:
            raise ValueError(f"shape must be a 3-tuple, got length {len(self.shape)}")
        if self.affine.shape != (4, 4):
            raise ValueError(f"affine must be a 4x4 matrix, got shape {self.affine.shape}")

    @classmethod
    def from_components(
        cls,
        shape: tuple[int, int, int],
        spacing: tuple[float, float, float],
        origin: tuple[float, float, float],
        direction: np.ndarray,
    ) -> Grid:
        """Build a Grid from shape/spacing/origin/direction components (LPS).

        Assembles the affine exactly like ``Image.affine_4x4``: the linear
        part is ``direction`` scaled per-column by ``spacing`` (``direction``'s
        columns are unit vectors per axis), the translation is ``origin``.
        Matching components on both sides therefore produce a byte-identical
        affine — the two are meant to be interchangeable representations of
        the same grid.
        """
        direction_arr = np.asarray(direction, dtype=float)
        if direction_arr.shape != (3, 3):
            raise ValueError(f"direction must be a 3x3 matrix, got shape {direction_arr.shape}")
        if len(spacing) != 3:
            raise ValueError(f"spacing must be a 3-tuple, got length {len(spacing)}")
        if len(origin) != 3:
            raise ValueError(f"origin must be a 3-tuple, got length {len(origin)}")
        affine = np.eye(4)
        affine[:3, :3] = direction_arr * np.array(spacing, dtype=float)
        affine[:3, 3] = np.array(origin, dtype=float)
        return cls(shape=(int(shape[0]), int(shape[1]), int(shape[2])), affine=affine)

    @property
    def origin(self) -> tuple[float, float, float]:
        """Patient-space origin (x, y, z) in mm — the affine's translation column."""
        o = self.affine[:3, 3]
        return (float(o[0]), float(o[1]), float(o[2]))

    @property
    def spacing(self) -> tuple[float, float, float]:
        """Voxel spacing in mm (x, y, z) — the column norms of the affine's linear part."""
        norms = np.linalg.norm(self.affine[:3, :3], axis=0)
        return (float(norms[0]), float(norms[1]), float(norms[2]))

    @property
    def direction(self) -> np.ndarray:
        """3x3 direction cosine matrix (columns = unit direction vectors per axis)."""
        linear = self.affine[:3, :3]
        spacing = np.linalg.norm(linear, axis=0)
        safe_spacing = np.where(spacing == 0, 1.0, spacing)
        return linear / safe_spacing

    def summary(self) -> str:
        """One-line grid description (shape/origin/spacing/direction) for diagnostics.

        Prints the full direction matrix (not just its diagonal) so an
        oblique or off-diagonal flip is visible in a mismatch message, not
        only an axis-aligned one. Ported from ``Image._grid_summary``.
        """
        return (
            f"shape={tuple(self.shape)}, "
            f"origin={tuple(round(float(x), 3) for x in self.origin)}, "
            f"spacing={tuple(round(float(x), 3) for x in self.spacing)}, "
            f"direction={np.round(self.direction, 3).tolist()}"
        )


def grid_relation(a: Grid, b: Grid, *, atol: float = 1e-4) -> GridRelation:
    """Classify how grid ``b`` relates to grid ``a``.

    Never raises for a mismatched grid — ``FOREIGN`` is a normal return
    value, not an exception; disk-level fail-fast asserts belong to a later,
    clarinet-only module (this one also ships in the Slicer bundle, which has
    no clarinet exceptions to raise).

    Composes ``M = inv(a.affine) @ b.affine`` — the transform from ``b``'s
    voxel-index space into ``a``'s. ``REARRANGED`` iff ``M``'s linear part is
    a signed permutation matrix (each row and each column has exactly one
    entry ~= +-1, the rest ~0, within ``atol``) and, for every axis mapping
    ``b``-axis ``j`` onto ``a``-axis ``i`` with sign ``s``:
    ``b.shape[j] == a.shape[i]`` and the translation ``M[i, 3]`` is within
    half a voxel of the exact target for that sign (``0`` for ``s=+1``,
    ``a.shape[i] - 1`` for ``s=-1``). ``SAME`` is the identity instance of
    that same check (no permutation, no flips, zero offset). Everything else
    — a non-permutation linear part (e.g. a genuine rotation), a shape
    mismatch, or an offset outside the half-voxel window — is ``FOREIGN``.

    The half-voxel tolerance is what makes ``REARRANGED`` mean "every voxel
    center of one grid lands exactly on a voxel center of the other,
    floating-point noise aside": it is the widest window that cannot
    straddle two neighbouring voxel centers, so it never misclassifies a
    genuinely different alignment as an exact one.

    Works identically for axis-aligned and oblique ``direction`` matrices —
    the predicate operates purely on the index-space transform ``M``, never
    on world-axis labels.
    """
    m = np.linalg.inv(a.affine) @ b.affine
    linear = m[:3, :3]
    offset = m[:3, 3]

    perm = [-1, -1, -1]
    flips = [False, False, False]
    used_cols: set[int] = set()

    for i in range(3):
        row = linear[i]
        hits = [j for j in range(3) if not np.isclose(row[j], 0.0, atol=atol)]
        if len(hits) != 1:
            return GridRelation(kind=RelationKind.FOREIGN)
        j = hits[0]
        value = row[j]
        if not np.isclose(abs(value), 1.0, atol=atol) or j in used_cols:
            return GridRelation(kind=RelationKind.FOREIGN)
        used_cols.add(j)
        perm[i] = j
        flips[i] = bool(value < 0)

    for i in range(3):
        j = perm[i]
        if b.shape[j] != a.shape[i]:
            return GridRelation(kind=RelationKind.FOREIGN)
        target = float(a.shape[i] - 1) if flips[i] else 0.0
        if abs(offset[i] - target) > _OFFSET_TOL_VOXELS:
            return GridRelation(kind=RelationKind.FOREIGN)

    perm_t = (perm[0], perm[1], perm[2])
    flips_t = (flips[0], flips[1], flips[2])
    if perm_t == (0, 1, 2) and flips_t == (False, False, False):
        return GridRelation(kind=RelationKind.SAME)
    return GridRelation(kind=RelationKind.REARRANGED, perm=perm_t, flips=flips_t)
