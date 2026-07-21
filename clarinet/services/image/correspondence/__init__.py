"""Pluggable component-correspondence engine (measure x matching x operation)."""

from clarinet.services.image.correspondence.graph import build_overlap_graph, correspond
from clarinet.services.image.correspondence.matching import (
    GreedyArgmax,
    ThresholdMatch,
    strategy_from_thresholds,
)
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
from clarinet.services.image.correspondence.operations import (
    AppendMerge,
    DeleteMatched,
    Difference,
    Intersection,
    SymmetricDifference,
    render,
)

__all__ = [
    "AbsoluteOverlap",
    "AppendMerge",
    "CentroidContainment",
    "CentroidProximity",
    "Component",
    "Correspondence",
    "Coverage",
    "DeleteMatched",
    "Dice",
    "Difference",
    "GreedyArgmax",
    "Intersection",
    "IoU",
    "KeepPlan",
    "MatchGroup",
    "MatchingStrategy",
    "Measure",
    "OverlapCoefficient",
    "OverlapGraph",
    "PairStats",
    "SetOperation",
    "SymmetricDifference",
    "ThresholdMatch",
    "Weighted",
    "build_overlap_graph",
    "correspond",
    "render",
    "strategy_from_thresholds",
]
