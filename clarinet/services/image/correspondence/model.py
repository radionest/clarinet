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
