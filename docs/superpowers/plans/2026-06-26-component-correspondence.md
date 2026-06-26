# Component Correspondence Subsystem (PR1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable, unit-testable component-correspondence engine under `clarinet/services/image/correspondence/` and rewire `Segmentation`'s set operations onto it, backward-compatibly.

**Architecture:** Three pure stages over plain data — `build_overlap_graph` (arrays → `OverlapGraph` of raw pair stats) → a `MatchingStrategy` (measure × policy → `Correspondence`) → a `SetOperation` (`Correspondence` → `KeepPlan`) rendered to a mask. Strategies are `frozen` dataclasses implementing `Protocol`s; only two functions touch numpy arrays, everything else is pure and testable on three-line literals.

**Tech Stack:** Python 3.12 (PEP 695), numpy, scipy (lazy, follow-up matchers only), pytest, mypy, ruff — all via `uv run`.

## Global Constraints

- **No new dependencies.** numpy/scipy/skimage already present. `scipy` only via lazy import inside a function body (mirror `Segmentation.reindex_to`).
- **The `correspondence/` package depends only on `numpy` + stdlib and MUST NOT import `image.py` or `segmentation.py`** (keeps the pure core importable/vendorable by the Slicer path in a later PR).
- **Segmentation dtype is `np.uint8`**; `render` returns `np.zeros_like(a)` (uint8) or a copy of `base`.
- **Backward compatibility:** existing `intersection`/`difference` *working* paths produce identical results when no `strategy=` is passed. `symmetric_difference` default semantics intentionally improve (clean component-level); its tests are updated. `difference(max_overlap_ratio=...)` is *fixed*, not preserved (was inert dead code).
- **Match boundary convention:** matched iff `score >= min_score` (uniform).
- **Run tests** with `./scripts/run_tests.sh -k <pattern> -q`; never pipe (`| tail`/`| tee`). For full runs redirect: `timeout 120 make test-fast > /tmp/test-overlap-coef.txt 2>&1`.
- **Quality gate per task:** `make check` (ruff format + ruff lint + mypy) must pass before commit.
- **Commits:** Conventional Commits; no `Co-Authored-By` trailer.
- **Worktree:** already inside `overlap-coef-matching` on branch `worktree-overlap-coef-matching`. Do not switch branches.
- **Spec:** `docs/superpowers/specs/2026-06-26-component-correspondence-design.md`.

---

## File Structure

New subpackage `clarinet/services/image/correspondence/`:
- `model.py` — frozen dataclasses (`Component`, `PairStats`, `OverlapGraph`, `MatchGroup`, `Correspondence`, `KeepPlan`) + Protocols (`Measure`, `MatchingStrategy`, `SetOperation`). The shared contract.
- `measures.py` — measure strategies.
- `graph.py` — `build_overlap_graph` (numpy builder) + `correspond` (entry point).
- `matching.py` — matcher strategies (`ThresholdMatch`, `GreedyArgmax`).
- `operations.py` — `SetOperation`s + `render` painter.
- `__init__.py` — public API re-exports (built up per task).

Modified:
- `clarinet/services/image/segmentation.py` — `intersection`/`difference`/`symmetric_difference`/`append` become thin adapters with optional `strategy=`.
- `docs/image-service.md` — document strategies + the symdiff change.

Tests:
- `tests/test_correspondence.py` — new; pure layers (measures, graph, matchers, operations, render).
- `tests/test_image.py` — extended; Segmentation adapters + backward compat.

**Out of scope for this plan (separate plans):** Slicer `subtract_segmentations` reuse (clarinet PR2); `nir_liver` call-sites (separate repo); follow-up matchers `Hungarian`/`ConnectedClusters` (trivially addable later).

---

### Task 1: Data model + Protocols

**Files:**
- Create: `clarinet/services/image/correspondence/__init__.py`
- Create: `clarinet/services/image/correspondence/model.py`
- Test: `tests/test_correspondence.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Component(label:int, size:int, centroid:tuple[float,...])`; `PairStats(a:int,b:int,inter:int,size_a:int,size_b:int,centroid_distance:float,a_centroid_in_b:bool,b_centroid_in_a:bool)`; `OverlapGraph(components_a:tuple[Component,...],components_b:tuple[Component,...],edges:tuple[PairStats,...],spacing:tuple[float,...])`; `MatchGroup(a_labels:tuple[int,...],b_labels:tuple[int,...],score:float)`; `Correspondence(matches:tuple[MatchGroup,...],unmatched_a:tuple[int,...],unmatched_b:tuple[int,...])`; `KeepPlan(from_a:tuple[tuple[int,int],...],from_b:tuple[tuple[int,int],...])`; Protocols `Measure`, `MatchingStrategy`, `SetOperation`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_correspondence.py
import dataclasses
import pytest
from clarinet.services.image.correspondence.model import (
    Component, PairStats, OverlapGraph, MatchGroup, Correspondence, KeepPlan,
)


def test_model_construct_and_frozen():
    c = Component(label=1, size=8, centroid=(2.0, 2.0, 2.0))
    assert c.size == 8
    ps = PairStats(a=1, b=2, inter=4, size_a=8, size_b=6,
                   centroid_distance=1.5, a_centroid_in_b=True, b_centroid_in_a=False)
    assert ps.inter == 4
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.size = 9  # type: ignore[misc]


