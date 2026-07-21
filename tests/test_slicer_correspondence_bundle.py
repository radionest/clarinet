"""Unit tests for build_correspondence_bundle() -- CI-only, no Slicer required.

The bundle flattens the clarinet.services.image.correspondence package into a
single string of source text so it can be injected into a Slicer script and
exec'd inside Slicer's bundled Python (no clarinet install there). These
tests exercise the flattening logic directly: the output must be free of
``from clarinet`` imports and contain at most one ``from __future__ import
annotations`` line, and exec'ing it in a fresh namespace must produce a
working ``build_overlap_graph`` equivalent to the original package function.
"""

import ast
import re

import numpy as np

from clarinet.services.slicer.correspondence_bundle import build_correspondence_bundle

_CLARINET_TOKEN_RE = re.compile(r"\bclarinet\b")


def test_bundle_has_no_clarinet_imports() -> None:
    bundle = build_correspondence_bundle()
    for line in bundle.splitlines():
        assert "from clarinet" not in line
        # Broader than the prefix check above: a bare `import clarinet.x`
        # (no leading "from") would evade it but still leave clarinet
        # unimportable inside Slicer's bundled Python. Deliberately strict:
        # a comment or docstring merely mentioning clarinet also fails --
        # keep the correspondence sources free of the token.
        assert not _CLARINET_TOKEN_RE.search(line.strip())


def test_bundle_has_at_most_one_future_import() -> None:
    bundle = build_correspondence_bundle()
    future_imports = [
        line
        for line in bundle.splitlines()
        if line.strip().startswith("from __future__ import annotations")
    ]
    assert len(future_imports) <= 1


def test_bundle_parses_under_python_39_grammar() -> None:
    """Tripwire for 3.10+ syntax drifting into the correspondence package.

    Slicer's bundled Python is 3.9, but CI execs the bundle under the venv's
    much newer interpreter -- a ``match`` statement or PEP 695 alias added to
    ``correspondence/*.py`` would pass CI and break every opted-in Slicer
    script at exec time. ``feature_version`` is best-effort and syntax-level
    only: annotation-position unions are already safe (the bundle keeps
    ``from __future__ import annotations``), while runtime-only 3.10isms
    (e.g. a module-level ``Alias = int | None`` assignment) are covered
    solely by the live-Slicer integration test.
    """
    ast.parse(build_correspondence_bundle(), feature_version=(3, 9))


def test_bundle_execs_and_builds_overlap_graph() -> None:
    bundle = build_correspondence_bundle()
    ns: dict = {"__name__": "_bundle"}
    exec(bundle, ns)
    assert "build_overlap_graph" in ns
    # The full engine surface must survive flattening, not just the graph
    # builder: measures, matching strategies, and the render/correspond API.
    for name in ("Dice", "GreedyArgmax", "correspond", "render"):
        assert name in ns

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


def test_bundle_exposes_strategy_derivation() -> None:
    """The scalars->strategy derivation must ship inside the bundle (D7)."""
    ns: dict = {"__name__": "_bundle"}
    exec(build_correspondence_bundle(), ns)
    assert callable(ns["strategy_from_thresholds"])


def test_bundle_derivation_matches_native() -> None:
    """Identical scalars -> identical strategies, bundled and native alike."""
    from clarinet.services.image.correspondence.matching import strategy_from_thresholds

    ns: dict = {"__name__": "_bundle"}
    exec(build_correspondence_bundle(), ns)
    cases = [
        {},
        {"max_overlap": 3},
        {"max_overlap_ratio": 0.25},
        {"max_overlap": 3, "max_overlap_ratio": 0.25},  # ratio takes precedence
    ]
    for kwargs in cases:
        bundled = ns["strategy_from_thresholds"](**kwargs)
        native = strategy_from_thresholds(**kwargs)
        assert type(bundled.measure).__name__ == type(native.measure).__name__
        assert bundled.min_score == native.min_score
        assert getattr(bundled.measure, "side", None) == getattr(native.measure, "side", None)
