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
    """Per-label voxel count and voxel-index centroid in a single pass.

    O(ndim * N) via weighted bincount, independent of the label count -- the
    previous per-label np.argwhere scan was O(L * N).
    """
    flat = labelmap.ravel()
    sizes = np.bincount(flat)
    n_labels = int(sizes.shape[0])
    ndim = labelmap.ndim
    coord_sums = np.zeros((ndim, n_labels), dtype=float)
    for ax in range(ndim):
        shape = [1] * ndim
        shape[ax] = labelmap.shape[ax]
        coord = np.broadcast_to(
            np.arange(labelmap.shape[ax]).reshape(shape), labelmap.shape
        ).ravel()
        coord_sums[ax] = np.bincount(flat, weights=coord, minlength=n_labels)
    out: dict[int, Component] = {}
    for lbl in range(1, n_labels):
        size = int(sizes[lbl])
        if size == 0:
            continue
        out[lbl] = Component(
            label=lbl,
            size=size,
            centroid=tuple(float(coord_sums[ax, lbl] / size) for ax in range(ndim)),
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
    """Build the bipartite overlap graph between two labelmaps.

    Emits one edge per OVERLAPPING (a-label, b-label) pair (inter > 0); disjoint
    components produce no edge. Pair intersections are counted in a single pass via a
    contingency key ``a*base + b`` (base = max b-label + 1, int64 to avoid uint8
    overflow). ``centroid_distance`` is physical (mm): the voxel-index centroid delta
    is scaled by ``spacing`` before the L2 norm. Containment flags index the other
    labelmap at each rounded, bounds-checked centroid.
    """
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
