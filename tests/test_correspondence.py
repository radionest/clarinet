import dataclasses

import pytest

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
    MatchGroup,
    PairStats,
)


def test_model_construct_and_frozen():
    c = Component(label=1, size=8, centroid=(2.0, 2.0, 2.0))
    assert c.size == 8
    ps = PairStats(
        a=1,
        b=2,
        inter=4,
        size_a=8,
        size_b=6,
        centroid_distance=1.5,
        a_centroid_in_b=True,
        b_centroid_in_a=False,
    )
    assert ps.inter == 4
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.size = 9  # type: ignore[misc]


def test_correspondence_equality():
    a = Correspondence(matches=(MatchGroup((1,), (1,), 10.0),), unmatched_a=(), unmatched_b=(2,))
    b = Correspondence(matches=(MatchGroup((1,), (1,), 10.0),), unmatched_a=(), unmatched_b=(2,))
    assert a == b  # frozen dataclasses get value equality for free


def _ps(inter=0, size_a=1, size_b=1, dist=0.0, a_in_b=False, b_in_a=False):
    return PairStats(
        a=1,
        b=1,
        inter=inter,
        size_a=size_a,
        size_b=size_b,
        centroid_distance=dist,
        a_centroid_in_b=a_in_b,
        b_centroid_in_a=b_in_a,
    )


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


import numpy as np  # noqa: E402

from clarinet.services.image.correspondence.graph import build_overlap_graph  # noqa: E402


def test_build_graph_two_overlapping_blobs():
    a = np.zeros((6, 6, 1), dtype=np.uint8)
    b = np.zeros((6, 6, 1), dtype=np.uint8)
    a[1:4, 1:4, 0] = 1  # |a|=9
    b[2:5, 2:5, 0] = 1  # |b|=9, overlap = [2:4,2:4] = 4 voxels
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
    a[3, 1:4, 0] = 1  # left bracket "[" -- centroid near the open middle
    b = np.ones((5, 5, 1), dtype=np.uint8)
    g = build_overlap_graph(a, b, spacing=(1.0, 1.0, 1.0))
    e = next(x for x in g.edges if x.a == 1)
    assert e.a_centroid_in_b is True  # b fills the volume
    assert e.b_centroid_in_a is False  # b's centroid (2,2) is not on the bracket
