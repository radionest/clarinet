"""LayeredSegmentation — RAM-lean 4-D overlapping NRRD segmentations (Slicer format).

Models overlapping segments (e.g. psoas ⊆ skeletal muscle) as a 4-D ``(L, X, Y, Z)``
uint8 NRRD over one shared 3-D grid — the layout Slicer uses for multi-layer
``.seg.nrrd``. This is composition, not a 4-D ``Segmentation``: each materialized layer
is a normal 3-D array on the shared grid, so ``Image``/``Segmentation`` 3-D invariants
stay 3-D.

Read side (``read_layer``/``read_layer_slice``/``iter_layers``) does a full 4-D
``nrrd.read()`` then numpy-indexes the requested layer — layout-agnostic (correct
regardless of on-disk byte order) but not lazy. A seek-based per-layer reader is
deferred to #454.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self

import nrrd
import numpy as np

from clarinet.exceptions.domain import ImageError, ImageReadError, ImageWriteError
from clarinet.services.image.grid import Grid
from clarinet.services.image.image import _nrrd_space_to_lps
from clarinet.utils.logger import logger


def _parse_segments(header: dict[str, Any]) -> list[tuple[str, int, int]]:
    """Extract ``(name, layer_index, label_value)`` per segment, ordered by block index.

    Honors the general ``SegmentN_Layer`` → layer mapping (multiple non-overlapping
    segments may share a layer). Missing ``_Layer`` defaults to the block index; missing
    ``_Name`` to ``Segment_N``; missing ``_LabelValue`` to 1.
    """
    segments: list[tuple[str, int, int]] = []
    i = 0
    while f"Segment{i}_LabelValue" in header or f"Segment{i}_Layer" in header:
        name = str(header.get(f"Segment{i}_Name", f"Segment_{i}"))
        layer = int(header.get(f"Segment{i}_Layer", i))
        label = int(header.get(f"Segment{i}_LabelValue", 1))
        segments.append((name, layer, label))
        i += 1
    return segments


class LayeredSegmentation:
    """Multi-layer (4-D, overlapping) NRRD segmentation over one shared 3-D grid."""

    def __init__(self) -> None:
        self._spacing: tuple[float, float, float] = (1.0, 1.0, 1.0)
        self._origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._direction: np.ndarray = np.eye(3)
        self._shape: tuple[int, int, int] = (0, 0, 0)
        self._segments: list[tuple[str, int, int]] = []
        self._layer_arrays: list[np.ndarray | None] = []
        self._nrrd_header: dict[str, Any] | None = None

    # -- grid accessors (shared 3-D grid) --

    @property
    def shape(self) -> tuple[int, int, int]:
        """Spatial shape (X, Y, Z) of each layer."""
        return self._shape

    @property
    def spacing(self) -> tuple[float, float, float]:
        return self._spacing

    @property
    def origin(self) -> tuple[float, float, float]:
        return self._origin

    @property
    def direction(self) -> np.ndarray:
        return self._direction

    @property
    def segments(self) -> list[tuple[str, int, int]]:
        """``(name, layer_index, label_value)`` per segment."""
        return list(self._segments)

    @property
    def grid(self) -> Grid:
        """This layered segmentation's shared 3-D voxel grid as a :class:`Grid`."""
        return Grid.from_components(self.shape, self.spacing, self.origin, self.direction)

    # -- construction --

    @classmethod
    def from_layers(
        cls,
        layers: list[tuple[str, np.ndarray]],
        *,
        spacing: tuple[float, float, float],
        origin: tuple[float, float, float],
        direction: np.ndarray,
    ) -> Self:
        """Build from one binary/label mask per segment (one segment per layer, label 1).

        Args:
            layers: ``[(name, mask3d), ...]`` — all masks must share the same 3-D shape.
            spacing: voxel spacing (mm).
            origin: patient-space origin (mm, LPS).
            direction: 3x3 direction cosines (columns = unit axis vectors).

        Raises:
            ImageError: empty ``layers`` or a shape mismatch.
        """
        if not layers:
            raise ImageError("from_layers requires at least one layer")
        direction = np.asarray(direction, dtype=float)
        if direction.shape != (3, 3):
            raise ImageError(f"direction must be a 3x3 matrix, got shape {direction.shape}")

        self = cls()
        self._spacing = (float(spacing[0]), float(spacing[1]), float(spacing[2]))
        self._origin = (float(origin[0]), float(origin[1]), float(origin[2]))
        self._direction = direction
        first_shape = tuple(int(s) for s in layers[0][1].shape)
        if len(first_shape) != 3:
            raise ImageError(f"layers must be 3-D; got shape {first_shape}")
        self._shape = first_shape

        for index, (name, mask) in enumerate(layers):
            if tuple(int(s) for s in mask.shape) != first_shape:
                raise ImageError(
                    f"Layer '{name}' shape {tuple(mask.shape)} != first layer {first_shape}"
                )
            self._segments.append((str(name), index, 1))  # one segment per layer, label 1
            self._layer_arrays.append(np.asarray(mask, dtype=np.uint8))
        return self

    # -- write --

    def save(self, path: Path | str) -> Path:
        """Write the 4-D ``(L, X, Y, Z)`` uint8 NRRD (raw, layer/list axis first).

        Pre-allocates the 4-D array once and fills each layer in place, releasing each
        source mask as it goes (avoids ``np.stack``'s transient doubling). Writes
        ``encoding: raw`` with the layer/list axis first in ``sizes`` (``[L, X, Y, Z]``,
        ``kinds`` list-first) — Slicer's native ``.seg.nrrd`` layout, so layers are
        **interleaved** byte-by-byte on disk (F-order, layer axis fastest), not
        layer-contiguous. A future per-layer reader (#454) therefore reads strided or the
        whole 4-D array; ``encoding: raw`` gives no contiguous-seek advantage in this
        layout (raw-vs-gzip to be re-evaluated in #454).
        """
        path = Path(path)
        num_layers = len(self._segments)
        try:
            out = np.zeros((num_layers, *self._shape), dtype=np.uint8)
            for i, arr in enumerate(self._layer_arrays):
                if arr is None:
                    raise ImageWriteError(f"Layer {i} was already consumed; save() is single-use")
                out[i] = arr
                self._layer_arrays[i] = None  # release the source so the caller's free reclaims it
            header = self._build_header()
            nrrd.write(str(path), out, header)
        except ImageError:
            raise
        except Exception as e:
            raise ImageWriteError(f"Failed to write layered NRRD: {path}") from e
        logger.debug(
            f"Saved LayeredSegmentation {path.name}: {num_layers} layers, shape={self._shape}"
        )
        return path

    def _build_header(self) -> dict[str, Any]:
        """NRRD header for the 4-D layered format (Slicer contract)."""
        space_dirs = np.vstack([np.full(3, np.nan), (self._direction * np.array(self._spacing)).T])
        header: dict[str, Any] = {
            "dimension": 4,
            "space": "left-posterior-superior",
            "kinds": ["list", "domain", "domain", "domain"],
            "space directions": space_dirs,
            "space origin": np.array(self._origin),
            "encoding": "raw",
        }
        for seg_index, (name, layer, label) in enumerate(self._segments):
            header[f"Segment{seg_index}_ID"] = f"Segment_{seg_index}"
            header[f"Segment{seg_index}_Name"] = name
            header[f"Segment{seg_index}_LabelValue"] = str(label)
            header[f"Segment{seg_index}_Layer"] = str(layer)
        return header

    # -- header read --

    @classmethod
    def read_header(cls, path: Path | str) -> Self:
        """Read grid + segment metadata (no voxels)."""
        path = Path(path)
        try:
            header = nrrd.read_header(str(path))
        except Exception as e:
            raise ImageReadError(f"Failed to read layered NRRD header: {path}") from e
        self = cls()
        self._nrrd_header = header
        self._apply_grid_from_header(header)
        self._segments = _parse_segments(header)
        return self

    def _apply_grid_from_header(self, header: dict[str, Any]) -> None:
        """Populate spacing/origin/direction/shape from a 4-D NRRD header.

        Spatial directions are rows 1..3 of ``space directions`` (row 0 is the ``none``
        list axis). Honors the header's ``space`` field via the shared
        ``_nrrd_space_to_lps`` helper (LPS as-is; RAS/LAS converted; anything else
        raises) — the same conversion :meth:`Image.read_nrrd` applies to 3-D NRRD.
        """
        sizes = [int(s) for s in header["sizes"]]
        self._shape = (sizes[1], sizes[2], sizes[3])
        space_dirs = header.get("space directions")
        if space_dirs is not None:
            raw_origin = header.get("space origin")
            arr, origin = _nrrd_space_to_lps(
                header.get("space"),
                np.asarray(space_dirs[1:4], dtype=float),  # skip the nan list-axis row
                np.asarray(raw_origin[:3], dtype=float) if raw_origin is not None else None,
            )
            norms = np.linalg.norm(arr, axis=1)
            self._spacing = (float(norms[0]), float(norms[1]), float(norms[2]))
            self._direction = (arr / norms[:, np.newaxis]).T
            if origin is not None:
                self._origin = (float(origin[0]), float(origin[1]), float(origin[2]))
        else:
            space_origin = header.get("space origin")
            if space_origin is not None:
                vals = space_origin[:3]
                self._origin = (float(vals[0]), float(vals[1]), float(vals[2]))

    # -- voxel read --

    def _resolve_layer(self, name_or_index: str | int) -> tuple[int, int | None]:
        """Map to ``(layer_index, label)``.

        A segment ``name`` resolves to its ``SegmentN_Layer`` index **and** its
        ``LabelValue`` — the label isolates it from any co-tenant segments Slicer packed
        onto the same layer. An ``int`` is a direct layer index with no label filter
        (the whole layer, co-tenants included).
        """
        if isinstance(name_or_index, int):
            return name_or_index, None
        for name, layer, label in self._segments:
            if name == name_or_index:
                return layer, label
        raise ImageError(f"no segment named {name_or_index!r}")

    def _check_layer_index(self, data: np.ndarray, layer_index: int) -> None:
        """Raise ``ImageError`` for an out-of-range layer index (no silent negative wrap)."""
        num_layers = int(data.shape[0])
        if not 0 <= layer_index < num_layers:
            raise ImageError(f"layer index {layer_index} out of range [0, {num_layers})")

    @staticmethod
    def _isolate(arr: np.ndarray, label: int | None) -> np.ndarray:
        """Zero co-tenant labels sharing the layer; keep this segment's own voxels.

        ``label is None`` (int-index read) returns ``arr`` untouched. For a
        one-segment-per-layer file (the ``from_layers`` default) this is a no-op — the
        layer holds only ``label`` and 0.
        """
        return arr if label is None else np.where(arr == label, arr, 0)

    def read_layer(
        self, path: Path | str, name_or_index: str | int, *, dtype: Any = np.uint8
    ) -> np.ndarray:
        """Return one segment (by name) or one raw layer (by int index) as a 3-D array.

        A ``str`` resolves via segment name → ``SegmentN_Layer`` and returns **only that
        segment's** voxels — co-tenant segments packed onto the same layer are zeroed, the
        segment's own ``LabelValue`` preserved. An ``int`` is a direct layer index and
        returns the whole layer unfiltered. pynrrd has no lazy proxy, so the full 4-D
        array is read then indexed (the accepted read floor).
        """
        layer_index, label = self._resolve_layer(name_or_index)
        data = self._read_4d(path)
        self._check_layer_index(data, layer_index)
        result: np.ndarray = self._isolate(data[layer_index], label).astype(dtype, copy=False)
        return result

    def read_layer_slice(
        self,
        path: Path | str,
        name_or_index: str | int,
        index: int,
        *,
        axis: int = 2,
        dtype: Any = np.uint8,
    ) -> np.ndarray:
        """Return one 2-D slice of one segment/layer (non-lazy: full read then index)."""
        layer_index, label = self._resolve_layer(name_or_index)
        data = self._read_4d(path)
        self._check_layer_index(data, layer_index)
        layer = data[layer_index]
        slicer: list[Any] = [slice(None)] * layer.ndim
        slicer[axis] = index
        result: np.ndarray = self._isolate(layer[tuple(slicer)], label).astype(dtype, copy=False)
        return result

    def iter_layers(
        self, path: Path | str, *, dtype: Any = np.uint8
    ) -> Iterator[tuple[str, np.ndarray]]:
        """Yield ``(segment_name, segment_mask)`` per segment (one 4-D read, shared).

        Each segment is isolated to its own ``LabelValue``, so two segments sharing a
        layer yield distinct masks — not the same raw layer twice.
        """
        data = self._read_4d(path)
        for name, layer, label in self._segments:
            yield name, self._isolate(data[layer], label).astype(dtype, copy=False)

    def _read_4d(self, path: Path | str) -> np.ndarray:
        """Read the full 4-D voxel array (pynrrd native uint8, no lazy proxy)."""
        try:
            data, _header = nrrd.read(str(Path(path)))
        except Exception as e:
            raise ImageReadError(f"Failed to read layered NRRD: {path}") from e
        result: np.ndarray = data
        return result
