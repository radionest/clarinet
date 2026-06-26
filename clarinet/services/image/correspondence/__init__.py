"""Pluggable component-correspondence engine (measure x matching x operation)."""

from clarinet.services.image.correspondence.graph import build_overlap_graph, correspond
from clarinet.services.image.correspondence.measures import (
    AbsoluteOverlap,
    CentroidContainment,
    CentroidProximity,
    Coverage,
    Dice,
    IoU,
    OverlapCoefficient,
    Weighted,
)
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
    "AbsoluteOverlap",
    "CentroidContainment",
    "CentroidProximity",
    "Component",
    "Correspondence",
    "Coverage",
    "Dice",
    "IoU",
    "KeepPlan",
    "MatchGroup",
    "MatchingStrategy",
    "Measure",
    "OverlapCoefficient",
    "OverlapGraph",
    "PairStats",
    "SetOperation",
    "Weighted",
    "build_overlap_graph",
    "correspond",
]