def test_correspondence_equality():
    a = Correspondence(matches=(MatchGroup((1,), (1,), 10.0),), unmatched_a=(), unmatched_b=(2,))
    b = Correspondence(matches=(MatchGroup((1,), (1,), 10.0),), unmatched_a=(), unmatched_b=(2,))
    assert a == b  # frozen dataclasses get value equality for free
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -q`
Expected: FAIL — `ModuleNotFoundError: clarinet.services.image.correspondence`.

- [ ] **Step 3: Write minimal implementation**

```python
# clarinet/services/image/correspondence/model.py
"""Plain-data contract shared by the correspondence layers (no numpy/Segmentation)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Component:
    label: int
    size: int  # voxel count
    centroid: tuple[float, ...]  # voxel-index center of mass


@dataclass(frozen=True)
class PairStats:
    a: int
    b: int
    inter: int  # |a ∩ b|, voxels
    size_a: int
    size_b: int
    centroid_distance: float  # physical (mm)
    a_centroid_in_b: bool
    b_centroid_in_a: bool


@dataclass(frozen=True)
class OverlapGraph:
    components_a: tuple[Component, ...]
    components_b: tuple[Component, ...]
    edges: tuple[PairStats, ...]
    spacing: tuple[float, ...]


@dataclass(frozen=True)
class MatchGroup:
    a_labels: tuple[int, ...]
    b_labels: tuple[int, ...]
    score: float


@dataclass(frozen=True)
class Correspondence:
    matches: tuple[MatchGroup, ...]
    unmatched_a: tuple[int, ...]
    unmatched_b: tuple[int, ...]


@dataclass(frozen=True)
class KeepPlan:
    from_a: tuple[tuple[int, int], ...]  # (source label in A, out value; 0 = auto)
    from_b: tuple[tuple[int, int], ...]


class Measure(Protocol):
    def __call__(self, e: PairStats) -> float: ...  # higher = better match


class MatchingStrategy(Protocol):
    def __call__(self, graph: OverlapGraph) -> Correspondence: ...


class SetOperation(Protocol):
    def __call__(self, corr: Correspondence) -> KeepPlan: ...
```

```python
# clarinet/services/image/correspondence/__init__.py
"""Pluggable component-correspondence engine (measure × matching × operation)."""

from clarinet.services.image.correspondence.model import (
    Component,
    Correspondence,
    KeepPlan,
    MatchGroup,
    MatchingStrategy,
    Measure,
    OverlapGraph,
    PairStats,
    SetOperation,
)

__all__ = [
    "Component",
    "Correspondence",
    "KeepPlan",
    "MatchGroup",
    "MatchingStrategy",
    "Measure",
    "OverlapGraph",
    "PairStats",
    "SetOperation",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Quality gate + commit**

Run: `make check`
```bash
git add clarinet/services/image/correspondence/ tests/test_correspondence.py
git commit -m "feat(image): correspondence data model + strategy protocols"
```

---

### Task 2: Measures

**Files:**
- Create: `clarinet/services/image/correspondence/measures.py`
- Modify: `clarinet/services/image/correspondence/__init__.py`
- Test: `tests/test_correspondence.py`

**Interfaces:**
- Consumes: `PairStats`, `Measure` from `model`.
- Produces: `AbsoluteOverlap()`, `IoU()`, `Dice()`, `Coverage(side="a"|"b")`, `OverlapCoefficient()`, `CentroidProximity(d_max_mm:float)`, `CentroidContainment()`, `Weighted(terms:tuple[tuple[float,Measure],...])` — each callable `(PairStats) -> float`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_correspondence.py  (append)
from clarinet.services.image.correspondence.measures import (
    AbsoluteOverlap, IoU, Dice, Coverage, OverlapCoefficient,
    CentroidProximity, CentroidContainment, Weighted,
)


def _ps(inter=0, size_a=1, size_b=1, dist=0.0, a_in_b=False, b_in_a=False):
    return PairStats(a=1, b=1, inter=inter, size_a=size_a, size_b=size_b,
                     centroid_distance=dist, a_centroid_in_b=a_in_b, b_centroid_in_a=b_in_a)


def test_region_measures():
    e = _ps(inter=4, size_a=8, size_b=8)
    assert AbsoluteOverlap()(e) == 4.0
    assert IoU()(e) == pytest.approx(4 / 12)
    assert Dice()(e) == pytest.approx(2 * 4 / 16)
    assert Coverage("a")(e) == pytest.approx(0.5)
    # small-inside-large: overlap-coefficient is robust where Coverage("a") is not
    small = _ps(inter=6, size_a=6, size_b=600)
    assert OverlapCoefficient()(small) == pytest.approx(1.0)
    assert IoU()(small) == pytest.approx(6 / 600)


def test_measure_zero_guards():
    assert IoU()(_ps(inter=0, size_a=0, size_b=0)) == 0.0
    assert OverlapCoefficient()(_ps(inter=0, size_a=0, size_b=5)) == 0.0


def test_centroid_measures_and_composite():
    assert CentroidProximity(d_max_mm=10.0)(_ps(dist=0.0)) == 1.0
    assert CentroidProximity(d_max_mm=10.0)(_ps(dist=10.0)) == 0.0
    assert CentroidProximity(d_max_mm=10.0)(_ps(dist=99.0)) == 0.0  # clamped
    assert CentroidContainment()(_ps(a_in_b=True)) == 1.0
    w = Weighted(terms=((0.5, IoU()), (0.5, CentroidProximity(10.0))))
    assert w(_ps(inter=4, size_a=8, size_b=8, dist=0.0)) == pytest.approx(0.5 * (4 / 12) + 0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -q`
Expected: FAIL — `ModuleNotFoundError: ...correspondence.measures`.

- [ ] **Step 3: Write minimal implementation**

```python
# clarinet/services/image/correspondence/measures.py
"""Measure strategies — pure scalar functions of a PairStats (higher = better)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from clarinet.services.image.correspondence.model import Measure, PairStats


