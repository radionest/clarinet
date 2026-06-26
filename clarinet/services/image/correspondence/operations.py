"""Set operations (Correspondence -> KeepPlan) + the one mechanical painter."""

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
    """Paint a KeepPlan onto an output mask.

    Starts from a blank ``np.zeros_like(a)`` (or a copy of ``base`` to overlay). Each
    ``(src_label, out_value)`` entry paints ``source == src_label`` with, in
    precedence: ``out_value`` when non-zero (explicit target); else a fresh sequential
    label when ``relabel`` (symmetric-difference style); else the original
    ``src_label`` (identity-preserving). ``from_a`` entries read from ``a``, ``from_b``
    from ``b``. Returns uint8 when ``a`` is uint8.
    """
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
