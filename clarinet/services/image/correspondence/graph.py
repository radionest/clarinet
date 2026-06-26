"""Array -> OverlapGraph (the one impure builder) + the correspond() entry point."""

from __future__ import annotations

import numpy as np

from clarinet.services.image.correspondence.model import (
    Component,
    Correspondence,
    MatchingStrategy,
    OverlapGraph,
    PairStats,
)


def _components(labelmap: np.ndarray) -> dict[int, Component]:
    """Per-label size and voxel-index centroid. O(N*L) -- fine for typical label counts."""
    out: dict[int, Component] = {}
    for lbl in np.unique(labelmap):
        if lbl == 0:
            continue
        coords = np.argwhere(labelmap == lbl)
        out[int(lbl)] = Component(
            label=int(lbl),
            size=int(coords.shape[0]),
            centroid=tuple(float(x) for x in coords.mean(axis=0)),
        )
    return out


def _centroid_inside(centroid: tuple[float, ...], labelmap: np.ndarray, label: int) -> bool:
    idx = tuple(round(c) for c in centroid)
    for i, dim in zip(idx, labelmap.shape):
        if i < 0 or i >= dim:
            return False
    return int(labelmap[idx]) == label


def build_overlap_graph(
    a: np.ndarray, b: np.ndarray, *, spacing: tuple[float, ...]
) -> OverlapGraph:
    comps_a = _components(a)
    comps_b = _components(b)
    spacing_arr = np.asarray(spacing, dtype=float)
    edges: list[PairStats] = []

    both = (a != 0) & (b != 0)
    if both.any():
        base = int(b.max()) + 1
        key = a[both].astype(np.int64) * base + b[both].astype(np.int64)
        uniq, counts = np.unique(key, return_counts=True)
        for k, inter in zip(uniq.tolist(), counts.tolist()):
            la, lb = divmod(int(k), base)
            ca, cb = comps_a[la], comps_b[lb]
            dist = float(
                np.linalg.norm((np.asarray(ca.centroid) - np.asarray(cb.centroid)) * spacing_arr)
            )
            edges.append(
                PairStats(
                    a=la,
                    b=lb,
                    inter=int(inter),
                    size_a=ca.size,
                    size_b=cb.size,
                    centroid_distance=dist,
                    a_centroid_in_b=_centroid_inside(ca.centroid, b, lb),
                    b_centroid_in_a=_centroid_inside(cb.centroid, a, la),
                )
            )

    return OverlapGraph(
        components_a=tuple(comps_a[k] for k in sorted(comps_a)),
        components_b=tuple(comps_b[k] for k in sorted(comps_b)),
        edges=tuple(edges),
        spacing=tuple(spacing),
    )


def correspond(
    a: np.ndarray, b: np.ndarray, *, spacing: tuple[float, ...], strategy: MatchingStrategy
) -> Correspondence:
    return strategy(build_overlap_graph(a, b, spacing=spacing))
