"""Disk-level grid IO: read a file's voxel grid, and fail-fast compare two files.

Unlike ``grid.py`` (pure numpy + stdlib, shipped in the Slicer helper bundle),
this module is clarinet-side only and freely imports :class:`Image`,
:class:`LayeredSegmentation`, and :class:`GeometryMismatchError`.
"""

from __future__ import annotations

from pathlib import Path

import nrrd

from clarinet.exceptions.domain import GeometryMismatchError, ImageReadError
from clarinet.services.image.grid import Grid, RelationKind, grid_relation
from clarinet.services.image.image import Image
from clarinet.services.image.layered_segmentation import LayeredSegmentation


def read_grid(path: Path | str) -> Grid:
    """Read a file's voxel grid (shape + affine) without loading voxel data.

    Dispatches on the NRRD header's ``sizes`` length, not the filename —
    ``.seg.nrrd`` is a Slicer naming convention, not a reliable format
    marker; a plain 3-D volume can carry that suffix too. A 4-D
    ``(L, X, Y, Z)`` NRRD (Slicer's layered-segmentation format) is read
    through :class:`LayeredSegmentation`, whose grid accessors already report
    the spatial ``(X, Y, Z)`` grid with the list axis excluded. Everything
    else — 3-D NRRD, NIfTI (3-D or 4-D) — goes through :class:`Image` in
    header-only mode (``load_data=False``); a 4-D NIfTI's shape is truncated
    to its first 3 (spatial) axes, since :class:`Grid` requires exactly 3.

    Raises:
        ImageReadError: the file cannot be read (corrupt or missing header).
        ImageError: unsupported extension (delegated to ``Image.read``).
    """
    path = Path(path)
    if ".nrrd" in path.suffixes:
        try:
            sizes = nrrd.read_header(str(path))["sizes"]
        except Exception as e:
            raise ImageReadError(f"Failed to read NRRD header: {path}") from e
        if len(sizes) == 4:
            seg = LayeredSegmentation.read_header(path)
            return Grid.from_components(seg.shape, seg.spacing, seg.origin, seg.direction)

    img = Image()
    img.read(path, load_data=False)
    shape = (img.shape[0], img.shape[1], img.shape[2])
    return Grid.from_components(shape, img.spacing, img.origin, img.direction)


def assert_same_grid_on_disk(path_a: Path | str, path_b: Path | str, *, atol: float = 1e-4) -> None:
    """Raise :class:`GeometryMismatchError` unless the two files share one grid.

    Reads both grids fresh from disk (:func:`read_grid`) and classifies the
    pair with :func:`grid_relation`. This is a disk-boundary guard, not a
    replacement for ``Image.same_grid``/``Image.assert_same_grid``: those
    compare two already-loaded objects with a tight ``atol``-only check on
    the full affine; this one also inherits ``grid_relation``'s half-voxel
    offset tolerance on its ``SAME`` verdict. That's intentional, not a
    loosened ``atol`` — a mirror is a whole-voxel offset, far outside that
    window, so the tolerance never masks a real flip. Use this at file
    load/save boundaries; use the ``Image`` methods for the in-memory
    pre-overlay guard on objects already held.

    Raises:
        GeometryMismatchError: the two grids classify as ``REARRANGED`` or
            ``FOREIGN`` (message includes both grids' ``summary()``).
    """
    grid_a = read_grid(path_a)
    grid_b = read_grid(path_b)
    if grid_relation(grid_a, grid_b, atol=atol).kind is not RelationKind.SAME:
        raise GeometryMismatchError(
            "Files do not occupy the same physical grid:\n"
            f"  {path_a}: {grid_a.summary()}\n"
            f"  {path_b}: {grid_b.summary()}"
        )
