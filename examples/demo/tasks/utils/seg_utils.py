"""Utilities for .seg.nrrd files with named segment metadata.

Provides read/write helpers that embed segment names and label values
in the NRRD header (``Segment{i}_Name``, ``Segment{i}_LabelValue``, etc.),
matching the 3D Slicer Segmentation NRRD convention.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import nrrd
import numpy as np

# ---------------------------------------------------------------------------
# Label maps
# ---------------------------------------------------------------------------

SEG_LABELS: dict[str, int] = {"mts": 1, "unclear": 2, "benign": 3}
REVIEW_LABELS: dict[str, int] = {"mts": 1, "unclear": 2, "benign": 3, "invisible": 4}


def master_label_converter(name: str) -> int:
    """Convert a numeric segment name to its integer label value."""
    return int(name)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_seg_nrrd(
    data: np.ndarray,
    path: str | Path,
    segment_names: list[str],
    label_converter: Callable[[str], int],
    *,
    spacing: tuple[float, ...],
    origin: tuple[float, ...],
    direction: np.ndarray,
) -> None:
    """Save a labeled array as ``.seg.nrrd`` with segment metadata headers.

    Args:
        data: 3D uint8 label array.
        path: Destination file path.
        segment_names: Ordered list of segment names (one per non-zero label).
        label_converter: Maps a segment name to its integer label value.
        spacing: Voxel spacing (x, y, z).
        origin: Image origin (x, y, z).
        direction: 3x3 direction cosine matrix.
    """
    header: dict[str, Any] = {
        "type": "unsigned char",
        "dimension": 3,
        "space": "left-posterior-superior",
        "space directions": (direction * np.array(spacing)).T,
        "space origin": np.array(origin),
    }

    for i, name in enumerate(segment_names):
        lbl = label_converter(name)
        header[f"Segment{i}_ID"] = f"Segment_{i}"
        header[f"Segment{i}_Name"] = name
        header[f"Segment{i}_LabelValue"] = str(lbl)
        header[f"Segment{i}_Layer"] = "0"

    nrrd.write(str(path), data.astype(np.uint8), header)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_seg_nrrd_labels(path: str | Path) -> dict[int, str]:
    """Parse segment metadata from a ``.seg.nrrd`` header.

    Returns:
        Mapping ``{label_value: segment_name}``.
    """
    _, header = nrrd.read(str(path))
    labels: dict[int, str] = {}
    i = 0
    while f"Segment{i}_Name" in header:
        name = header[f"Segment{i}_Name"]
        lbl = int(header.get(f"Segment{i}_LabelValue", i + 1))
        labels[lbl] = name
        i += 1
    return labels