@dataclass(frozen=True)
class AbsoluteOverlap:
    def __call__(self, e: PairStats) -> float:
        return float(e.inter)


@dataclass(frozen=True)
class IoU:
    def __call__(self, e: PairStats) -> float:
        union = e.size_a + e.size_b - e.inter
        return e.inter / union if union else 0.0


@dataclass(frozen=True)
class Dice:
    def __call__(self, e: PairStats) -> float:
        denom = e.size_a + e.size_b
        return 2 * e.inter / denom if denom else 0.0


@dataclass(frozen=True)
class Coverage:
    """|a∩b| / |side|. side='a' is the legacy min_overlap_ratio semantics."""

    side: Literal["a", "b"] = "a"

    def __call__(self, e: PairStats) -> float:
        size = e.size_a if self.side == "a" else e.size_b
        return e.inter / size if size else 0.0


@dataclass(frozen=True)
class OverlapCoefficient:
    """Szymkiewicz–Simpson: |a∩b| / min(|a|,|b|). Symmetric, size-robust."""

    def __call__(self, e: PairStats) -> float:
        m = min(e.size_a, e.size_b)
        return e.inter / m if m else 0.0


@dataclass(frozen=True)
class CentroidProximity:
    """1.0 at coincident centers, decaying to 0.0 at d_max_mm."""

    d_max_mm: float

    def __call__(self, e: PairStats) -> float:
        return max(0.0, 1.0 - e.centroid_distance / self.d_max_mm)


@dataclass(frozen=True)
class CentroidContainment:
    def __call__(self, e: PairStats) -> float:
        return 1.0 if e.a_centroid_in_b else 0.0


@dataclass(frozen=True)
class Weighted:
    terms: tuple[tuple[float, Measure], ...]

    def __call__(self, e: PairStats) -> float:
        return sum(w * m(e) for w, m in self.terms)
```

Append to `__init__.py` `__all__` and imports: `AbsoluteOverlap, IoU, Dice, Coverage, OverlapCoefficient, CentroidProximity, CentroidContainment, Weighted` from `.measures`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -q`
Expected: PASS.

- [ ] **Step 5: Quality gate + commit**

Run: `make check`
```bash
git add clarinet/services/image/correspondence/ tests/test_correspondence.py
git commit -m "feat(image): correspondence measures (IoU/Dice/coverage/overlap-coef/centroid)"
```

---

### Task 3: Overlap graph builder

**Files:**
- Create: `clarinet/services/image/correspondence/graph.py`
- Modify: `clarinet/services/image/correspondence/__init__.py`
- Test: `tests/test_correspondence.py`

**Interfaces:**
- Consumes: `Component`, `OverlapGraph`, `PairStats` from `model`.
- Produces: `build_overlap_graph(a:np.ndarray, b:np.ndarray, *, spacing:tuple[float,...]) -> OverlapGraph` — edges for overlapping pairs only (`inter > 0`); sizes from voxel counts; centroids as per-label coordinate means; `centroid_distance` physical (mm) via spacing; `*_centroid_in_*` by indexing the other labelmap at the rounded centroid.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_correspondence.py  (append)
import numpy as np
from clarinet.services.image.correspondence.graph import build_overlap_graph


def test_build_graph_two_overlapping_blobs():
    a = np.zeros((6, 6, 1), dtype=np.uint8)
    b = np.zeros((6, 6, 1), dtype=np.uint8)
    a[1:4, 1:4, 0] = 1            # |a|=9
    b[2:5, 2:5, 0] = 1            # |b|=9, overlap = [2:4,2:4] = 4 voxels
    g = build_overlap_graph(a, b, spacing=(1.0, 1.0, 1.0))
    assert len(g.edges) == 1
    e = g.edges[0]
    assert (e.a, e.b, e.inter, e.size_a, e.size_b) == (1, 1, 4, 9, 9)
    assert e.centroid_distance == pytest.approx(np.sqrt(2), abs=1e-6)


def test_build_graph_no_overlap_no_edges():
    a = np.zeros((6, 6, 1), dtype=np.uint8)
    b = np.zeros((6, 6, 1), dtype=np.uint8)
    a[0:2, 0:2, 0] = 1
    b[4:6, 4:6, 0] = 1
    g = build_overlap_graph(a, b, spacing=(1.0, 1.0, 1.0))
    assert g.edges == ()
    assert len(g.components_a) == 1 and len(g.components_b) == 1


