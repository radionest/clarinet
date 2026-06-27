"""Measure strategies -- pure scalar functions of a PairStats (higher = better)."""

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
    """intersect(a,b) / |side|. side='a' is the legacy min_overlap_ratio semantics."""

    side: Literal["a", "b"] = "a"

    def __call__(self, e: PairStats) -> float:
        size = e.size_a if self.side == "a" else e.size_b
        return e.inter / size if size else 0.0


@dataclass(frozen=True)
class OverlapCoefficient:
    """Szymkiewicz-Simpson: intersect(a,b) / min(|a|,|b|). Symmetric, size-robust."""

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
