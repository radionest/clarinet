"""Matching strategies -- pure functions OverlapGraph -> Correspondence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from clarinet.services.image.correspondence.measures import AbsoluteOverlap, Coverage
from clarinet.services.image.correspondence.model import (
    Correspondence,
    MatchGroup,
    Measure,
    OverlapGraph,
)


def _unmatched(
    graph: OverlapGraph, matched_a: set[int], matched_b: set[int]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
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


def strategy_from_thresholds(
    max_overlap: int = 0, max_overlap_ratio: float | None = None
) -> ThresholdMatch:
    """Derive the difference-matching strategy from legacy scalar thresholds.

    Ratio takes precedence: with ``max_overlap_ratio`` set, a label is matched
    (removed from a difference) iff ``inter / size_a >= max_overlap_ratio``
    (``Coverage("a")``), and ``max_overlap`` is ignored. Otherwise
    ``AbsoluteOverlap`` with ``min_score = max_overlap + 1`` keeps labels whose
    largest single-pair overlap is at most ``max_overlap``.
    """
    if max_overlap_ratio is not None:
        return ThresholdMatch(Coverage("a"), min_score=max_overlap_ratio)
    return ThresholdMatch(AbsoluteOverlap(), min_score=float(max_overlap + 1))
