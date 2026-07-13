"""LayeredSegmentation — RAM-lean 4-D overlapping NRRD segmentations (Slicer format).

Models overlapping segments (e.g. psoas ⊆ skeletal muscle) as a 4-D ``(L, X, Y, Z)``
uint8 NRRD over one shared 3-D grid — the layout Slicer uses for multi-layer
``.seg.nrrd``. This is composition, not a 4-D ``Segmentation``: each materialized layer
is a normal 3-D array on the shared grid, so ``Image``/``Segmentation`` 3-D invariants
stay 3-D.

Read side (``read_layer``/``read_layer_slice``/``iter_layers``) is added in a follow-up
step; ``read_header`` + ``from_layers`` + ``save`` cover the write/metadata round-trip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import nrrd
import numpy as np

from clarinet.exceptions.domain import ImageError, ImageReadError, ImageWriteError
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
        list axis). NRRD is LPS-native — no RAS flip.
        """
        sizes = [int(s) for s in header["sizes"]]
        self._shape = (sizes[1], sizes[2], sizes[3])
        space_dirs = header.get("space directions")
        if space_dirs is not None:
            arr = np.asarray(space_dirs[1:4], dtype=float)  # skip the nan list-axis row
            norms = np.linalg.norm(arr, axis=1)
            self._spacing = (float(norms[0]), float(norms[1]), float(norms[2]))
            self._direction = (arr / norms[:, np.newaxis]).T
        space_origin = header.get("space origin")
        if space_origin is not None:
            vals = space_origin[:3]
            self._origin = (float(vals[0]), float(vals[1]), float(vals[2]))
