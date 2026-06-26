import dataclasses

import pytest

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