def test_build_graph_centroid_containment_cshape():
    # C-shape whose center of mass lies OUTSIDE the component
    a = np.zeros((5, 5, 1), dtype=np.uint8)
    a[1:4, 1, 0] = 1
    a[1, 1:4, 0] = 1
    a[3, 1:4, 0] = 1              # left bracket "[" — centroid near the open middle
    b = np.ones((5, 5, 1), dtype=np.uint8)
    g = build_overlap_graph(a, b, spacing=(1.0, 1.0, 1.0))
    e = next(x for x in g.edges if x.a == 1)
    assert e.a_centroid_in_b is True          # b fills the volume
    assert e.b_centroid_in_a is False         # b's centroid (2,2) is not on the bracket
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -k build_graph -q`
Expected: FAIL — `ModuleNotFoundError: ...correspondence.graph`.

- [ ] **Step 3: Write minimal implementation**

```python
# clarinet/services/image/correspondence/graph.py
"""Array → OverlapGraph (the one impure builder) + the correspond() entry point."""

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
    """Per-label size and voxel-index centroid. O(N·L) — fine for typical label counts."""
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
    idx = tuple(int(round(c)) for c in centroid)
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
```

Append `build_overlap_graph, correspond` to `__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -k build_graph -q`
Expected: PASS.

- [ ] **Step 5: Quality gate + commit**

Run: `make check`
```bash
git add clarinet/services/image/correspondence/ tests/test_correspondence.py
git commit -m "feat(image): vectorized overlap-graph builder + correspond entry point"
```

---

### Task 4: Matchers (`ThresholdMatch`, `GreedyArgmax`)

**Files:**
- Create: `clarinet/services/image/correspondence/matching.py`
- Modify: `clarinet/services/image/correspondence/__init__.py`
- Test: `tests/test_correspondence.py`

**Interfaces:**
- Consumes: `Correspondence`, `MatchGroup`, `Measure`, `OverlapGraph` from `model`; `correspond` from `graph`.
- Produces: `ThresholdMatch(measure:Measure, min_score:float=0.0)`; `GreedyArgmax(measure:Measure, min_score:float=0.0, direction:Literal["mutual","a_to_b","b_to_a"]="mutual")`. Both `(OverlapGraph) -> Correspondence`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_correspondence.py  (append)
from clarinet.services.image.correspondence.model import Component, OverlapGraph, MatchGroup
from clarinet.services.image.correspondence.matching import ThresholdMatch, GreedyArgmax


def _comp(label, size=10, centroid=(0.0, 0.0, 0.0)):
    return Component(label=label, size=size, centroid=centroid)


def _edge(a, b, inter, dist=0.0):
    return PairStats(a=a, b=b, inter=inter, size_a=20, size_b=20,
                     centroid_distance=dist, a_centroid_in_b=False, b_centroid_in_a=False)


def _graph(a_labels, b_labels, edges):
    return OverlapGraph(
        components_a=tuple(_comp(x) for x in a_labels),
        components_b=tuple(_comp(x) for x in b_labels),
        edges=tuple(edges),
        spacing=(1.0, 1.0, 1.0),
    )


def test_greedy_argmax_resolves_1_to_n_by_overlap():
    g = _graph([1], [1, 2], [_edge(1, 1, 10), _edge(1, 2, 3)])
    corr = GreedyArgmax(AbsoluteOverlap(), direction="a_to_b")(g)
    assert corr.matches == (MatchGroup(a_labels=(1,), b_labels=(1,), score=10.0),)
    assert corr.unmatched_b == (2,)            # smaller-overlap loser kept


def test_measure_swap_changes_winner():
    g = _graph([1], [1, 2], [_edge(1, 1, 10, dist=8.0), _edge(1, 2, 3, dist=1.0)])
    corr = GreedyArgmax(CentroidProximity(d_max_mm=10.0), direction="a_to_b")(g)
    assert corr.matches[0].b_labels == (2,)    # same matcher, different measure → different winner


def test_threshold_match_keeps_clusters_and_filters():
    g = _graph([1, 2], [1], [_edge(1, 1, 10), _edge(2, 1, 1)])
    corr = ThresholdMatch(AbsoluteOverlap(), min_score=5.0)(g)
    assert corr.matches == (MatchGroup(a_labels=(1,), b_labels=(1,), score=10.0),)
    assert corr.unmatched_a == (2,)            # below threshold → unmatched


def test_unmatched_both_sides():
    g = _graph([1], [2], [])                    # 1:0 and 0:1
    corr = ThresholdMatch(AbsoluteOverlap(), min_score=1.0)(g)
    assert corr.matches == ()
    assert corr.unmatched_a == (1,) and corr.unmatched_b == (2,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -k "argmax or threshold or unmatched or winner" -q`
Expected: FAIL — `ModuleNotFoundError: ...correspondence.matching`.

- [ ] **Step 3: Write minimal implementation**

