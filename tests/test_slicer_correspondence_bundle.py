"""Unit tests for build_correspondence_bundle() -- CI-only, no Slicer required.

The bundle flattens the clarinet.services.image.correspondence package into a
single string of source text so it can be injected into a Slicer script and
exec'd inside Slicer's bundled Python (no clarinet install there). These
tests exercise the flattening logic directly: the output must be free of
``from clarinet`` imports and contain at most one ``from __future__ import
annotations`` line, and exec'ing it in a fresh namespace must produce a
working ``build_overlap_graph`` equivalent to the original package function.
"""

import numpy as np

from clarinet.services.slicer.correspondence_bundle import build_correspondence_bundle


def test_bundle_has_no_clarinet_imports() -> None:
    bundle = build_correspondence_bundle()
    for line in bundle.splitlines():
        assert "from clarinet" not in line


def test_bundle_has_at_most_one_future_import() -> None:
    bundle = build_correspondence_bundle()
    future_imports = [
        line
        for line in bundle.splitlines()
        if line.strip().startswith("from __future__ import annotations")
    ]
    assert len(future_imports) <= 1


def test_bundle_execs_and_builds_overlap_graph() -> None:
    bundle = build_correspondence_bundle()
    ns: dict = {"__name__": "_bundle"}
    exec(bundle, ns)
    assert "build_overlap_graph" in ns

    # Two 3-D labelmaps sharing exactly one voxel on exactly one (a-label,
    # b-label) pair: a's label 1 and b's label 1 both occupy (0, 0, 0).
    # a's label 2 and b's label 2 are disjoint components -- no edge.
    a = np.zeros((2, 2, 2), dtype=np.uint8)
    a[0, 0, 0] = 1
    a[1, 1, 1] = 2

    b = np.zeros((2, 2, 2), dtype=np.uint8)
    b[0, 0, 0] = 1
    b[0, 0, 1] = 2

    graph = ns["build_overlap_graph"](a, b, spacing=(1, 1, 1))
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.a == 1
    assert edge.b == 1
    assert edge.inter == 1