```python
# clarinet/services/image/correspondence/matching.py
"""Matching strategies — pure functions OverlapGraph → Correspondence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from clarinet.services.image.correspondence.model import (
    Correspondence,
    MatchGroup,
    Measure,
    OverlapGraph,
)


def _unmatched(graph: OverlapGraph, matched_a: set[int], matched_b: set[int]) -> tuple[
    tuple[int, ...], tuple[int, ...]
]:
    ua = tuple(c.label for c in graph.components_a if c.label not in matched_a)
    ub = tuple(c.label for c in graph.components_b if c.label not in matched_b)
    return ua, ub


def _connected_groups(edges: list[tuple[int, int]]) -> list[tuple[set[int], set[int]]]:
    """Union-find over a-nodes and b-nodes (namespaced to avoid label collision)."""
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(x: tuple[str, int]) -> tuple[str, int]:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(("a", a))] = find(("b", b))

    clusters: dict[tuple[str, int], tuple[set[int], set[int]]] = {}
    for a, b in edges:
        root = find(("a", a))
        clusters.setdefault(root, (set(), set()))
        clusters[root][0].add(a)
        clusters[root][1].add(b)
    return list(clusters.values())


@dataclass(frozen=True)
class ThresholdMatch:
    measure: Measure
    min_score: float = 0.0

    def __call__(self, graph: OverlapGraph) -> Correspondence:
        scored = {(e.a, e.b): self.measure(e) for e in graph.edges}
        kept = {pair: s for pair, s in scored.items() if s >= self.min_score}
        groups = _connected_groups(list(kept))
        matches = tuple(
            MatchGroup(
                a_labels=tuple(sorted(a_set)),
                b_labels=tuple(sorted(b_set)),
                score=max(kept[(a, b)] for a in a_set for b in b_set if (a, b) in kept),
            )
            for a_set, b_set in groups
        )
        matched_a = {a for m in matches for a in m.a_labels}
        matched_b = {b for m in matches for b in m.b_labels}
        ua, ub = _unmatched(graph, matched_a, matched_b)
        return Correspondence(matches=matches, unmatched_a=ua, unmatched_b=ub)


@dataclass(frozen=True)
class GreedyArgmax:
    measure: Measure
    min_score: float = 0.0
    direction: Literal["mutual", "a_to_b", "b_to_a"] = "mutual"

    def __call__(self, graph: OverlapGraph) -> Correspondence:
        scored = sorted(
            ((s, e) for e in graph.edges if (s := self.measure(e)) >= self.min_score),
            key=lambda se: (-se[0], se[1].a, se[1].b),  # deterministic tie-break
        )
        used_a: set[int] = set()
        used_b: set[int] = set()
        matches: list[MatchGroup] = []
        for score, e in scored:
            if self.direction in ("mutual", "a_to_b") and e.a in used_a:
                continue
            if self.direction in ("mutual", "b_to_a") and e.b in used_b:
                continue
            matches.append(MatchGroup(a_labels=(e.a,), b_labels=(e.b,), score=score))
            used_a.add(e.a)
            used_b.add(e.b)
        matched_a = {m.a_labels[0] for m in matches}
        matched_b = {m.b_labels[0] for m in matches}
        ua, ub = _unmatched(graph, matched_a, matched_b)
        return Correspondence(matches=tuple(matches), unmatched_a=ua, unmatched_b=ub)
```

Append `ThresholdMatch, GreedyArgmax` to `__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -q`
Expected: PASS (all correspondence tests).

- [ ] **Step 5: Quality gate + commit**

Run: `make check`
```bash
git add clarinet/services/image/correspondence/ tests/test_correspondence.py
git commit -m "feat(image): threshold + greedy-argmax matching strategies"
```

---

### Task 5: Operations + render

**Files:**
- Create: `clarinet/services/image/correspondence/operations.py`
- Modify: `clarinet/services/image/correspondence/__init__.py`
- Test: `tests/test_correspondence.py`

**Interfaces:**
- Consumes: `Correspondence`, `KeepPlan` from `model`.
- Produces: `SymmetricDifference()`, `Difference()`, `Intersection()`, `AppendMerge()`, `DeleteMatched(side="a"|"b")` — each `(Correspondence) -> KeepPlan`; `render(plan:KeepPlan, a:np.ndarray, b:np.ndarray, *, base:np.ndarray|None=None, relabel:bool=True) -> np.ndarray`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_correspondence.py  (append)
from clarinet.services.image.correspondence.operations import (
    SymmetricDifference, Difference, Intersection, AppendMerge, DeleteMatched, render,
)


def _corr(matches=(), ua=(), ub=()):
    return Correspondence(matches=tuple(matches), unmatched_a=tuple(ua), unmatched_b=tuple(ub))


def test_operations_plans():
    c = _corr(matches=[MatchGroup((1,), (1,), 9.0)], ua=(2,), ub=(3,))
    assert SymmetricDifference()(c) == KeepPlan(from_a=((2, 0),), from_b=((3, 0),))
    assert Difference()(c) == KeepPlan(from_a=((2, 0),), from_b=())
    assert Intersection()(c) == KeepPlan(from_a=((1, 0),), from_b=())
    assert DeleteMatched(side="b")(c) == KeepPlan(from_a=(), from_b=((3, 0),))


def test_append_merge_targets_winner_label():
    c = _corr(matches=[MatchGroup((5,), (2, 3), 8.0)])
    assert AppendMerge()(c) == KeepPlan(from_a=(), from_b=((2, 5), (3, 5)))


def test_render_relabel_and_overlay():
    a = np.zeros((1, 5, 1), dtype=np.uint8)
    b = np.zeros((1, 5, 1), dtype=np.uint8)
    a[0, 0, 0] = 7            # source label 7 in A
    b[0, 4, 0] = 3            # source label 3 in B
    # symmetric-difference style: relabel both to sequential, blank canvas
    out = render(KeepPlan(from_a=((7, 0),), from_b=((3, 0),)), a, b, relabel=True)
    assert sorted(int(v) for v in np.unique(out)) == [0, 1, 2]
    # append style: overlay B onto a copy of A with an explicit target value
    out2 = render(KeepPlan(from_a=(), from_b=((3, 7),)), a, b, base=a, relabel=False)
    assert int(out2[0, 0, 0]) == 7 and int(out2[0, 4, 0]) == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -k "operations or append_merge or render" -q`
Expected: FAIL — `ModuleNotFoundError: ...correspondence.operations`.

- [ ] **Step 3: Write minimal implementation**

```python
# clarinet/services/image/correspondence/operations.py
"""Set operations (Correspondence → KeepPlan) + the one mechanical painter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from clarinet.services.image.correspondence.model import Correspondence, KeepPlan


@dataclass(frozen=True)
class SymmetricDifference:
    def __call__(self, corr: Correspondence) -> KeepPlan:
        return KeepPlan(
            from_a=tuple((lbl, 0) for lbl in corr.unmatched_a),
            from_b=tuple((lbl, 0) for lbl in corr.unmatched_b),
        )


@dataclass(frozen=True)
class Difference:
    def __call__(self, corr: Correspondence) -> KeepPlan:
        return KeepPlan(from_a=tuple((lbl, 0) for lbl in corr.unmatched_a), from_b=())


@dataclass(frozen=True)
class Intersection:
    def __call__(self, corr: Correspondence) -> KeepPlan:
        matched_a = sorted({a for m in corr.matches for a in m.a_labels})
        return KeepPlan(from_a=tuple((lbl, 0) for lbl in matched_a), from_b=())


@dataclass(frozen=True)
class AppendMerge:
    def __call__(self, corr: Correspondence) -> KeepPlan:
        out = [(b, m.a_labels[0]) for m in corr.matches for b in m.b_labels]
        return KeepPlan(from_a=(), from_b=tuple(out))


@dataclass(frozen=True)
class DeleteMatched:
    side: Literal["a", "b"] = "b"

    def __call__(self, corr: Correspondence) -> KeepPlan:
        if self.side == "a":
            return KeepPlan(from_a=tuple((lbl, 0) for lbl in corr.unmatched_a), from_b=())
        return KeepPlan(from_a=(), from_b=tuple((lbl, 0) for lbl in corr.unmatched_b))


def render(
    plan: KeepPlan,
    a: np.ndarray,
    b: np.ndarray,
    *,
    base: np.ndarray | None = None,
    relabel: bool = True,
) -> np.ndarray:
    out = np.zeros_like(a) if base is None else base.copy()
    next_label = int(out.max()) + 1
    for source, entries in ((a, plan.from_a), (b, plan.from_b)):
        for src_label, out_value in entries:
            if out_value != 0:
                value = out_value
            elif relabel:
                value = next_label
                next_label += 1
            else:
                value = int(src_label)
            out[source == src_label] = value
    return out
```

Append the five operations + `render` to `__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/run_tests.sh tests/test_correspondence.py -q`
Expected: PASS.

- [ ] **Step 5: Quality gate + commit**

Run: `make check`
```bash
git add clarinet/services/image/correspondence/ tests/test_correspondence.py
git commit -m "feat(image): set operations + render painter over Correspondence"
```

---

### Task 6: `Segmentation` adapters + backward compatibility

**Files:**
- Modify: `clarinet/services/image/segmentation.py` — `intersection` (326-361), `difference` (386-425), `symmetric_difference` (427-464)
- Test: `tests/test_image.py`

**Interfaces:**
- Consumes: `correspond`, `ThresholdMatch`, `AbsoluteOverlap`, `Coverage`, `Intersection`, `Difference`, `SymmetricDifference`, `render` from `clarinet.services.image.correspondence`.
- Produces: same public method signatures plus a new keyword-only `strategy: MatchingStrategy | None = None`. Default strategy reproduces legacy *working* behavior; explicit `strategy=` overrides.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_image.py  (append within the Segmentation test class/module)
import numpy as np
from clarinet.services.image.segmentation import Segmentation
from clarinet.services.image.correspondence import GreedyArgmax, AbsoluteOverlap


def _seg(arr):
    s = Segmentation(autolabel=True)
    s.img = arr.astype(np.uint8)
    return s


def test_intersection_backward_compatible():
    a = np.zeros((8, 8, 1), dtype=np.uint8)
    b = np.zeros((8, 8, 1), dtype=np.uint8)
    a[1:4, 1:4, 0] = 1
    b[2:3, 2:3, 0] = 1                       # 1-voxel overlap
    seg_a, seg_b = _seg(a), _seg(b)
    assert not seg_a.intersection(seg_b, min_overlap=1).is_empty
    assert seg_a.intersection(seg_b, min_overlap=5).is_empty


def test_strategy_overrides_resolve_1_to_n():
    a = np.zeros((4, 10, 1), dtype=np.uint8)
    b = np.zeros((4, 10, 1), dtype=np.uint8)
    a[1:3, 1:9, 0] = 1                       # one wide A component
    b[1:3, 1:5, 0] = 1                       # B1 — larger overlap
    b[1:3, 7:8, 0] = 1                       # B2 — smaller overlap (separate component)
    seg_a, seg_b = _seg(a), _seg(b)
    out = seg_a.difference(seg_b, strategy=GreedyArgmax(AbsoluteOverlap(), direction="a_to_b"))
    # A matched its larger-overlap partner and is dropped; the result is empty of A
    assert out.is_empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/run_tests.sh tests/test_image.py -k "backward_compatible or resolve_1_to_n" -q`
Expected: FAIL — `intersection() got an unexpected keyword argument 'strategy'` (and the override test errors).

- [ ] **Step 3: Write minimal implementation**

Add imports near the top of `segmentation.py`:

```python
from clarinet.services.image.correspondence import (
    AbsoluteOverlap,
    Coverage,
    Difference as _DifferenceOp,
    Intersection as _IntersectionOp,
    MatchingStrategy,
    SymmetricDifference as _SymmetricDifferenceOp,
    ThresholdMatch,
    correspond,
    render,
)
```

Replace the bodies of `intersection`, `difference`, `symmetric_difference`:

```python
    def intersection(
        self,
        other: Self,
        *,
        strategy: MatchingStrategy | None = None,
        min_overlap: int = 1,
        min_overlap_ratio: float | None = None,
        resample: bool = False,
    ) -> Segmentation:
        if other.img.size == 1:
            return Segmentation(template=self)
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        strategy = strategy or self._threshold(min_overlap, min_overlap_ratio)
        corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
        out = Segmentation(autolabel=False, template=self)
        out.img = render(_IntersectionOp()(corr), self.img, other.img, relabel=False)
        return out

    def difference(
        self,
        other: Self,
        *,
        strategy: MatchingStrategy | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
        resample: bool = False,
    ) -> Segmentation:
        if other.img.size == 1:
            return Segmentation(template=self, copy_data=True)
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        if strategy is None:
            if max_overlap_ratio is not None:
                strategy = ThresholdMatch(Coverage("a"), min_score=max_overlap_ratio)
            else:
                strategy = ThresholdMatch(AbsoluteOverlap(), min_score=float(max_overlap + 1))
        corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
        out = Segmentation(autolabel=False, template=self)
        out.img = render(_DifferenceOp()(corr), self.img, other.img, relabel=False)
        return out

    def symmetric_difference(
        self,
        other: Self,
        *,
        strategy: MatchingStrategy | None = None,
        min_overlap: int = 1,
        min_overlap_ratio: float | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
        resample: bool = False,
    ) -> Segmentation:
        if max_overlap != 0 or max_overlap_ratio is not None:
            logger.warning(
                "Segmentation.symmetric_difference: max_overlap/max_overlap_ratio are ignored "
                "in the correspondence model; use min_overlap/min_overlap_ratio or strategy=."
            )
        if other.img.size == 1:
            return Segmentation(template=self, copy_data=True)
        other = self._align_other(other, resample=resample)  # type: ignore[assignment]
        strategy = strategy or self._threshold(min_overlap, min_overlap_ratio)
        corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
        out = Segmentation(autolabel=False, template=self)
        out.img = render(_SymmetricDifferenceOp()(corr), self.img, other.img, relabel=True)
        return out

    @staticmethod
    def _threshold(min_overlap: int, min_overlap_ratio: float | None) -> MatchingStrategy:
        if min_overlap_ratio is not None:
            return ThresholdMatch(Coverage("a"), min_score=min_overlap_ratio)
        return ThresholdMatch(AbsoluteOverlap(), min_score=float(min_overlap))
```

Notes: `symmetric_difference` keeps `max_overlap`/`max_overlap_ratio` in its signature for compatibility but **ignores** them (single match threshold now — see spec open item; the deprecated `__xor__` passes only `min_overlap=3`, so it keeps working). In `difference`, when both `max_overlap` and `max_overlap_ratio` are given the ratio wins — the legacy two-gate AND (the inert F1 path) is not reproduced.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./scripts/run_tests.sh tests/test_image.py -k "intersection or difference or symmetric or backward or resolve" -q`
Expected: PASS. Two pre-existing tests need attention: `test_symmetric_difference` only asserts a valid `Segmentation` (stays green); `test_difference_with_ratio` exercised the old AND-gated ratio path — update it to the corrected semantics (`difference(max_overlap_ratio=r)` now drops labels with `Coverage("a") ≥ r`, the F1 fix). If any assertion checks exact symdiff voxels, update to the component-level result.

- [ ] **Step 5: Quality gate + commit**

Run: `make check`
```bash
git add clarinet/services/image/segmentation.py tests/test_image.py
git commit -m "feat(image): rewire Segmentation set ops onto the correspondence engine"
```

---

### Task 7: `append` opt-in resolution + docs

**Files:**
- Modify: `clarinet/services/image/segmentation.py` — `append` (234-262)
- Modify: `docs/image-service.md` — Named Set Operations + In-Place Operations sections
- Test: `tests/test_image.py`

**Interfaces:**
- Consumes: `GreedyArgmax`, `AppendMerge`, `correspond`, `render` from the correspondence package.
- Produces: `append(other, *, strategy=None, resample=False)` — default still raises `ValueError` on multi-label overlap (backward compatible); with `strategy=GreedyArgmax(..., direction="b_to_a")` it resolves instead.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_image.py  (append)
import pytest
from clarinet.services.image.correspondence import GreedyArgmax, AbsoluteOverlap


def _two_label_self_and_bridging_other():
    base = np.zeros((4, 10, 1), dtype=np.uint8)
    base[1:3, 1:3, 0] = 1                    # self label A
    base[1:3, 7:9, 0] = 1                    # self label B (separate)
    other = np.zeros((4, 10, 1), dtype=np.uint8)
    other[1:3, 2:8, 0] = 1                   # one ROI bridging both, more overlap on the left
    return _seg(base), _seg(other)


def test_append_multi_label_raises_by_default():
    seg, other = _two_label_self_and_bridging_other()
    with pytest.raises(ValueError, match="multiple labels"):
        seg.append(other)


def test_append_strategy_resolves_to_winner():
    seg, other = _two_label_self_and_bridging_other()
    seg.append(other, strategy=GreedyArgmax(AbsoluteOverlap(), direction="b_to_a"))
    # bridging ROI merged into exactly one existing label, none added as new
    assert set(int(v) for v in np.unique(seg.img)) <= {0, 1, 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/run_tests.sh tests/test_image.py -k append -q`
Expected: FAIL — `append() got an unexpected keyword argument 'strategy'`.

- [ ] **Step 3: Write minimal implementation**

Replace `append` body:

```python
    def append(
        self,
        other: Self | Image,
        *,
        strategy: MatchingStrategy | None = None,
        resample: bool = False,
    ) -> None:
        other = self._align_other(other, resample=resample)
        if strategy is not None:
            corr = correspond(self.img, other.img, spacing=self.spacing, strategy=strategy)
            merged = render(_AppendMergeOp()(corr), self.img, other.img,
                            base=self.img, relabel=False)
            prev, self.autolabel = self.autolabel, False
            try:
                self.img = merged          # preserve merged labels (no re-autolabel)
            finally:
                self.autolabel = prev
            return
        for region in regionprops(label(other.img)):
            coords = region.coords
            intersection = self.img[coords[:, 0], coords[:, 1], coords[:, 2]]
            unique_labels = [int(v) for v in np.unique(intersection) if v != 0]
            match unique_labels:
                case []:
                    pass
                case [label_value]:
                    self.img[coords[:, 0], coords[:, 1], coords[:, 2]] = label_value
                case [*label_values]:
                    raise ValueError(f"ROI overlaps multiple labels: {label_values}")
```

Add `AppendMerge as _AppendMergeOp` to the correspondence import block. The strategy branch toggles `autolabel` off around the assignment so `render`'s merged labels survive the `img` setter (the legacy single-label branch likewise leaves labels untouched).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./scripts/run_tests.sh tests/test_image.py -k append -q`
Expected: PASS (both default-raise and strategy-resolve).

- [ ] **Step 5: Update docs**

In `docs/image-service.md` → "Named Set Operations": add a `strategy=` column note and a row documenting that a `MatchingStrategy` (e.g. `GreedyArgmax(AbsoluteOverlap(), direction="a_to_b")`) overrides the default `ThresholdMatch`, with measures `IoU/Dice/Coverage/OverlapCoefficient/CentroidProximity`. In "In-Place Operations": document `append(strategy=...)` resolving multi-label overlaps. Add a short paragraph that `symmetric_difference` is now clean component-level (no `union`-relabel), and that `difference(max_overlap_ratio=...)` now applies the ratio (the prior inert behavior is fixed).

- [ ] **Step 6: Quality gate + commit**

Run: `make check`
```bash
git add clarinet/services/image/segmentation.py docs/image-service.md tests/test_image.py
git commit -m "feat(image): opt-in strategy for append + document correspondence API"
```

---

## Final verification

- [ ] Run the full fast suite: `timeout 180 make test-fast > /tmp/test-overlap-coef.txt 2>&1`, then read the file — all green.
- [ ] `make check` clean (format + lint + mypy).
- [ ] `Agent(subagent_type=pr-diff-reviewer)` before the first `gh pr create`.

## Self-review checklist (filled by plan author)

- **Spec coverage:** data model (T1) · measures incl. OverlapCoefficient (T2) · build_overlap_graph numpy-only + centroid containment (T3) · ThresholdMatch+GreedyArgmax+correspond (T4) · operations+render (T5) · Segmentation back-compat + strategy override + improved symdiff + fixed difference-ratio (T6) · append opt-in + docs (T7). Deferred per spec sequencing: Slicer reuse (PR2), nir_liver (separate repo), Hungarian/ConnectedClusters (follow-up).
- **Inert-threshold (F1):** eliminated — tolerance is a single `min_score` on a single measure; `difference(max_overlap_ratio=r)` → `ThresholdMatch(Coverage("a"), r)` (T6).
- **Type consistency:** strategy callables `(OverlapGraph)->Correspondence`; operations `(Correspondence)->KeepPlan`; `render(plan,a,b,*,base,relabel)`; names match across T1–T7.
